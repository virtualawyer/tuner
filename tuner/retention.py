"""Deleting things, carefully.

This module is the only place in Tuner that unlinks a file. Every delete
resolves the real path and refuses anything that does not sit under the
configured library root, so a bad database row, a crafted title, or a
symlink cannot reach outside the library.
"""

import calendar
import datetime
import os
import re
import time

from . import LIBRARY_CHANGED, db, filters, jellyfin, log, paths

# Jellyfin timestamps look like 2026-08-11T12:00:00.0000000Z: seven
# fractional digits and a Z, neither of which fromisoformat accepts on the
# Python this has to run on.
ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:\.\d+)?(?:(Z)|([+-])(\d{2}):?(\d{2}))?$")


class UnsafePathError(Exception):
    """A delete was refused because the path escaped the library root."""


def is_inside(root, path):
    """True when `path` really sits under `root`.

    Both sides are resolved first, so a symlink pointing out of the library
    is judged by its target rather than its location.
    """
    real_root = os.path.realpath(root)
    real_path = os.path.realpath(path)
    if real_root in ("", os.sep):
        return False
    return real_path.startswith(real_root + os.sep)


def _unlink(path):
    """The single unlink in the codebase. Returns the bytes freed."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    try:
        os.unlink(path)
    except OSError:
        return 0
    return size


def sidecars_of(path):
    """Companion files sharing the media file's stem."""
    directory = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    found = []
    try:
        names = os.listdir(directory)
    except OSError:
        return found
    for name in sorted(names):
        candidate = os.path.join(directory, name)
        if candidate == path:
            continue
        if name.startswith(stem + "."):
            found.append(candidate)
    return found


def safe_delete(cfg, path, conn=None):
    """Delete one media file, its sidecars, and an emptied channel folder.

    Returns the number of bytes freed. Raises UnsafePathError, without
    deleting anything, when the path is not inside the library.
    """
    root = paths.library_root(cfg)
    if not path:
        raise UnsafePathError("no path given")

    if not is_inside(root, path):
        message = "refused to delete outside the library: %s" % path
        if conn is not None:
            log.error(conn, message)
        raise UnsafePathError(message)

    freed = 0
    for companion in sidecars_of(path):
        freed += _unlink(companion)
    freed += _unlink(path)

    # A channel folder left empty is noise in Jellyfin's library.
    directory = os.path.dirname(os.path.realpath(path))
    if directory != os.path.realpath(root) and is_inside(root, directory):
        try:
            if not os.listdir(directory):
                os.rmdir(directory)
        except OSError:
            pass

    if freed:
        LIBRARY_CHANGED.set()
    return freed


def delete_video(conn, cfg, row, reason):
    """Delete a video's file and mark the row deleted.

    The row is kept on purpose: it is what stops the next feed poll from
    downloading the same video again.
    """
    freed = 0
    if row["file_path"]:
        try:
            freed = safe_delete(cfg, row["file_path"], conn=conn)
        except UnsafePathError:
            return 0

    db.set_video_status(conn, row["id"], db.DELETED,
                        deleted_at=log.now_iso(), file_size=None)
    log.info(conn, "deleted %s (%s)" % (row["title"], reason),
             channel_row_id=row["channel_row_id"], video_id=row["video_id"])
    return freed


def prune_channel(conn, cfg, channel_row):
    """Keep only the newest N downloaded videos for one channel.

    A keep_n of 0 means unlimited, not delete-everything. That distinction
    is the difference between a retention rule and a disaster, so it is
    checked before anything is listed.
    """
    keep_n = filters.settings_for(cfg, channel_row).get("keep_n", 0)
    if not keep_n or keep_n <= 0:
        return {"deleted": 0, "freed": 0}

    rows = [row for row in
            db.list_videos(conn, status=db.DOWNLOADED,
                           channel_row_id=channel_row["id"],
                           order="published_at DESC, id DESC")
            # A curated pick is not churn. Keep-N bounds what the poller
            # brings in; it has no business deleting a deliberate choice.
            if not row["picked"]]

    deleted, freed = 0, 0
    for row in rows[keep_n:]:
        freed += delete_video(conn, cfg, row, "keep newest %d" % keep_n)
        deleted += 1
    return {"deleted": deleted, "freed": freed}


def delete_one(conn, cfg, row_id, reason="deleted by hand"):
    """Delete a single video on request. Returns freed bytes, or None."""
    row = db.get_video(conn, row_id)
    if row is None:
        return None
    if row["status"] == db.DELETED:
        return 0
    return delete_video(conn, cfg, row, reason)


def remove_channel(conn, cfg, row_id, delete_files=False):
    """Stop tracking a channel, optionally taking its downloads with it.

    Without the flag the files stay on disk and only the rows go, which is
    what someone means by "stop following this" most of the time.
    """
    channel_row = db.get_channel(conn, row_id)
    if channel_row is None:
        return None

    freed, removed = 0, 0
    if delete_files:
        for row in db.list_videos(conn, status=db.DOWNLOADED,
                                  channel_row_id=row_id):
            if not row["file_path"]:
                continue
            try:
                freed += safe_delete(cfg, row["file_path"], conn=conn)
                removed += 1
            except UnsafePathError:
                continue

    title = channel_row["title"]
    db.delete_channel(conn, row_id)      # ON DELETE CASCADE takes the videos
    log.info(conn, "removed channel %s (%d file%s deleted)"
             % (title, removed, "" if removed == 1 else "s"))
    return {"title": title, "files_deleted": removed, "freed": freed}


def parse_iso(text):
    """An ISO timestamp as epoch seconds, or None when unreadable."""
    match = ISO_RE.match(str(text or "").strip())
    if not match:
        return None

    year, month, day, hour, minute, second = (int(match.group(n))
                                              for n in range(1, 7))
    try:
        stamp = calendar.timegm(
            (year, month, day, hour, minute, second, 0, 0, 0))
    except (ValueError, OverflowError):
        return None

    sign, offset_h, offset_m = match.group(8), match.group(9), match.group(10)
    if sign and offset_h is not None:
        offset = (int(offset_h) * 3600) + (int(offset_m) * 60)
        stamp += -offset if sign == "+" else offset
    return stamp


def sync_watched(conn, cfg, now=None):
    """Mark watched videos, then delete the ones past their grace period.

    Runs only when watched deletion is switched on and Jellyfin is fully
    configured. A row is marked the first time Jellyfin reports it played;
    the clock then runs from Jellyfin's own timestamp where it gives one.
    """
    result = {"marked": 0, "deleted": 0, "freed": 0}

    if not cfg.get("watched_delete"):
        return result
    if not jellyfin.configured(cfg) or not (cfg.get("jellyfin_user_id") or "").strip():
        return result

    played = jellyfin.played_video_ids(cfg)
    if not played:
        return result

    now = time.time() if now is None else now
    grace = max(0, int(cfg.get("watched_grace_hours", 24) or 0)) * 3600

    for video_id, played_at in played.items():
        row = db.get_video_by_ytid(conn, video_id)
        if row is None or row["status"] != db.DOWNLOADED:
            continue

        watched_at = row["watched_at"]
        if not watched_at:
            # Prefer Jellyfin's own timestamp: if Tuner was off for a week,
            # a video watched six days ago should not get a fresh grace
            # period just because this is the first time we noticed.
            stamp = parse_iso(played_at)
            watched_at = (iso_from_epoch(stamp) if stamp is not None
                          else log.now_iso())
            db.set_video_status(conn, row["id"], db.DOWNLOADED,
                                watched_at=watched_at)
            result["marked"] += 1
            log.info(conn, "watched in Jellyfin: %s" % row["title"],
                     channel_row_id=row["channel_row_id"], video_id=video_id)

        marked_at = parse_iso(watched_at)
        if marked_at is None or (now - marked_at) < grace:
            continue

        row = db.get_video(conn, row["id"])
        result["freed"] += delete_video(conn, cfg, row, "watched in Jellyfin")
        result["deleted"] += 1

    return result


def iso_from_epoch(stamp):
    return datetime.datetime.utcfromtimestamp(stamp).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def prune_all(conn, cfg):
    """Apply the keep rule to every channel."""
    totals = {"deleted": 0, "freed": 0}
    for channel_row in db.list_channels(conn):
        try:
            result = prune_channel(conn, cfg, channel_row)
        except Exception as exc:      # one bad channel must not stop the rest
            log.error(conn, "prune failed for %s: %s" % (channel_row["title"], exc),
                      channel_row_id=channel_row["id"])
            continue
        totals["deleted"] += result["deleted"]
        totals["freed"] += result["freed"]
    return totals
