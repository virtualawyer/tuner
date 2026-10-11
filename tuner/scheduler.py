"""The background poll loop.

One daemon thread. It sleeps in short slices against an Event so a restart
is immediate rather than waiting out a 30 minute nap, and so the UI can
force a cycle without a second thread.
"""

import random
import threading
import time

from . import LIBRARY_CHANGED, db, discover, jellyfin, log, retention

# Never poll tighter than this, whatever the config says. YouTube rate
# limits aggressive pollers and a home server gains nothing from it.
MIN_INTERVAL_MINUTES = 5
SLICE_SECONDS = 1.0


class Scheduler(object):

    def __init__(self, app):
        self.app = app
        self.thread = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._forced = None
        self._lock = threading.Lock()
        self.cycles = 0
        self.last_cycle_at = None
        # The Jellyfin sync runs on its own cadence inside this thread
        # rather than in a third thread of its own.
        self._last_watched_sync = 0.0

    # --- timing ---

    def interval_seconds(self):
        cfg = self.app.cfg
        minutes = max(MIN_INTERVAL_MINUTES, int(cfg.get("poll_minutes", 30) or 0))
        jitter = max(0, int(cfg.get("poll_jitter_minutes", 5) or 0))
        return (minutes * 60) + random.randint(0, jitter * 60)

    def _sleep(self, seconds):
        """Sleep in slices so stop() and check_now() are felt at once."""
        remaining = float(seconds)
        while remaining > 0 and not self._stop.is_set():
            if self._wake.wait(timeout=min(SLICE_SECONDS, remaining)):
                self._wake.clear()
                return
            remaining -= SLICE_SECONDS

    # --- work ---

    def _cycle(self, only_channel_id=None):
        """One pass over the channels. Never raises."""
        conn, cfg = self.app.conn, self.app.cfg
        found = 0
        try:
            found = discover.ingest_all(conn, cfg,
                                        only_channel_id=only_channel_id,
                                        stop_event=self._stop)
            discover.process_new(conn, cfg)
            retention.prune_all(conn, cfg)
            self.maybe_sync_watched()
            self.maybe_refresh_library()
        except Exception as exc:      # the loop must outlive any one cycle
            log.error(conn, "poll cycle failed: %s" % exc)

        self.cycles += 1
        self.last_cycle_at = log.now_iso()
        return found

    def maybe_sync_watched(self, now=None):
        """Ask Jellyfin what has been watched, on its own slower cadence."""
        cfg = self.app.cfg
        if not cfg.get("watched_delete"):
            return None

        now = time.time() if now is None else now
        interval = max(1, int(cfg.get("jellyfin_poll_minutes", 15) or 15)) * 60
        if (now - self._last_watched_sync) < interval:
            return None

        self._last_watched_sync = now
        return retention.sync_watched(self.app.conn, cfg)

    def maybe_refresh_library(self):
        """Rescan Jellyfin only when this cycle actually changed a file."""
        if not LIBRARY_CHANGED.is_set():
            return False
        LIBRARY_CHANGED.clear()
        return jellyfin.refresh_library(self.app.cfg, conn=self.app.conn)

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                target, self._forced = self._forced, None
            self._cycle(only_channel_id=target)
            if self._stop.is_set():
                break
            self._sleep(self.interval_seconds())

    # --- control ---

    def start(self):
        if self.thread is not None:
            return self
        self.thread = threading.Thread(target=self._run, name="tuner-scheduler",
                                       daemon=True)
        self.thread.start()
        return self

    def stop(self, timeout=5):
        self._stop.set()
        self._wake.set()
        if self.thread is not None:
            self.thread.join(timeout=timeout)
            self.thread = None

    def check_now(self, channel_row_id=None):
        """Force a cycle. Returns False when the channel is unknown."""
        if channel_row_id is not None:
            if db.get_channel(self.app.conn, channel_row_id) is None:
                return False
        with self._lock:
            self._forced = channel_row_id
        self._wake.set()
        return True
