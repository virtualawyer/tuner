"""HTTP server and the /api/state snapshot.

The page renders from one payload and nothing else, so every value it shows
has to appear here. Actions POST and then re-fetch rather than patching the
DOM from a response body.
"""

import base64
import hmac
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from . import (VERSION, db, discover, disk, filters, jellyfin, log, retention,
               web, worker)

MAX_BODY = 256 * 1024
CHANNEL_CHECK_RE = re.compile(r"^/api/channels/(\d+)/check$")
CHANNEL_RE = re.compile(r"^/api/channels/(\d+)$")
VIDEO_RETRY_RE = re.compile(r"^/api/videos/(\d+)/retry$")
VIDEO_DELETE_RE = re.compile(r"^/api/videos/(\d+)/delete$")


class App(object):
    """Runtime state shared by the handler threads."""

    def __init__(self, cfg, conn, scheduler=None, worker=None):
        self.cfg = cfg
        self.conn = conn
        self.scheduler = scheduler
        self.worker = worker
        # Read from the environment only, so it never lands in config.json,
        # the settings panel or the state payload.
        self.password = os.environ.get("TUNER_PASSWORD", "")


# Moved to tuner.disk at task 5.2; kept as an alias so /api/state and the
# tests that predate the move keep one name for it.
disk_usage = disk.usage

# Running `yt-dlp --version` on every 2 second poll would spawn a process
# 30 times a minute, so the answer is cached.
_VERSION_CACHE = {"value": None, "checked_at": 0.0}
VERSION_TTL = 300.0


def ytdlp_version(cfg, force=False):
    from . import ytdlp

    now = time.time()
    if force or (now - _VERSION_CACHE["checked_at"]) > VERSION_TTL:
        _VERSION_CACHE["value"] = ytdlp.version(cfg)
        _VERSION_CACHE["checked_at"] = now
    return _VERSION_CACHE["value"]


def channel_payload(conn, cfg, row):
    counts = db.count_videos_by_status(conn, row["id"])
    totals = db.library_bytes(conn, row["id"])
    effective = filters.settings_for(cfg, row)
    return {
        "id": row["id"],
        "channel_id": row["channel_id"],
        "title": row["title"],
        "url": row["url"],
        "folder": row["folder"],
        "enabled": bool(row["enabled"]),
        # None means "inherit"; the resolved value is sent alongside so the
        # UI can show what inheriting currently amounts to.
        "keep_n": row["keep_n"],
        "seed_n": row["seed_n"],
        "skip_shorts": row["skip_shorts"],
        "skip_live": row["skip_live"],
        "effective": effective,
        "added_at": row["added_at"],
        "last_checked_at": row["last_checked_at"],
        "last_error": row["last_error"],
        "counts": counts,
        "bytes": totals["bytes"],
        "videos": totals["videos"],
    }


# What the settings panel may change, and the bounds each value has to sit
# in. Anything not listed here is not editable from the browser.
SETTINGS_SCHEMA = {
    "title": ("text", 1, 60),
    "poll_minutes": ("int", 5, 1440),
    "poll_jitter_minutes": ("int", 0, 120),
    "feed_delay_seconds": ("int", 0, 60),
    "seed_n": ("int", 0, 50),
    "keep_n": ("int", 0, 10000),
    "min_free_gb": ("int", 0, 10000),
    "shorts_max_seconds": ("int", 0, 3600),
    "max_attempts": ("int", 1, 10),
    "skip_shorts": ("bool", None, None),
    "skip_live": ("bool", None, None),
    "subtitles": ("bool", None, None),
    "prefer_h264": ("bool", None, None),
    "thumbnails": ("bool", None, None),
    "jellyfin_url": ("text", 0, 300),
    "jellyfin_user_id": ("text", 0, 100),
    "jellyfin_api_key": ("secret", 0, 200),
    "jellyfin_poll_minutes": ("int", 1, 1440),
    "watched_delete": ("bool", None, None),
    "watched_grace_hours": ("int", 0, 8760),
}

TRUE_VALUES = (True, 1, "1", "true", "True", "on", "yes")


def apply_settings(cfg, payload):
    """Validate and apply a settings change in place.

    Returns (changed, problems). The config dict is shared with every
    running thread, so applying in place is what makes a change live
    without a restart.
    """
    changed, problems = {}, []

    for key, value in payload.items():
        if key not in SETTINGS_SCHEMA:
            continue          # unknown keys are ignored, not an error
        kind, low, high = SETTINGS_SCHEMA[key]

        if kind == "bool":
            changed[key] = value in TRUE_VALUES
            continue

        if kind == "int":
            try:
                number = int(value)
            except (TypeError, ValueError):
                problems.append("%s must be a whole number" % key)
                continue
            if number < low or number > high:
                problems.append("%s must be between %d and %d" % (key, low, high))
                continue
            changed[key] = number
            continue

        text = "" if value is None else str(value).strip()
        if kind == "secret":
            # The browser is shown a masked key. Getting it back unchanged
            # means "leave it alone", so a save cannot wipe the real one.
            if not text or text == jellyfin.mask_key(cfg.get(key)):
                continue
        if len(text) > high:
            problems.append("%s is too long" % key)
            continue
        if low and len(text) < low:
            problems.append("%s cannot be empty" % key)
            continue
        changed[key] = text

    if problems:
        return ({}, problems)

    cfg.update(changed)
    return (changed, [])


def video_payload(conn, row, channel_titles):
    return {
        "id": row["id"],
        "video_id": row["video_id"],
        "title": row["title"],
        "channel": channel_titles.get(row["channel_row_id"], ""),
        "channel_row_id": row["channel_row_id"],
        "status": row["status"],
        "skip_reason": row["skip_reason"],
        "error": row["error"],
        "attempts": row["attempts"],
        "duration_s": row["duration_s"],
        "published_at": row["published_at"],
        "file_size": row["file_size"],
        "downloaded_at": row["downloaded_at"],
        "watched_at": row["watched_at"],
        "picked": bool(row["picked"]),
    }


def build_state(app):
    conn, cfg = app.conn, app.cfg
    totals = db.count_videos_by_status(conn)
    channel_rows = db.list_channels(conn)
    channels = [channel_payload(conn, cfg, row) for row in channel_rows]
    channel_titles = dict((row["id"], row["title"]) for row in channel_rows)

    if cfg.get("demo"):
        from . import demo
        demo.tick()
        space = demo.disk_usage(cfg)
        ytdlp_shown = demo.YTDLP_VERSION
    else:
        space = disk.usage(cfg.get("library_root", ""))
        ytdlp_shown = ytdlp_version(cfg)
    # Below the configured floor the worker stops starting downloads, so the
    # page has to say so rather than looking idle for no reason.
    space["low"] = space["ok"] and space["free"] <= disk.min_free_bytes(cfg)
    space["min_free_gb"] = cfg.get("min_free_gb", 10)

    library_totals = db.library_bytes(conn)

    return {
        "version": VERSION,
        "title": cfg.get("title", "Tuner"),
        "now": log.now_iso(),
        "ytdlp_version": ytdlp_shown,
        "disk": space,
        "library": {
            "root": cfg.get("library_root", ""),
            "bytes": library_totals["bytes"],
            "videos": library_totals["videos"],
            "per_channel": [
                {"id": item["id"], "title": item["title"],
                 "bytes": item["bytes"], "videos": item["videos"],
                 "keep_n": item["effective"]["keep_n"]}
                for item in channels
            ],
        },
        "counts": {
            "channels": len(channels),
            "downloaded": totals.get(db.DOWNLOADED, 0),
            "queued": totals.get(db.QUEUED, 0),
            "failed": totals.get(db.FAILED, 0),
            "skipped": totals.get(db.SKIPPED, 0),
            "missing": totals.get(db.MISSING, 0),
            "deferred": totals.get(db.DEFERRED, 0),
        },
        "jellyfin": {
            "url": cfg.get("jellyfin_url", ""),
            # Only the last four characters ever reach the browser.
            "api_key": jellyfin.mask_key(cfg.get("jellyfin_api_key")),
            "user_id": cfg.get("jellyfin_user_id", ""),
            "configured": jellyfin.configured(cfg),
            "watched_delete": bool(cfg.get("watched_delete")),
            "watched_grace_hours": cfg.get("watched_grace_hours", 24),
        },
        # Only the editable keys, so the settings panel renders from the
        # same payload as everything else.
        "settings": dict((key, cfg.get(key)) for key in SETTINGS_SCHEMA
                         if key != "jellyfin_api_key"),
        "channels": channels,
        "activity": {
            "current": worker.current(),
            "queue": [video_payload(conn, row, channel_titles)
                      for row in db.list_videos(
                          conn, status=[db.QUEUED, db.DEFERRED], limit=25,
                          order="published_at ASC, id ASC")],
        },
        "recent": [video_payload(conn, row, channel_titles)
                   for row in db.list_videos(
                       conn, limit=50,
                       order="COALESCE(downloaded_at, deleted_at,"
                             " discovered_at) DESC, id DESC")],
        "events": [
            {"ts": row["ts"], "level": row["level"], "message": row["message"],
             "video_id": row["video_id"]}
            for row in log.recent(conn, limit=50)
        ],
    }


def make_handler(app):

    class Handler(BaseHTTPRequestHandler):
        server_version = "Tuner/" + VERSION
        protocol_version = "HTTP/1.1"

        # --- plumbing ---

        def log_message(self, fmt, *args):
            """Silence the stderr access log; events go through tuner.log."""

        def _send(self, status, body, content_type, headers=()):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def send_json(self, obj, status=200):
            self._send(status, json.dumps(obj), "application/json; charset=utf-8")

        def send_html(self, markup, status=200):
            self._send(status, markup, "text/html; charset=utf-8")

        def not_found(self):
            self.send_json({"ok": False, "error": "not found"}, status=404)

        def bad_request(self, message):
            self.send_json({"ok": False, "error": message}, status=400)

        def refuse(self, status, message, headers=()):
            """Answer without reading the body, so close the connection:
            an unread body would be parsed as the next request."""
            self.close_connection = True
            self._send(status, json.dumps({"ok": False, "error": message}),
                       "application/json; charset=utf-8",
                       tuple(headers) + (("Connection", "close"),))

        def authorized(self):
            if not app.password:
                return True
            header = self.headers.get("Authorization") or ""
            if not header.startswith("Basic "):
                return False
            try:
                decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return False
            supplied = decoded.split(":", 1)[1] if ":" in decoded else ""
            return hmac.compare_digest(supplied.encode("utf-8"),
                                       app.password.encode("utf-8"))

        def admitted(self):
            """The checks every route but /healthz goes through.

            A cross-site form or a no-cors fetch can POST text/plain at a
            LAN address without the browser asking first. Requiring JSON
            makes the browser send a preflight, which this server never
            answers, so another website cannot drive Tuner through a
            visitor's browser. PATCH and DELETE always preflight.
            """
            if not self.authorized():
                self.refuse(401, "password required",
                            (("WWW-Authenticate",
                              'Basic realm="Tuner", charset="UTF-8"'),))
                return False
            if self.command in ("POST", "PATCH"):
                kind = (self.headers.get("Content-Type") or "")
                if kind.split(";", 1)[0].strip().lower() != "application/json":
                    self.refuse(415, "expected Content-Type: application/json")
                    return False
            if app.cfg.get("demo") and self.command != "GET":
                # Every action would run yt-dlp, call Jellyfin or touch
                # files, and the demo promises none of that.
                self.refuse(403, "demo mode: nothing can be changed here")
                return False
            return True

        def read_json(self):
            """Parse a JSON request body. Returns None when unusable."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length <= 0 or length > MAX_BODY:
                return None
            try:
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError, OSError):
                return None
            return payload if isinstance(payload, dict) else None

        # --- routes ---

        def query(self):
            if "?" not in self.path:
                return {}
            return parse_qs(self.path.split("?", 1)[1])

        def flag(self, name):
            values = self.query().get(name) or []
            return bool(values) and values[0].lower() in ("1", "true", "yes")

        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/healthz":
                # Open even with a password set, for container healthchecks.
                return self.send_json({"ok": True, "version": VERSION})
            if not self.admitted():
                return
            if path == "/":
                self.send_html(web.render(app.cfg))
            elif path == "/api/state":
                self.send_json(build_state(app))
            else:
                self.not_found()

        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if not self.admitted():
                return

            if path == "/api/channels":
                payload = self.read_json()
                if payload is None:
                    return self.bad_request("expected a JSON object")

                raw_urls = payload.get("urls")
                if raw_urls is None:
                    single = payload.get("url")
                    raw_urls = [single] if single else []
                if isinstance(raw_urls, str):
                    raw_urls = raw_urls.splitlines()
                if not isinstance(raw_urls, list) or not raw_urls:
                    return self.bad_request("no channel URL given")
                if len(raw_urls) > 200:
                    return self.bad_request("too many URLs in one request")

                def whole(name):
                    if name not in payload or payload[name] in (None, ""):
                        return None
                    try:
                        number = int(payload[name])
                    except (TypeError, ValueError):
                        return None
                    return max(0, min(10000, number))

                results = discover.add_channels(app.conn, app.cfg, raw_urls,
                                                seed_n=whole("seed_n"),
                                                keep_n=whole("keep_n"))
                added = sum(1 for item in results if item["ok"])
                if added and app.scheduler is not None:
                    app.scheduler.check_now()
                return self.send_json({"ok": added > 0, "added": added,
                                       "results": results})

            match = CHANNEL_CHECK_RE.match(path)
            if match:
                row_id = int(match.group(1))
                if app.scheduler is None:
                    return self.send_json(
                        {"ok": False, "error": "scheduler is not running"},
                        status=503)
                if not app.scheduler.check_now(row_id):
                    return self.send_json({"ok": False, "error": "no such channel"},
                                          status=404)
                return self.send_json({"ok": True, "message": "check queued"})

            match = VIDEO_RETRY_RE.match(path)
            if match:
                row_id = int(match.group(1))
                if app.worker is None:
                    return self.send_json(
                        {"ok": False, "error": "worker is not running"},
                        status=503)
                if not app.worker.retry(row_id):
                    return self.send_json({"ok": False, "error": "no such video"},
                                          status=404)
                return self.send_json({"ok": True, "message": "re-queued"})

            if path == "/api/browse":
                payload = self.read_json()
                if payload is None:
                    return self.bad_request("expected a JSON object")
                url = str(payload.get("url") or "").strip()
                if not url:
                    return self.bad_request("no channel URL given")
                try:
                    limit = int(payload.get("limit") or 100)
                except (TypeError, ValueError):
                    limit = 100
                return self.send_json(
                    discover.browse(app.conn, app.cfg, url, limit=limit))

            if path == "/api/pick":
                payload = self.read_json()
                if payload is None:
                    return self.bad_request("expected a JSON object")
                url = str(payload.get("url") or "").strip()
                videos = payload.get("videos") or []
                if not isinstance(videos, list) or len(videos) > 500:
                    return self.bad_request("expected a list of up to 500 videos")

                result = discover.pick(app.conn, app.cfg, url, videos)
                if result["ok"] and app.scheduler is not None:
                    app.scheduler.check_now(result.get("channel_row_id"))
                return self.send_json(result)

            if path == "/api/settings":
                payload = self.read_json()
                if payload is None:
                    return self.bad_request("expected a JSON object")

                changed, problems = apply_settings(app.cfg, payload)
                if problems:
                    return self.bad_request("; ".join(problems))
                if not changed:
                    return self.send_json({"ok": True, "changed": [],
                                           "message": "nothing to change"})

                try:
                    from . import config as config_module
                    written = config_module.save(app.cfg)
                except (OSError, ValueError) as exc:
                    return self.send_json(
                        {"ok": False,
                         "error": "applied, but could not write the config "
                                  "file: %s" % exc}, status=500)

                log.info(app.conn, "settings changed: %s"
                         % ", ".join(sorted(changed)))
                return self.send_json({
                    "ok": True,
                    "changed": sorted(changed),
                    "written_to": written,
                    # Everything editable here is read live on each use.
                    "needs_restart": [],
                })

            if path == "/api/ytdlp/update":
                from . import ytdlp

                result = ytdlp.update(app.cfg)
                ytdlp_version(app.cfg, force=True)   # refresh the header chip
                log.info(app.conn, "yt-dlp update: %s" % result["message"])
                return self.send_json(result)

            if path == "/api/jellyfin/test":
                info = jellyfin.system_info(app.cfg)
                info["users"] = jellyfin.users(app.cfg) if info["ok"] else []
                info["ok"] = bool(info["ok"])
                return self.send_json(info)

            match = VIDEO_DELETE_RE.match(path)
            if match:
                freed = retention.delete_one(app.conn, app.cfg,
                                             int(match.group(1)))
                if freed is None:
                    return self.send_json({"ok": False, "error": "no such video"},
                                          status=404)
                return self.send_json({"ok": True, "freed": freed,
                                       "freed_human": disk.human(freed)})

            self.not_found()

        def do_PATCH(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if not self.admitted():
                return

            match = CHANNEL_RE.match(path)
            if not match:
                return self.not_found()

            row_id = int(match.group(1))
            if db.get_channel(app.conn, row_id) is None:
                return self.send_json({"ok": False, "error": "no such channel"},
                                      status=404)

            payload = self.read_json()
            if payload is None:
                return self.bad_request("expected a JSON object")

            fields, problems = {}, []
            for name in ("enabled", "skip_shorts", "skip_live"):
                if name not in payload:
                    continue
                value = payload[name]
                if value is None:
                    # Only the two filters can inherit; a channel is either
                    # on or off.
                    if name == "enabled":
                        problems.append("enabled cannot be null")
                        continue
                    fields[name] = None
                else:
                    fields[name] = 1 if value in (True, 1, "1", "true") else 0

            for name in ("keep_n", "seed_n"):
                if name not in payload:
                    continue
                value = payload[name]
                if value is None or value == "":
                    fields[name] = None
                    continue
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    problems.append("%s must be a whole number" % name)
                    continue
                if number < 0 or number > 10000:
                    problems.append("%s must be between 0 and 10000" % name)
                else:
                    fields[name] = number

            if problems:
                return self.bad_request("; ".join(problems))
            if not fields:
                return self.bad_request("nothing to change")

            db.update_channel(app.conn, row_id, **fields)

            # Asking for more back-catalogue only means something if the
            # videos the seed cutoff skipped can come back.
            pulled = 0
            if "seed_n" in fields:
                pulled = discover.backfill(
                    app.conn, app.cfg, db.get_channel(app.conn, row_id))
                if pulled and app.scheduler is not None:
                    app.scheduler.check_now(row_id)

            return self.send_json({
                "pulled_back": pulled,
                "ok": True,
                "channel": channel_payload(app.conn, app.cfg,
                                           db.get_channel(app.conn, row_id)),
            })

        def do_DELETE(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if not self.admitted():
                return

            match = CHANNEL_RE.match(path)
            if match:
                result = retention.remove_channel(
                    app.conn, app.cfg, int(match.group(1)),
                    delete_files=self.flag("delete_files"))
                if result is None:
                    return self.send_json({"ok": False, "error": "no such channel"},
                                          status=404)
                result["ok"] = True
                result["freed_human"] = disk.human(result["freed"])
                return self.send_json(result)

            self.not_found()

    return Handler


def make_server(app, host=None, port=None):
    host = app.cfg.get("host", "0.0.0.0") if host is None else host
    port = app.cfg.get("port", 8770) if port is None else port

    httpd = ThreadingHTTPServer((host, int(port)), make_handler(app))
    httpd.daemon_threads = True
    return httpd


def build_app(cfg):
    if cfg.get("demo"):
        from . import demo
        return App(cfg, demo.build(cfg))

    conn = db.connect(cfg["db_path"])
    db.init_schema(conn)
    log.reconcile(conn, cfg)
    return App(cfg, conn)


def serve(cfg):
    from . import scheduler as scheduler_module
    from . import worker as worker_module

    app = build_app(cfg)

    log.prune(app.conn)  # a database from before 0.6.1 may hold millions of events

    # Demo mode runs no threads: it must never poll YouTube or spawn yt-dlp.
    if not cfg.get("demo"):
        worker_module.sweep_tmp(cfg)
        app.scheduler = scheduler_module.Scheduler(app).start()
        app.worker = worker_module.Worker(app).start()

    httpd = make_server(app)
    host, port = httpd.server_address[0], httpd.server_address[1]
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    log.info(app.conn, "%s %s listening on http://%s:%d"
             % (cfg.get("title", "Tuner"), VERSION, shown, port))

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info(app.conn, "shutting down")
    finally:
        httpd.server_close()
        if app.scheduler is not None:
            app.scheduler.stop()
        if app.worker is not None:
            app.worker.stop()
        app.conn.close()
        if cfg.get("demo"):
            from . import demo
            demo.cleanup(cfg)
    return 0
