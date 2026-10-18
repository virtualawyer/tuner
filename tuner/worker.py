"""The download worker.

One video at a time, on purpose: the target box is an old Chromebook that
also runs Jellyfin, and yt-dlp plus ffmpeg is enough load on its own.
"""

import errno
import os
import shutil
import threading
import time

from . import LIBRARY_CHANGED, db, disk, log, paths, retention, ytdlp

# Wait this long before re-trying a row that just failed, growing with the
# attempt count and capped so a flaky video never blocks the queue for long.
BACKOFF_STEP_SECONDS = 60
BACKOFF_MAX_SECONDS = 600

# What yt-dlp may leave behind as the finished video, best first.
MEDIA_EXTS = (".mp4", ".mkv", ".webm", ".m4v", ".mov")
SIDECAR_EXTS = (".srt", ".vtt", ".jpg", ".jpeg", ".png", ".webp")

IDLE_SECONDS = 5.0          # queue empty
NO_SPACE_SECONDS = 60.0     # waiting for room on the drive
LIBRARY_DOWN_SECONDS = 60.0  # the drive can't be read: check again in a minute
CRASH_PAUSE_SECONDS = 5.0   # after a crash, never go straight round again
SLICE_SECONDS = 1.0

# What an unreadable library looks like: a USB drive that fell off the bus
# leaves a dead mount behind (EIO), an unmounted one leaves nothing (ENOENT).
LIBRARY_GONE = {errno.EIO, errno.ENOENT, errno.ENOTCONN, errno.ESTALE, errno.ENXIO,
                errno.ENODEV, errno.EROFS}


def library_problem(cfg):
    """None when the library can be read, else why not.

    statvfs still answers on a dead mount, so free space alone can't tell;
    listing the folder can. A folder that isn't there at all is left to the
    free-space guard, which defers the queue until the drive is mounted.
    """
    root = paths.library_root(cfg)
    try:
        os.listdir(root)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "%s: %s" % (root, exc.strerror or exc)
    return None

# What the worker is doing right now, for /api/state to read. One process,
# one worker, so a module-level snapshot is enough.
_CURRENT_LOCK = threading.Lock()
CURRENT = None


def current():
    with _CURRENT_LOCK:
        return dict(CURRENT) if CURRENT else None


def set_current(snapshot):
    global CURRENT
    with _CURRENT_LOCK:
        CURRENT = dict(snapshot) if snapshot else None


def blank_progress(row):
    return {"video_id": row["video_id"], "title": row["title"],
            "percent": 0.0, "size": None, "rate": None, "eta": None,
            "stage": "starting"}


def find_media(tmp, video_id=None, exact_only=False):
    """The finished video in a scratch folder.

    The merged result is written as `<video id>.<ext>`, while the separate
    streams that fed it keep a format suffix like `.f137.mp4`. Matching the
    exact name is therefore the difference between a complete video and a
    silent one, which matters when salvaging after an error.
    """
    exact, best, best_size = None, None, -1
    try:
        names = os.listdir(tmp)
    except OSError:
        return None

    for name in names:
        stem, extension = os.path.splitext(name)
        if extension.lower() not in MEDIA_EXTS:
            continue
        path = os.path.join(tmp, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if video_id and stem == video_id:
            exact = path
        # A merge leaves the source streams behind; the muxed result is the
        # big one.
        if size > best_size:
            best, best_size = path, size

    if exact is not None:
        return exact
    return None if exact_only else best


def _last_error_line(tail):
    lines = [line.strip() for line in (tail or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if "ERROR" in line:
            return line[:120]
    return lines[-1][:120] if lines else "no output"


def suffix_after_id(name, video_id):
    """'.en.srt' from 'dQw4w9WgXcQ.en.srt', keeping the language tag."""
    if name.startswith(video_id):
        rest = name[len(video_id):]
        if rest.startswith("."):
            return rest
    return "." + name.lstrip(".")


def discard_tmp(cfg, tmp):
    """Remove a scratch folder, refusing anything outside the scratch root."""
    root = os.path.realpath(paths.tmp_root(cfg))
    target = os.path.realpath(tmp)
    if target != root and target.startswith(root + os.sep):
        shutil.rmtree(target, ignore_errors=True)
        return True
    return False


def sweep_tmp(cfg):
    """Clear scratch folders left behind by a crash or a restart."""
    root = paths.tmp_root(cfg)
    removed = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        if discard_tmp(cfg, os.path.join(root, name)):
            removed += 1
    return removed


class Worker(object):

    def __init__(self, app):
        self.app = app
        self.thread = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.process = None          # the running yt-dlp, when there is one
        self.completed = 0
        # row id -> epoch second before which it must not be claimed again.
        # In memory on purpose: a restart is a fresh start, not a penalty.
        self._backoff = {}
        self.library_down = False    # logged once when it goes, once when it's back

    # --- timing ---

    def _sleep(self, seconds):
        remaining = float(seconds)
        while remaining > 0 and not self._stop.is_set():
            if self._wake.wait(timeout=min(SLICE_SECONDS, remaining)):
                self._wake.clear()
                return
            remaining -= SLICE_SECONDS

    # --- queue ---

    def requeue_deferred(self):
        """Bring deferred rows back once there is room again."""
        rows = db.list_videos(self.app.conn, status=db.DEFERRED)
        for row in rows:
            db.set_video_status(self.app.conn, row["id"], db.QUEUED)
        if rows:
            log.info(self.app.conn,
                     "space is back, re-queued %d deferred download%s"
                     % (len(rows), "" if len(rows) == 1 else "s"))
        return len(rows)

    def blocked_ids(self):
        """Rows still serving their backoff, dropping expired entries."""
        now = time.time()
        for row_id in [k for k, until in self._backoff.items() if until <= now]:
            self._backoff.pop(row_id, None)
        return set(self._backoff)

    def back_off(self, row_id, attempts):
        delay = min(BACKOFF_STEP_SECONDS * max(1, attempts),
                    BACKOFF_MAX_SECONDS)
        self._backoff[row_id] = time.time() + delay
        return delay

    def clear_backoff(self, row_id=None):
        if row_id is None:
            self._backoff.clear()
        else:
            self._backoff.pop(row_id, None)

    def claim(self):
        """Take the oldest queued video that is not backing off, or None."""
        return db.claim_next_queued(self.app.conn,
                                    exclude_ids=self.blocked_ids())

    def defer(self, row):
        """Park a row until the drive has room."""
        free = disk.human(disk.free_bytes(self.app.cfg))
        db.set_video_status(self.app.conn, row["id"], db.DEFERRED,
                            error="waiting for disk space (%s free)" % free)
        log.warn(self.app.conn,
                 "deferred %s: only %s free, below the configured floor"
                 % (row["title"], free),
                 channel_row_id=row["channel_row_id"], video_id=row["video_id"])

    # --- work ---

    # --- finalize ---

    def _track_process(self, process):
        self.process = process

    def _progress(self, update):
        snapshot = current() or {}
        snapshot.update(update)
        set_current(snapshot)

    def on_failure(self, row, code, tail):
        """Decide what a failed download costs the row.

        A permanent refusal skips it without burning an attempt; anything
        else goes back in the queue behind a growing backoff until the
        attempt budget runs out.
        """
        conn = self.app.conn
        detail = tail or ("yt-dlp exited %s" % code)
        max_attempts = max(1, int(self.app.cfg.get("max_attempts", 3) or 1))

        permanent, reason = ytdlp.classify_failure(tail, code)
        if permanent:
            db.set_video_status(conn, row["id"], db.SKIPPED,
                                skip_reason=reason, error=detail)
            log.info(conn, "skipped %s: %s" % (row["title"], reason),
                     channel_row_id=row["channel_row_id"],
                     video_id=row["video_id"])
            return

        attempts = db.bump_attempts(conn, row["id"])
        if attempts >= max_attempts:
            db.set_video_status(conn, row["id"], db.FAILED, error=detail)
            log.warn(conn, "download failed %d times, giving up on %s"
                     % (attempts, row["title"]),
                     channel_row_id=row["channel_row_id"],
                     video_id=row["video_id"])
            return

        delay = self.back_off(row["id"], attempts)
        db.set_video_status(conn, row["id"], db.QUEUED, error=detail)
        log.warn(conn, "download failed for %s (attempt %d), retrying in %ds"
                 % (row["title"], attempts, delay),
                 channel_row_id=row["channel_row_id"],
                 video_id=row["video_id"])

    def retry(self, row_id):
        """Put a failed or missing video back in the queue, budget reset."""
        row = db.get_video(self.app.conn, row_id)
        if row is None:
            return False
        db.set_video_status(self.app.conn, row_id, db.QUEUED, attempts=0,
                            error=None, skip_reason=None)
        self.clear_backoff(row_id)
        log.info(self.app.conn, "re-queued %s" % row["title"],
                 channel_row_id=row["channel_row_id"], video_id=row["video_id"])
        self.nudge()
        return True

    def handle(self, row):
        """Download one video and file it in the library."""
        cfg, conn = self.app.cfg, self.app.conn
        channel = db.get_channel(conn, row["channel_row_id"])
        tmp = paths.tmp_dir(cfg, row["video_id"])

        try:
            code, tail = ytdlp.download(cfg, row["video_id"], tmp,
                                        on_progress=self._progress,
                                        on_start=self._track_process)
        finally:
            self.process = None

        if code != 0:
            # yt-dlp exits non-zero when an optional extra fails, and a
            # subtitle fetch getting rate limited used to throw away a video
            # that had downloaded perfectly well. If the merged file is
            # there, keep it and say what went missing.
            salvaged = find_media(tmp, row["video_id"], exact_only=True)
            if salvaged is None:
                self.on_failure(row, code, tail)
                return None
            log.warn(conn, "%s: kept the video despite an error (%s)"
                     % (row["title"], _last_error_line(tail)),
                     channel_row_id=row["channel_row_id"],
                     video_id=row["video_id"])

        media = find_media(tmp, row["video_id"])
        if media is None:
            # Exit code 0 with nothing to show for it is a failure, not a
            # success: a zero-byte library entry is worse than a retry.
            self.on_failure(row, code,
                            "yt-dlp reported success but produced no media "
                            "file\n" + (tail or ""))
            return None

        final = self.finalize(row, channel, media, tmp)
        log.info(conn, "downloaded %s (%s)"
                 % (row["title"], disk.human(os.path.getsize(final))),
                 channel_row_id=row["channel_row_id"], video_id=row["video_id"])
        LIBRARY_CHANGED.set()

        # Apply the keep rule right away, so the drive never carries more
        # than N per channel even for a moment.
        retention.prune_channel(conn, cfg, channel)
        return final

    def finalize(self, row, channel, media, tmp):
        """Move the finished files out of scratch and record them."""
        cfg, conn = self.app.cfg, self.app.conn
        plan = paths.final_paths(cfg, channel, row)

        extension = os.path.splitext(media)[1].lower()
        final = plan.video if extension == ".mp4" else plan.sidecar(extension)
        os.replace(media, final)

        for name in sorted(os.listdir(tmp)):
            if os.path.splitext(name)[1].lower() not in SIDECAR_EXTS:
                continue
            suffix = suffix_after_id(name, row["video_id"])
            os.replace(os.path.join(tmp, name), plan.sidecar(suffix))

        db.set_video_status(conn, row["id"], db.DOWNLOADED,
                            file_path=final,
                            file_size=os.path.getsize(final),
                            downloaded_at=log.now_iso(),
                            error=None, skip_reason=None)
        discard_tmp(cfg, tmp)
        return final

    def library_check(self):
        """Pause while the library can't be read, saying so once each way."""
        problem = library_problem(self.app.cfg)
        if problem and not self.library_down:
            log.error(self.app.conn, "the library can't be read (%s); downloads are paused "
                      "until it is back" % problem)
        elif not problem and self.library_down:
            log.info(self.app.conn, "the library is readable again; downloads resume")
        self.library_down = bool(problem)
        return not problem

    def on_crash(self, row, exc):
        """A download that raised instead of failing. Returns the wait."""
        conn = self.app.conn
        if isinstance(exc, OSError) and exc.errno in LIBRARY_GONE \
                and library_problem(self.app.cfg):
            # The drive went away under it: not the video's fault, so no
            # attempt is spent. The next pass pauses and says why.
            db.set_video_status(conn, row["id"], db.QUEUED,
                                error="library unreachable: %s" % exc)
            return LIBRARY_DOWN_SECONDS

        attempts = db.bump_attempts(conn, row["id"])
        max_attempts = max(1, int(self.app.cfg.get("max_attempts", 3) or 1))
        if attempts >= max_attempts:
            db.set_video_status(conn, row["id"], db.FAILED, error="worker error: %s" % exc)
            log.error(conn, "download crashed %d times, giving up on %s: %s"
                      % (attempts, row["title"], exc),
                      channel_row_id=row["channel_row_id"], video_id=row["video_id"])
            return CRASH_PAUSE_SECONDS
        delay = self.back_off(row["id"], attempts)
        db.set_video_status(conn, row["id"], db.QUEUED, error="worker error: %s" % exc)
        log.error(conn, "download crashed for %s (attempt %d), retrying in %ds: %s"
                  % (row["title"], attempts, delay, exc),
                  channel_row_id=row["channel_row_id"], video_id=row["video_id"])
        return CRASH_PAUSE_SECONDS

    def _once(self):
        """One pass. Returns the seconds to wait before the next."""
        if not self.library_check():
            return LIBRARY_DOWN_SECONDS
        if not disk.has_headroom(self.app.cfg):
            row = self.claim()
            if row is not None:
                self.defer(row)
            return NO_SPACE_SECONDS

        self.requeue_deferred()

        row = self.claim()
        if row is None:
            return IDLE_SECONDS

        set_current(blank_progress(row))
        try:
            self.handle(row)
            self.completed += 1
        except Exception as exc:
            # A crash must never leave a row stuck in DOWNLOADING, and must
            # never turn into a tight loop: it pays an attempt and a backoff
            # like any failure, unless the drive itself has gone.
            return self.on_crash(row, exc)
        finally:
            set_current(None)
        return 0.0

    def _run(self):
        while not self._stop.is_set():
            wait = self._once()
            if wait and not self._stop.is_set():
                self._sleep(wait)

    # --- control ---

    def start(self):
        if self.thread is not None:
            return self
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name="tuner-worker",
                                       daemon=True)
        self.thread.start()
        return self

    def stop(self, timeout=10):
        self._stop.set()
        self._wake.set()

        running = self.process
        if running is not None and running.poll() is None:
            try:
                running.terminate()
            except OSError:
                pass

        if self.thread is not None:
            self.thread.join(timeout=timeout)
            self.thread = None

    def nudge(self):
        """Wake the worker early, for example right after a new queue entry."""
        self._wake.set()
