"""Task 9.1 -- demo mode seeds sample data and touches nothing real."""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from tuner import config, db, demo, server, worker


class BuildTest(unittest.TestCase):

    def setUp(self):
        self.cfg = config.defaults()
        self.conn = demo.build(self.cfg)

    def tearDown(self):
        self.conn.close()
        demo.cleanup(self.cfg)
        worker.set_current(None)

    def test_everything_lands_in_a_temp_directory(self):
        root = self.cfg["_demo_root"]
        self.assertTrue(root.startswith(tempfile.gettempdir()))
        self.assertTrue(self.cfg["library_root"].startswith(root))
        self.assertTrue(self.cfg["db_path"].startswith(root))
        # The shipped defaults must not be touched.
        self.assertNotEqual(self.cfg["library_root"], "/srv/media/youtube")
        self.assertNotEqual(self.cfg["db_path"], "/var/lib/tuner/tuner.db")

    def test_four_channels_are_seeded(self):
        channels = db.list_channels(self.conn)
        self.assertEqual(len(channels), 4)
        titles = sorted(row["title"] for row in channels)
        self.assertIn("Bench Notes", titles)
        self.assertIn("Quiet Channel", titles)

    def test_roughly_forty_videos_across_every_status(self):
        videos = db.list_videos(self.conn)
        self.assertGreaterEqual(len(videos), 30)

        seen = set(row["status"] for row in videos)
        for status in (db.DOWNLOADED, db.QUEUED, db.DEFERRED, db.SKIPPED,
                       db.FAILED, db.MISSING, db.DELETED):
            self.assertIn(status, seen)

    def test_a_channel_with_nothing_yet_is_included(self):
        quiet = db.get_channel_by_ytid(self.conn, "UCdemoquietchannel000001")
        self.assertEqual(len(db.list_videos(self.conn,
                                            channel_row_id=quiet["id"])), 0)
        self.assertEqual(quiet["enabled"], 0)

    def test_a_channel_carries_an_error_and_an_override(self):
        broken = db.get_channel_by_ytid(self.conn, "UCdemohomelabdiaries0001")
        self.assertIn("404", broken["last_error"])

        overridden = db.get_channel_by_ytid(self.conn,
                                            "UCdemosmallsystems000001")
        self.assertEqual(overridden["keep_n"], 3)
        self.assertEqual(overridden["skip_shorts"], 0)

    def test_downloaded_rows_have_real_files_and_sizes(self):
        for row in db.list_videos(self.conn, status=db.DOWNLOADED):
            self.assertTrue(os.path.exists(row["file_path"]))
            self.assertTrue(row["file_path"].startswith(self.cfg["_demo_root"]))
            self.assertGreater(row["file_size"], 0)

    def test_the_synthetic_drive_looks_like_the_real_one(self):
        usage = demo.disk_usage(self.cfg)
        self.assertTrue(usage["ok"])
        self.assertGreater(usage["total"], 2_000 * (1024 ** 3))
        self.assertGreater(usage["free"], 0)
        self.assertLess(usage["percent"], 100)

    def test_the_fake_download_advances_and_wraps(self):
        demo.tick()
        first = worker.current()["percent"]
        demo.tick()
        second = worker.current()["percent"]
        self.assertGreater(second, first)

        for _ in range(200):
            demo.tick()
            self.assertLessEqual(worker.current()["percent"], 100)
            self.assertGreaterEqual(worker.current()["percent"], 0)

    def test_cleanup_removes_the_whole_tree(self):
        root = self.cfg["_demo_root"]
        self.conn.close()
        demo.cleanup(self.cfg)
        self.assertFalse(os.path.exists(root))
        # Guard the tearDown that follows.
        self.conn = db.connect(":memory:")
        self.cfg["_demo_root"] = None


class ServeTest(unittest.TestCase):

    def test_demo_serves_populated_state_and_starts_no_threads(self):
        cfg = config.defaults()
        cfg["demo"] = True
        cfg["host"] = "127.0.0.1"
        cfg["port"] = 0

        app = server.build_app(cfg)
        try:
            self.assertIsNone(app.scheduler)
            self.assertIsNone(app.worker)

            httpd = server.make_server(app, host="127.0.0.1", port=0)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            port = httpd.server_address[1]
            try:
                url = "http://127.0.0.1:%d/api/state" % port
                with urllib.request.urlopen(url, timeout=5) as response:
                    state = json.loads(response.read().decode())

                self.assertEqual(len(state["channels"]), 4)
                self.assertTrue(state["recent"])
                self.assertTrue(state["activity"]["queue"])
                self.assertIsNotNone(state["activity"]["current"])
                self.assertTrue(state["disk"]["ok"])

                # The fake download moves between polls.
                first = state["activity"]["current"]["percent"]
                with urllib.request.urlopen(url, timeout=5) as response:
                    second = json.loads(response.read().decode())
                self.assertNotEqual(second["activity"]["current"]["percent"],
                                    first)

                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/" % port, timeout=5) as response:
                    self.assertEqual(response.status, 200)
            finally:
                httpd.shutdown()
                httpd.server_close()
        finally:
            app.conn.close()
            demo.cleanup(cfg)
            worker.set_current(None)

    def test_the_state_and_the_actions_never_reach_yt_dlp(self):
        cfg = config.defaults()
        cfg["demo"] = True
        cfg["ytdlp_path"] = "/nonexistent/yt-dlp-must-not-run"
        app = server.build_app(cfg)
        try:
            server._VERSION_CACHE["checked_at"] = 0.0
            state = server.build_state(app)
            self.assertEqual(state["ytdlp_version"], demo.YTDLP_VERSION)
            self.assertEqual(server._VERSION_CACHE["checked_at"], 0.0)

            httpd = server.make_server(app, host="127.0.0.1", port=0)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            port = httpd.server_address[1]
            try:
                for method, path in (("POST", "/api/ytdlp/update"),
                                     ("POST", "/api/channels"),
                                     ("POST", "/api/settings"),
                                     ("PATCH", "/api/channels/1"),
                                     ("DELETE", "/api/channels/1")):
                    request = urllib.request.Request(
                        "http://127.0.0.1:%d%s" % (port, path),
                        data=None if method == "DELETE" else b'{"urls": "@x"}',
                        headers={"Content-Type": "application/json"},
                        method=method)
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(caught.exception.code, 403, path)
            finally:
                httpd.shutdown()
                httpd.server_close()
            self.assertEqual(len(state["channels"]), 4)
        finally:
            app.conn.close()
            demo.cleanup(cfg)
            worker.set_current(None)

    def test_demo_runs_no_subprocess_and_no_request(self):
        # The one thing demo mode must guarantee: nothing real is touched.
        # Checked against imports and calls, not prose -- the module's own
        # docstring says the word "subprocess".
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "tuner", "demo.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()

        import ast
        tree = ast.parse(source)

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
                if node.module:
                    imported.add(node.module.split(".")[0])

        for forbidden in ("subprocess", "urllib", "http", "socket", "ytdlp",
                          "jellyfin", "feed", "discover", "scheduler"):
            self.assertNotIn(forbidden, imported,
                             "demo.py must not import %s" % forbidden)


if __name__ == "__main__":
    unittest.main()
