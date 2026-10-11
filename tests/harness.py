"""Test harness: a disposable Tuner with the fake yt-dlp wired in.

Stands in for the Debian box. Every test gets its own temp directory, its
own database, its own library root, and a scriptable yt-dlp. Nothing here
touches the network or the real binary.
"""

import contextlib
import json
import os
import shutil
import stat
import tempfile

from tuner import config, db, log

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_YTDLP = os.path.join(HERE, "fixtures", "fake-ytdlp.py")
FIXTURES = os.path.join(HERE, "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return handle.read()


class Harness(object):
    """A temp Tuner. Use through temp_app()."""

    def __init__(self, tmp, cfg, conn):
        self.tmp = tmp
        self.cfg = cfg
        self.conn = conn
        self.scenario_path = os.path.join(tmp, "scenario.json")
        self.calls_path = os.path.join(tmp, "calls.jsonl")
        self.scenario([])

    # --- yt-dlp scripting ---

    def scenario(self, rules, default=None):
        """Script the fake binary. Rules are tried in order."""
        payload = {
            "rules": rules,
            "default": default or {"returncode": 1,
                                   "stderr": "no scenario rule matched"},
        }
        with open(self.scenario_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.environ["TUNER_FAKE_SCENARIO"] = self.scenario_path
        os.environ["TUNER_FAKE_CALLS"] = self.calls_path
        return self

    def calls(self):
        """Every argv the fake binary was invoked with, in order."""
        if not os.path.exists(self.calls_path):
            return []
        out = []
        with open(self.calls_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def last_call(self):
        calls = self.calls()
        return calls[-1] if calls else None

    # --- convenience ---

    def add_channel(self, channel_id="UC_test", title="Test Channel",
                    url="https://www.youtube.com/@test", folder=None, **kwargs):
        return db.add_channel(self.conn, channel_id, title, url,
                              folder or title, **kwargs)

    def add_video(self, video_id, channel_row_id, title="A video",
                  published_at="2026-08-01T00:00:00Z", **kwargs):
        return db.add_video(self.conn, video_id, channel_row_id, title,
                            published_at, **kwargs)

    def channel_dir(self, name):
        path = os.path.join(self.cfg["library_root"], name)
        os.makedirs(path, exist_ok=True)
        return path

    def make_file(self, path, size=1024):
        """Create a file with real bytes, making parents as needed."""
        if not os.path.isabs(path):
            path = os.path.join(self.cfg["library_root"], path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"0" * size)
        return path

    def events(self, level=None):
        rows = log.recent(self.conn, limit=500)
        if level:
            rows = [r for r in rows if r["level"] == level]
        return [r["message"] for r in rows]

    def close(self):
        try:
            self.conn.close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)
            os.environ.pop("TUNER_FAKE_SCENARIO", None)
            os.environ.pop("TUNER_FAKE_CALLS", None)


@contextlib.contextmanager
def temp_app(**overrides):
    """A disposable Tuner rooted in a temp directory."""
    tmp = tempfile.mkdtemp(prefix="tuner-test-")

    # The fake is executed as a binary, so guarantee the exec bit no matter
    # how the repo was copied around.
    mode = os.stat(FAKE_YTDLP).st_mode
    os.chmod(FAKE_YTDLP, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    cfg = config.defaults()
    cfg["library_root"] = os.path.join(tmp, "library")
    cfg["db_path"] = os.path.join(tmp, "state", "tuner.db")
    cfg["ytdlp_path"] = FAKE_YTDLP
    cfg["ffmpeg_path"] = "/bin/true"
    cfg["feed_delay_seconds"] = 0      # no real waiting in tests
    cfg.update(overrides)
    try:
        os.makedirs(cfg["library_root"], exist_ok=True)
    except OSError:
        # A test may point library_root at an unreachable path on purpose,
        # standing in for the media drive being unmounted.
        pass

    conn = db.connect(cfg["db_path"])
    db.init_schema(conn)

    harness = Harness(tmp, cfg, conn)
    try:
        yield harness
    finally:
        harness.close()


# --- ready-made scenario rules ----------------------------------------------

def rule_version(version="2026.08.01"):
    return {"match": ["--version"], "stdout": version, "returncode": 0}


def rule_resolve(channel_id="UC_alpha_0001", title="Alpha Channel",
                 match=None, payload=None):
    """A channel resolve: playlist-level metadata, the way -J returns it."""
    body = payload if payload is not None else {
        "_type": "playlist",
        "id": channel_id,
        "channel_id": channel_id,
        "channel": title,
        "uploader": title,
        "title": "%s - Videos" % title,
        "entries": [],
    }
    return {"match": match or ["-J", "--flat-playlist"],
            "stdout": json.dumps(body), "returncode": 0}


def rule_resolve_flat_stub(channel_id="UC_alpha_0001", title="Alpha Channel",
                           match=None):
    """The shape that used to break resolution.

    A channel tab whose playlist level carries no channel_id, only a video
    stub underneath it. Printing a per-entry template against this returned
    NA|NA.
    """
    return rule_resolve(match=match, payload={
        "_type": "playlist",
        "id": channel_id,
        "title": "%s - Videos" % title,
        "entries": [{"_type": "url", "id": "vid00000001",
                     "title": "Some video"}],
    })


def rule_probe(video_id="vid00000001", title="A video", duration=600,
               width=1920, height=1080, live_status="not_live",
               channel_id="UC_alpha_0001", channel="Alpha Channel",
               upload_date="20260801", match=None, extra=None):
    payload = {
        "id": video_id,
        "title": title,
        "duration": duration,
        "width": width,
        "height": height,
        "live_status": live_status,
        "was_live": live_status == "was_live",
        "upload_date": upload_date,
        "timestamp": 1785000000,
        "channel_id": channel_id,
        "channel": channel,
        "availability": "public",
    }
    if extra:
        payload.update(extra)
    return {"match": match or ["-J", video_id],
            "stdout": json.dumps(payload), "returncode": 0}


def rule_download(video_id="vid00000001", size=4096, progress=None, match=None):
    return {
        "match": match or ["--merge-output-format", video_id],
        "progress_lines": progress or [
            "[download] Destination: %s.f137.mp4" % video_id,
            "[download]   0.0% of   10.00MiB at    1.00MiB/s ETA 00:10",
            "[download]  50.0% of   10.00MiB at    2.00MiB/s ETA 00:05",
            "[download] 100% of   10.00MiB in 00:05",
            "[Merger] Merging formats into \"%s.mp4\"" % video_id,
            "[EmbedThumbnail] mp4 cover art added",
        ],
        "creates": ["%(id)s.mp4", "%(id)s.en.srt"],
        "creates_size": size,
        "returncode": 0,
    }
