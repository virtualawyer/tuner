"""SQLite persistence. The only module in Tuner that opens a connection.

A poller thread and a download thread both write, so every write goes
through the module lock. Callers receive plain sqlite3.Row objects and
never a cursor.
"""

import datetime
import os
import sqlite3
import threading

# One process-wide lock. SQLite serializes writers anyway; the lock keeps
# read-modify-write sequences (claiming a queued video) atomic across
# threads without relying on isolation levels.
_LOCK = threading.Lock()

# videos.status values.
NEW = "new"
PROBING = "probing"
QUEUED = "queued"
DOWNLOADING = "downloading"
DOWNLOADED = "downloaded"
SKIPPED = "skipped"
FAILED = "failed"
DEFERRED = "deferred"
MISSING = "missing"
DELETED = "deleted"

STATUSES = (NEW, PROBING, QUEUED, DOWNLOADING, DOWNLOADED,
            SKIPPED, FAILED, DEFERRED, MISSING, DELETED)

CHANNEL_FIELDS = ("title", "url", "folder", "enabled", "keep_n", "seed_n",
                  "skip_shorts",
                  "skip_live", "last_checked_at", "last_error", "etag",
                  "last_modified")

VIDEO_FIELDS = ("title", "published_at", "duration_s", "status", "skip_reason",
                "error", "attempts", "file_path", "file_size", "downloaded_at",
                "watched_at", "deleted_at", "picked")

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id              INTEGER PRIMARY KEY,
  channel_id      TEXT    UNIQUE NOT NULL,
  title           TEXT    NOT NULL,
  url             TEXT    NOT NULL,
  folder          TEXT    NOT NULL,
  enabled         INTEGER NOT NULL DEFAULT 1,
  keep_n          INTEGER,
  seed_n          INTEGER,
  skip_shorts     INTEGER,
  skip_live       INTEGER,
  added_at        TEXT    NOT NULL,
  last_checked_at TEXT,
  last_error      TEXT,
  etag            TEXT,
  last_modified   TEXT
);

CREATE TABLE IF NOT EXISTS videos (
  id             INTEGER PRIMARY KEY,
  video_id       TEXT    UNIQUE NOT NULL,
  channel_row_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  title          TEXT    NOT NULL,
  published_at   TEXT,
  duration_s     INTEGER,
  status         TEXT    NOT NULL,
  skip_reason    TEXT,
  error          TEXT,
  attempts       INTEGER NOT NULL DEFAULT 0,
  file_path      TEXT,
  file_size      INTEGER,
  downloaded_at  TEXT,
  watched_at     TEXT,
  deleted_at     TEXT,
  discovered_at  TEXT    NOT NULL,
  picked         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
  id             INTEGER PRIMARY KEY,
  ts             TEXT NOT NULL,
  level          TEXT NOT NULL,
  channel_row_id INTEGER,
  video_id       TEXT,
  message        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_videos_status  ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_row_id);
CREATE INDEX IF NOT EXISTS idx_videos_ytid    ON videos(video_id);
CREATE INDEX IF NOT EXISTS idx_events_ts      ON events(id DESC);
"""


def now_iso():
    """Current UTC time as YYYY-MM-DDTHH:MM:SSZ.

    Uses timezone.utc rather than datetime.UTC, which does not exist before
    Python 3.11 and the build machine runs 3.9.
    """
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path):
    """Open the database, creating its directory when needed."""
    if path != ":memory:":
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# Columns added after the first release. CREATE TABLE IF NOT EXISTS does
# nothing to a database that already exists, so they are added by hand.
LATER_COLUMNS = (
    ("channels", "seed_n", "INTEGER"),
    # Chosen by hand from the channel's lineup rather than discovered by
    # the poller. Picked videos bypass the filters and survive keep-N.
    ("videos", "picked", "INTEGER NOT NULL DEFAULT 0"),
)


def init_schema(conn):
    with _LOCK:
        conn.executescript(SCHEMA)
        for table, column, kind in LATER_COLUMNS:
            present = set(row["name"] for row in
                          conn.execute("PRAGMA table_info(%s)" % table))
            if column not in present:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s"
                             % (table, column, kind))
        conn.commit()
    return conn


# --- channels ---------------------------------------------------------------

def add_channel(conn, channel_id, title, url, folder, **overrides):
    """Insert a channel. Returns its row id, or None when already tracked."""
    with _LOCK:
        try:
            cur = conn.execute(
                "INSERT INTO channels (channel_id, title, url, folder, enabled,"
                " keep_n, seed_n, skip_shorts, skip_live, added_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (channel_id, title, url, folder,
                 1 if overrides.get("enabled", True) else 0,
                 overrides.get("keep_n"),
                 overrides.get("seed_n"),
                 overrides.get("skip_shorts"),
                 overrides.get("skip_live"),
                 now_iso()),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            conn.rollback()
            return None


def list_channels(conn, enabled_only=False):
    sql = "SELECT * FROM channels"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY title COLLATE NOCASE"
    return conn.execute(sql).fetchall()


def get_channel(conn, row_id):
    return conn.execute("SELECT * FROM channels WHERE id = ?", (row_id,)).fetchone()


def get_channel_by_ytid(conn, channel_id):
    return conn.execute("SELECT * FROM channels WHERE channel_id = ?",
                        (channel_id,)).fetchone()


def update_channel(conn, row_id, **fields):
    """Update only the named columns. Unknown columns raise."""
    unknown = [k for k in fields if k not in CHANNEL_FIELDS]
    if unknown:
        raise ValueError("unknown channel fields: %s" % ", ".join(sorted(unknown)))
    if not fields:
        return 0

    names = sorted(fields)
    assignments = ", ".join("%s = ?" % name for name in names)
    values = [fields[name] for name in names] + [row_id]
    with _LOCK:
        cur = conn.execute("UPDATE channels SET %s WHERE id = ?" % assignments, values)
        conn.commit()
        return cur.rowcount


def delete_channel(conn, row_id):
    with _LOCK:
        cur = conn.execute("DELETE FROM channels WHERE id = ?", (row_id,))
        conn.commit()
        return cur.rowcount


# --- videos -----------------------------------------------------------------

def add_video(conn, video_id, channel_row_id, title, published_at=None,
              status=NEW, **extra):
    """Insert a discovered video. Returns its row id, or None when known."""
    with _LOCK:
        try:
            cur = conn.execute(
                "INSERT INTO videos (video_id, channel_row_id, title,"
                " published_at, duration_s, status, discovered_at, picked)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (video_id, channel_row_id, title, published_at,
                 extra.get("duration_s"), status, now_iso(),
                 1 if extra.get("picked") else 0),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            conn.rollback()
            return None


def get_video(conn, row_id):
    return conn.execute("SELECT * FROM videos WHERE id = ?", (row_id,)).fetchone()


def get_video_by_ytid(conn, video_id):
    return conn.execute("SELECT * FROM videos WHERE video_id = ?",
                        (video_id,)).fetchone()


def list_videos(conn, status=None, channel_row_id=None, limit=None,
                order="published_at DESC, id DESC"):
    clauses, params = [], []
    if status is not None:
        if isinstance(status, (list, tuple, set)):
            statuses = sorted(status)
            clauses.append("status IN (%s)" % ",".join("?" for _ in statuses))
            params.extend(statuses)
        else:
            clauses.append("status = ?")
            params.append(status)
    if channel_row_id is not None:
        clauses.append("channel_row_id = ?")
        params.append(channel_row_id)

    sql = "SELECT * FROM videos"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY " + order
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def count_videos_by_status(conn, channel_row_id=None):
    sql = "SELECT status, COUNT(*) AS n FROM videos"
    params = []
    if channel_row_id is not None:
        sql += " WHERE channel_row_id = ?"
        params.append(channel_row_id)
    sql += " GROUP BY status"
    return dict((row["status"], row["n"]) for row in conn.execute(sql, params))


def set_video_status(conn, row_id, status, **fields):
    """Move a video to a new status, optionally setting other columns."""
    if status not in STATUSES:
        raise ValueError("unknown status: %s" % status)
    unknown = [k for k in fields if k not in VIDEO_FIELDS]
    if unknown:
        raise ValueError("unknown video fields: %s" % ", ".join(sorted(unknown)))

    fields = dict(fields)
    fields["status"] = status
    names = sorted(fields)
    assignments = ", ".join("%s = ?" % name for name in names)
    values = [fields[name] for name in names] + [row_id]
    with _LOCK:
        cur = conn.execute("UPDATE videos SET %s WHERE id = ?" % assignments, values)
        conn.commit()
        return cur.rowcount


def bump_attempts(conn, row_id):
    with _LOCK:
        conn.execute("UPDATE videos SET attempts = attempts + 1 WHERE id = ?",
                     (row_id,))
        conn.commit()
    return get_video(conn, row_id)["attempts"]


def claim_next_queued(conn, exclude_ids=None):
    """Atomically take the oldest queued video and mark it downloading.

    Returns the row as it was before the flip, or None when the queue is
    empty. Holding the lock across the select and the update is what stops
    two workers claiming the same row.
    """
    exclude_ids = set(exclude_ids or ())
    with _LOCK:
        sql = "SELECT * FROM videos WHERE status = ?"
        params = [QUEUED]
        if exclude_ids:
            ordered = sorted(exclude_ids)
            sql += " AND id NOT IN (%s)" % ",".join("?" for _ in ordered)
            params.extend(ordered)
        sql += " ORDER BY published_at ASC, id ASC LIMIT 1"

        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE videos SET status = ? WHERE id = ?",
                     (DOWNLOADING, row["id"]))
        conn.commit()
        return row


def library_bytes(conn, channel_row_id=None):
    """Sum of file sizes for rows that still have a file on disk."""
    sql = ("SELECT COALESCE(SUM(file_size), 0) AS total, COUNT(*) AS n"
           " FROM videos WHERE status = ? AND file_size IS NOT NULL")
    params = [DOWNLOADED]
    if channel_row_id is not None:
        sql += " AND channel_row_id = ?"
        params.append(channel_row_id)
    row = conn.execute(sql, params).fetchone()
    return {"bytes": row["total"], "videos": row["n"]}


# --- settings ---------------------------------------------------------------

def set_setting(conn, key, value):
    with _LOCK:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, str(value)))
        conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
