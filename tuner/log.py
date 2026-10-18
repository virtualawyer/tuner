"""Event log and startup reconciliation.

Events go to the database so the web page can show them, and to stdout so
`journalctl -u tuner` shows the same lines.
"""

import os
import sys

from . import db
from .db import now_iso  # single implementation, re-exported as log.now_iso

INFO = "info"
WARN = "warn"
ERROR = "error"
LEVELS = (INFO, WARN, ERROR)

# The test runner sets this so a suite run does not bury itself in log lines.
# Events still reach the database; only the journald mirror is silenced.
QUIET = os.environ.get("TUNER_LOG_QUIET") == "1"


def event(conn, level, message, channel_row_id=None, video_id=None):
    """Record one event. Never raises: logging must not break a cycle."""
    if level not in LEVELS:
        level = INFO
    stamp = now_iso()

    if not QUIET:
        line = "%s %-5s %s" % (stamp, level.upper(), message)
        if video_id:
            line += " [%s]" % video_id
        stream = sys.stderr if level == ERROR else sys.stdout
        try:
            stream.write(line + "\n")
            stream.flush()
        except (IOError, ValueError):
            pass

    if conn is None:
        return None
    try:
        with db._LOCK:
            cur = conn.execute(
                "INSERT INTO events (ts, level, channel_row_id, video_id, message)"
                " VALUES (?,?,?,?,?)",
                (stamp, level, channel_row_id, video_id, message))
            conn.commit()
            row_id = cur.lastrowid
        if row_id and row_id % PRUNE_EVERY == 0:
            prune(conn)
        return row_id
    except Exception:  # pragma: no cover - a broken log must not break the app
        return None


def info(conn, message, **kwargs):
    return event(conn, INFO, message, **kwargs)


def warn(conn, message, **kwargs):
    return event(conn, WARN, message, **kwargs)


def error(conn, message, **kwargs):
    return event(conn, ERROR, message, **kwargs)


def recent(conn, limit=200):
    """Newest first."""
    return conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()


# Trim to the newest KEEP_EVENTS every PRUNE_EVERY events. (Until 0.6.1
# nothing called prune, and a retry loop wrote 4.5 million rows.)
KEEP_EVENTS = 5000
PRUNE_EVERY = 500


def prune(conn, keep=None):
    """Keep the log from growing without bound on a long-running box."""
    keep = KEEP_EVENTS if keep is None else keep
    with db._LOCK:
        conn.execute(
            "DELETE FROM events WHERE id NOT IN"
            " (SELECT id FROM events ORDER BY id DESC LIMIT ?)", (int(keep),))
        conn.commit()


def reconcile(conn, cfg=None):
    """Bring the database back in step with the filesystem at startup.

    Two situations survive a crash or a restart: a row that claims a file
    that is no longer there, and a row left mid-download. Neither is
    re-queued blindly; a missing file is surfaced for the operator to
    decide, and only the interrupted download goes back in the queue.
    """
    missing = 0
    for row in db.list_videos(conn, status=db.DOWNLOADED):
        path = row["file_path"]
        if path and os.path.exists(path):
            continue
        db.set_video_status(conn, row["id"], db.MISSING)
        warn(conn, "file missing on disk, marked missing: %s" % (path or "no path"),
             channel_row_id=row["channel_row_id"], video_id=row["video_id"])
        missing += 1

    requeued = 0
    for row in db.list_videos(conn, status=db.DOWNLOADING):
        db.set_video_status(conn, row["id"], db.QUEUED)
        info(conn, "download interrupted by restart, re-queued: %s" % row["title"],
             channel_row_id=row["channel_row_id"], video_id=row["video_id"])
        requeued += 1

    return {"missing": missing, "requeued": requeued}
