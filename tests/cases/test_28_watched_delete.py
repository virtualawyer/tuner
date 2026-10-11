"""Task 7.3 -- deleting what Jellyfin says has been watched."""

import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import harness
from tuner import db, retention, scheduler, server

VIDEO_A = "dQw4w9WgXcQ"
VIDEO_B = "aBcDeFgHiJk"
HOUR = 3600


class StubJellyfin(object):
    """Serves a fixed set of played items."""

    def __init__(self, items):
        self.items = items

    def __enter__(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({"Items": stub.items,
                                   "TotalRecordCount": len(stub.items)}
                                  ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def played_item(video_id, played_at):
    return {"Id": "jf-" + video_id,
            "Name": "A video",
            "Path": "/media/youtube/Alpha/2026-08-11 - A video [%s].mp4" % video_id,
            "UserData": {"Played": True, "LastPlayedDate": played_at}}


def downloaded(app, video_id=VIDEO_A):
    chan = app.add_channel(channel_id="UC_a", title="Alpha",
                           url="https://www.youtube.com/channel/UC_a",
                           folder="Alpha")
    path = app.make_file("Alpha/2026-08-11 - A video [%s].mp4" % video_id,
                         size=2048)
    app.add_video(video_id, chan, "A video", published_at="2026-08-11T00:00:00Z")
    row = db.get_video_by_ytid(app.conn, video_id)
    db.set_video_status(app.conn, row["id"], db.DOWNLOADED,
                        file_path=path, file_size=2048)
    return path


def watching(stub_url, **extra):
    settings = {"jellyfin_url": stub_url, "jellyfin_api_key": "k",
                "jellyfin_user_id": "user-1", "watched_delete": True,
                "watched_grace_hours": 24}
    settings.update(extra)
    return harness.temp_app(**settings)


class ParseIsoTest(unittest.TestCase):

    def test_jellyfin_style_timestamps(self):
        # Seven fractional digits and a Z: what Jellyfin actually sends.
        self.assertIsNotNone(retention.parse_iso("2026-08-11T12:00:00.0000000Z"))
        self.assertIsNotNone(retention.parse_iso("2026-08-11T12:00:00Z"))
        self.assertIsNotNone(retention.parse_iso("2026-08-11T12:00:00"))

    def test_offsets_are_applied(self):
        utc = retention.parse_iso("2026-08-11T12:00:00Z")
        plus_two = retention.parse_iso("2026-08-11T14:00:00+02:00")
        self.assertEqual(utc, plus_two)

    def test_unreadable_values(self):
        for bad in ("", None, "yesterday", "2026-13-45T99:99:99Z"):
            self.assertIsNone(retention.parse_iso(bad))

    def test_round_trip_with_our_own_format(self):
        stamp = retention.parse_iso("2026-08-11T12:00:00Z")
        self.assertEqual(retention.iso_from_epoch(stamp), "2026-08-11T12:00:00Z")


class SyncWatchedTest(unittest.TestCase):

    def test_inside_the_grace_window_nothing_is_deleted(self):
        now = retention.parse_iso("2026-08-11T12:00:00Z")
        items = [played_item(VIDEO_A, "2026-08-11T06:00:00Z")]   # 6 hours ago

        with StubJellyfin(items) as stub:
            with watching(stub.url, watched_grace_hours=24) as app:
                path = downloaded(app)
                result = retention.sync_watched(app.conn, app.cfg, now=now)

                self.assertEqual(result["marked"], 1)
                self.assertEqual(result["deleted"], 0)
                self.assertTrue(os.path.exists(path))

                row = db.get_video_by_ytid(app.conn, VIDEO_A)
                self.assertEqual(row["status"], db.DOWNLOADED)
                self.assertEqual(row["watched_at"], "2026-08-11T06:00:00Z")

    def test_past_the_grace_window_the_file_goes(self):
        now = retention.parse_iso("2026-08-13T12:00:00Z")
        items = [played_item(VIDEO_A, "2026-08-11T06:00:00Z")]   # 2 days ago

        with StubJellyfin(items) as stub:
            with watching(stub.url, watched_grace_hours=24) as app:
                path = downloaded(app)
                result = retention.sync_watched(app.conn, app.cfg, now=now)

                self.assertEqual(result["deleted"], 1)
                self.assertEqual(result["freed"], 2048)
                self.assertFalse(os.path.exists(path))
                self.assertEqual(
                    db.get_video_by_ytid(app.conn, VIDEO_A)["status"], db.DELETED)
                self.assertTrue(any("watched in Jellyfin" in m
                                    for m in app.events()))

    def test_a_second_pass_deletes_what_the_first_only_marked(self):
        items = [played_item(VIDEO_A, "2026-08-11T06:00:00Z")]
        with StubJellyfin(items) as stub:
            with watching(stub.url, watched_grace_hours=24) as app:
                path = downloaded(app)

                first = retention.sync_watched(
                    app.conn, app.cfg,
                    now=retention.parse_iso("2026-08-11T12:00:00Z"))
                self.assertEqual(first["deleted"], 0)

                second = retention.sync_watched(
                    app.conn, app.cfg,
                    now=retention.parse_iso("2026-08-12T12:00:00Z"))
                self.assertEqual(second["deleted"], 1)
                self.assertFalse(os.path.exists(path))

    def test_a_zero_grace_deletes_on_sight(self):
        now = retention.parse_iso("2026-08-11T12:00:00Z")
        items = [played_item(VIDEO_A, "2026-08-11T11:59:00Z")]

        with StubJellyfin(items) as stub:
            with watching(stub.url, watched_grace_hours=0) as app:
                path = downloaded(app)
                retention.sync_watched(app.conn, app.cfg, now=now)
                self.assertFalse(os.path.exists(path))

    def test_watched_delete_switched_off_does_nothing(self):
        items = [played_item(VIDEO_A, "2026-08-01T00:00:00Z")]
        with StubJellyfin(items) as stub:
            with watching(stub.url, watched_delete=False) as app:
                path = downloaded(app)
                result = retention.sync_watched(app.conn, app.cfg)

                self.assertEqual(result, {"marked": 0, "deleted": 0, "freed": 0})
                self.assertTrue(os.path.exists(path))

    def test_an_incomplete_configuration_does_nothing(self):
        items = [played_item(VIDEO_A, "2026-08-01T00:00:00Z")]
        with StubJellyfin(items) as stub:
            with watching(stub.url, jellyfin_user_id="") as app:
                path = downloaded(app)
                retention.sync_watched(app.conn, app.cfg)
                self.assertTrue(os.path.exists(path))

    def test_an_already_deleted_row_is_left_alone(self):
        items = [played_item(VIDEO_A, "2026-08-01T00:00:00Z")]
        with StubJellyfin(items) as stub:
            with watching(stub.url) as app:
                downloaded(app)
                row = db.get_video_by_ytid(app.conn, VIDEO_A)
                db.set_video_status(app.conn, row["id"], db.DELETED)

                result = retention.sync_watched(app.conn, app.cfg)
                self.assertEqual(result["deleted"], 0)
                self.assertEqual(result["marked"], 0)

    def test_a_played_video_tuner_does_not_track_is_ignored(self):
        items = [played_item(VIDEO_B, "2026-08-01T00:00:00Z")]
        with StubJellyfin(items) as stub:
            with watching(stub.url) as app:
                path = downloaded(app, VIDEO_A)
                result = retention.sync_watched(app.conn, app.cfg)

                self.assertEqual(result["deleted"], 0)
                self.assertTrue(os.path.exists(path))

    def test_an_unreadable_played_timestamp_falls_back_to_now(self):
        items = [played_item(VIDEO_A, "not a date")]
        with StubJellyfin(items) as stub:
            with watching(stub.url, watched_grace_hours=24) as app:
                downloaded(app)
                result = retention.sync_watched(app.conn, app.cfg)

                self.assertEqual(result["marked"], 1)
                self.assertEqual(result["deleted"], 0)
                self.assertIsNotNone(
                    db.get_video_by_ytid(app.conn, VIDEO_A)["watched_at"])


class CadenceTest(unittest.TestCase):

    def test_the_sync_runs_on_its_own_slower_clock(self):
        with StubJellyfin([]) as stub:
            with watching(stub.url, jellyfin_poll_minutes=15) as app:
                sched = scheduler.Scheduler(server.App(app.cfg, app.conn))
                now = 1_000_000.0

                self.assertIsNotNone(sched.maybe_sync_watched(now=now))
                # Five minutes later the poll cycle runs again, but the
                # Jellyfin sync should not.
                self.assertIsNone(sched.maybe_sync_watched(now=now + 300))
                self.assertIsNotNone(sched.maybe_sync_watched(now=now + 16 * 60))

    def test_the_sync_is_skipped_entirely_when_switched_off(self):
        with harness.temp_app(watched_delete=False) as app:
            sched = scheduler.Scheduler(server.App(app.cfg, app.conn))
            self.assertIsNone(sched.maybe_sync_watched(now=time.time()))


if __name__ == "__main__":
    unittest.main()
