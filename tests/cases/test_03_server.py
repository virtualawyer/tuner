"""Task 1.4 -- server routing, the state payload, and the page shell."""

import json
import os
import re
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from tuner import config, db, server, web


class ServerTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tuner-srv-")
        cfg = config.defaults()
        cfg["library_root"] = os.path.join(cls.tmp, "library")
        cfg["db_path"] = os.path.join(cls.tmp, "state", "tuner.db")
        cfg["title"] = "Den Tuner"
        os.makedirs(cfg["library_root"])

        cls.cfg = cfg
        cls.app = server.build_app(cfg)
        cls.httpd = server.make_server(cls.app, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.app.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode("utf-8")

    def test_state_carries_every_documented_key(self):
        status, body = self.get("/api/state")
        self.assertEqual(status, 200)
        state = json.loads(body)

        for key in ("version", "title", "now", "disk", "counts", "channels",
                    "activity", "events"):
            self.assertIn(key, state)
        for key in ("total", "used", "free", "percent"):
            self.assertIn(key, state["disk"])
            self.assertIsInstance(state["disk"][key], int)
        for key in ("channels", "downloaded", "queued", "failed"):
            self.assertIn(key, state["counts"])
        self.assertIn("current", state["activity"])
        self.assertIn("queue", state["activity"])
        self.assertEqual(state["title"], "Den Tuner")

    def test_state_reflects_the_database(self):
        conn = self.app.conn
        chan = db.add_channel(conn, "UC_state", "State Channel",
                              "https://youtube.com/@state", "State Channel")
        db.add_video(conn, "vstate0001", chan, "One")
        db.set_video_status(conn, 1, db.DOWNLOADED, file_size=10)

        state = json.loads(self.get("/api/state")[1])
        self.assertEqual(state["counts"]["channels"], 1)
        self.assertEqual(state["counts"]["downloaded"], 1)
        self.assertEqual(state["channels"][0]["title"], "State Channel")
        self.assertTrue(state["channels"][0]["enabled"])

        db.delete_channel(conn, chan)

    def test_page_renders_with_no_placeholder_left(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Den Tuner", body)
        self.assertNotIn("{{", body)
        self.assertNotIn("__BRAND__", body)
        self.assertNotIn("__STYLE__", body)
        self.assertNotIn("__SCRIPT__", body)
        self.assertNotIn("__LIBRARY__", body)

    def test_page_uses_tokens_and_hides_hex_outside_root(self):
        markup = web.render(self.cfg)
        for token in ("--bg", "--card", "--border", "--text", "--muted",
                      "--accent", "--track", "--green", "--red", "--amber",
                      "--shadow"):
            self.assertIn(token, markup)

        # Every literal color belongs in a :root block. Remove those blocks
        # and no hex value may remain anywhere in the stylesheet.
        style = markup.split("<style>")[1].split("</style>")[0]
        without_root = re.sub(r":root\{[^}]*\}", "", style)
        leftover = re.findall(r"#[0-9a-fA-F]{3,8}\b", without_root)
        self.assertEqual(leftover, [],
                         "hex colors outside :root: %s" % leftover)

    def test_page_makes_no_external_requests(self):
        # Example URLs in placeholder text are fine; what must not exist is
        # anything that actually fetches from another host.
        markup = web.render(self.cfg)
        for construct in ("<link", "<script src", "@import", "url(http",
                          'src="http', 'href="http', "jsdelivr", "unpkg"):
            self.assertNotIn(construct, markup)

    def test_unknown_route_is_json_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/nope")
        self.assertEqual(caught.exception.code, 404)
        payload = json.loads(caught.exception.read().decode("utf-8"))
        self.assertFalse(payload["ok"])

    def test_trailing_slash_and_query_are_tolerated(self):
        self.assertEqual(self.get("/api/state/")[0], 200)
        self.assertEqual(self.get("/api/state?t=1")[0], 200)


class DiskUsageTest(unittest.TestCase):

    def test_real_path_reports_integers(self):
        usage = server.disk_usage(tempfile.gettempdir())
        self.assertTrue(usage["ok"])
        self.assertGreater(usage["total"], 0)
        self.assertIsInstance(usage["free"], int)
        self.assertLessEqual(usage["percent"], 100)

    def test_missing_path_reports_zeros_not_an_exception(self):
        usage = server.disk_usage("/definitely/not/mounted/anywhere")
        self.assertFalse(usage["ok"])
        self.assertEqual(usage["total"], 0)
        self.assertEqual(usage["percent"], 0)


if __name__ == "__main__":
    unittest.main()
