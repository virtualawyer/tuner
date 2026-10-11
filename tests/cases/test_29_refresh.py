"""Task 7.4 -- asking Jellyfin to rescan, but only when something changed."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import harness
from tuner import LIBRARY_CHANGED, db, jellyfin, scheduler, server


class StubJellyfin(object):

    def __init__(self, status=204):
        self.status = status
        self.seen = []

    def __enter__(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                stub.seen.append({"path": self.path, "method": "POST"})
                self.send_response(stub.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                body = json.dumps({"Items": [], "TotalRecordCount": 0}).encode()
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


def connected(stub_url, **extra):
    settings = {"jellyfin_url": stub_url, "jellyfin_api_key": "k",
                "jellyfin_user_id": "user-1"}
    settings.update(extra)
    return harness.temp_app(**settings)


class RefreshTest(unittest.TestCase):

    def test_a_refresh_posts_to_the_library_route(self):
        with StubJellyfin() as stub:
            with connected(stub.url) as app:
                self.assertTrue(jellyfin.refresh_library(app.cfg, conn=app.conn))

        self.assertEqual(stub.seen[0]["path"], "/Library/Refresh")
        self.assertEqual(stub.seen[0]["method"], "POST")

    def test_a_server_error_is_logged_not_raised(self):
        with StubJellyfin(status=500) as stub:
            with connected(stub.url) as app:
                self.assertFalse(jellyfin.refresh_library(app.cfg, conn=app.conn))
                self.assertTrue(any("refresh failed" in message
                                    for message in app.events(level="warn")))

    def test_an_unreachable_server_is_logged_not_raised(self):
        with connected("http://127.0.0.1:1") as app:
            self.assertFalse(jellyfin.refresh_library(app.cfg, conn=app.conn))

    def test_an_unconfigured_jellyfin_is_a_no_op(self):
        with harness.temp_app() as app:
            self.assertFalse(jellyfin.refresh_library(app.cfg, conn=app.conn))


class CycleTest(unittest.TestCase):

    def setUp(self):
        LIBRARY_CHANGED.clear()

    def tearDown(self):
        LIBRARY_CHANGED.clear()

    def test_one_refresh_after_a_cycle_that_changed_files(self):
        with StubJellyfin() as stub:
            with connected(stub.url) as app:
                sched = scheduler.Scheduler(server.App(app.cfg, app.conn))

                LIBRARY_CHANGED.set()      # two downloads landed
                self.assertTrue(sched.maybe_refresh_library())

        self.assertEqual(len(stub.seen), 1)

    def test_no_refresh_after_an_idle_cycle(self):
        with StubJellyfin() as stub:
            with connected(stub.url) as app:
                sched = scheduler.Scheduler(server.App(app.cfg, app.conn))
                self.assertFalse(sched.maybe_refresh_library())

        self.assertEqual(stub.seen, [])

    def test_the_flag_is_cleared_so_the_next_cycle_stays_quiet(self):
        with StubJellyfin() as stub:
            with connected(stub.url) as app:
                sched = scheduler.Scheduler(server.App(app.cfg, app.conn))
                LIBRARY_CHANGED.set()
                sched.maybe_refresh_library()
                sched.maybe_refresh_library()

        self.assertEqual(len(stub.seen), 1)

    def test_a_failed_refresh_still_clears_the_flag(self):
        # Otherwise a Jellyfin that is down would make every later cycle
        # retry forever.
        with StubJellyfin(status=500) as stub:
            with connected(stub.url) as app:
                sched = scheduler.Scheduler(server.App(app.cfg, app.conn))
                LIBRARY_CHANGED.set()
                sched.maybe_refresh_library()
                self.assertFalse(LIBRARY_CHANGED.is_set())

    def test_a_deletion_also_earns_a_refresh(self):
        with StubJellyfin() as stub:
            with connected(stub.url) as app:
                chan = app.add_channel(channel_id="UC_a", title="Alpha",
                                       url="https://www.youtube.com/channel/UC_a",
                                       folder="Alpha")
                path = app.make_file("Alpha/a [dQw4w9WgXcQ].mp4", size=100)
                app.add_video("dQw4w9WgXcQ", chan, "A video")
                row = db.get_video_by_ytid(app.conn, "dQw4w9WgXcQ")
                db.set_video_status(app.conn, row["id"], db.DOWNLOADED,
                                    file_path=path, file_size=100)

                from tuner import retention
                LIBRARY_CHANGED.clear()
                retention.delete_one(app.conn, app.cfg, row["id"])
                self.assertTrue(LIBRARY_CHANGED.is_set())

                sched = scheduler.Scheduler(server.App(app.cfg, app.conn))
                self.assertTrue(sched.maybe_refresh_library())


if __name__ == "__main__":
    unittest.main()
