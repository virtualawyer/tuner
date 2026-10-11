"""Demo mode: a populated Tuner that touches nothing real.

`python3 -m tuner --demo` seeds a throwaway database in a temp directory so
the page can be reviewed on a machine that is not the server. No subprocess
runs, no network request is made, and nothing is written outside the temp
directory.
"""

import os
import shutil
import tempfile

from . import db, disk, log, worker

CHANNELS = (
    # (channel id, title, keep_n, skip_shorts, enabled, last_error)
    ("UCdemobenchnotes00000001", "Bench Notes", None, None, 1, None),
    ("UCdemosmallsystems000001", "Small Systems", 3, 0, 1, None),
    ("UCdemohomelabdiaries0001", "Homelab Diaries", None, None, 1,
     "feed returned HTTP 404"),
    ("UCdemoquietchannel000001", "Quiet Channel", None, None, 0, None),
)

# (title, status, size, skip reason, error)
VIDEOS = (
    ("How a Chromebook became a 3TB NAS", db.DOWNLOADED, 1_240_000_000, None, None),
    ("Fixing exFAT and the vanishing permissions", db.DOWNLOADED, 890_000_000,
     None, None),
    ("Wir bauen einen Medienserver -- Folge 3: Übertragung", db.DOWNLOADED,
     1_010_000_000, None, None),
    ("The 13GB root partition problem", db.QUEUED, None, None, None),
    ("yt-dlp in 2026: what changed", db.QUEUED, None, None, None),
    ("SQLite WAL mode explained", db.DEFERRED, None, None,
     "waiting for disk space (4.2 GB free)"),
    ("Quick tip: fixing a stuck mount", db.SKIPPED, None, "short", None),
    ("LIVE: building the rack", db.SKIPPED, None, "was_live", None),
    ("Members-only Q and A", db.SKIPPED, None, "members-only", None),
    ("Backing up 2.6TB without a second drive", db.FAILED, None, None,
     "ERROR: fragment 3 not found; giving up after 3 attempts"),
    ("An episode whose file went missing", db.MISSING, None, None, None),
    ("An older episode tidied away by the keep rule", db.DELETED, None, None,
     None),
)

# Shown in the header chip, so the demo never runs `yt-dlp --version`.
YTDLP_VERSION = "2026.09.16"

# A synthetic 2.8 TB drive, so the demo shows the figures the real box does.
DISK = {"total": 2_800 * disk.GB, "free": 1_180 * disk.GB, "ok": True}

_progress = {"percent": 3.0}


def disk_usage(cfg=None):
    total, free = DISK["total"], DISK["free"]
    used = total - free
    return {"total": total, "used": used, "free": free,
            "percent": int(round((used * 100.0) / total)), "ok": True}


def tick():
    """Advance the fake download a little on each state poll."""
    _progress["percent"] += 1.7
    if _progress["percent"] > 99:
        _progress["percent"] = 3.0

    percent = round(_progress["percent"], 1)
    worker.set_current({
        "video_id": "demo0000001",
        "title": "The 13GB root partition problem",
        "percent": percent,
        "size": "1.42GiB",
        "rate": "3.40MiB/s",
        "eta": "0%d:%02d" % (int((100 - percent) / 20),
                             int((100 - percent) * 0.6) % 60),
        "stage": "downloading" if percent < 95 else "merging streams",
    })


def build(cfg):
    """Point the config at a throwaway tree and fill it with sample rows."""
    root = tempfile.mkdtemp(prefix="tuner-demo-")
    cfg["library_root"] = os.path.join(root, "library")
    cfg["db_path"] = os.path.join(root, "state", "tuner.db")
    cfg["demo"] = True
    cfg["_demo_root"] = root
    os.makedirs(cfg["library_root"], exist_ok=True)

    conn = db.connect(cfg["db_path"])
    db.init_schema(conn)

    for index, (channel_id, title, keep_n, shorts, enabled, error) in \
            enumerate(CHANNELS):
        row_id = db.add_channel(conn, channel_id, title,
                                "https://www.youtube.com/channel/" + channel_id,
                                title, keep_n=keep_n, skip_shorts=shorts,
                                enabled=bool(enabled))
        db.update_channel(conn, row_id, last_checked_at=log.now_iso(),
                          last_error=error)

        # The quiet channel stays empty on purpose, so the demo shows a
        # channel that has nothing yet.
        if title == "Quiet Channel":
            continue

        for offset, (video_title, status, size, reason, error_text) in \
                enumerate(VIDEOS):
            video_id = "demo%03d%04d" % (index, offset)
            db.add_video(conn, video_id, row_id, video_title,
                         published_at="2026-08-%02dT12:00:00Z" % (offset + 1),
                         duration_s=420 + offset * 137)
            row = db.get_video_by_ytid(conn, video_id)

            fields = {}
            if size:
                path = os.path.join(cfg["library_root"], title,
                                    "2026-08-%02d - %s [%s].mp4"
                                    % (offset + 1, video_title[:60], video_id))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(b"0")
                fields.update({"file_path": path, "file_size": size,
                               "downloaded_at": log.now_iso()})
            if reason:
                fields["skip_reason"] = reason
            if error_text:
                fields["error"] = error_text
                fields["attempts"] = 3
            db.set_video_status(conn, row["id"], status, **fields)

    log.info(conn, "demo mode: sample data seeded, nothing here is real")
    log.warn(conn, "feed fetch failed for Homelab Diaries: feed returned HTTP 404")
    log.info(conn, "downloaded How a Chromebook became a 3TB NAS (1.2 GB)")
    log.info(conn, "deleted An older episode tidied away by the keep rule "
                   "(keep newest 5)")
    tick()
    return conn


def cleanup(cfg):
    root = cfg.get("_demo_root")
    if root:
        shutil.rmtree(root, ignore_errors=True)
