"""Task 3.4 -- adding channels, seeding, and the POST endpoint."""

import contextlib
import json
import threading
import unittest
import urllib.error
import urllib.request

import harness
from tuner import db, discover, feed, server

CHANNEL = "UCabcdefghijklmnopqrstuv"


class FakeResponse(object):

    def __init__(self, body, status=200, headers=None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = headers or {}

    def read(self):
        return self.body

    def getcode(self):
        return self.status

    def close(self):
        pass


@contextlib.contextmanager
def serving(body):
    def opener(request, timeout=None):
        return FakeResponse(body)

    previous = feed.OPENER
    feed.OPENER = opener
    try:
        yield
    finally:
        feed.OPENER = previous


class AddChannelTest(unittest.TestCase):

    def setUp(self):
        self.sample = harness.fixture("feed_sample.xml")

    def test_adding_a_handle_resolves_seeds_and_reports(self):
        with harness.temp_app(seed_n=3) as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(self.sample):
                result = discover.add_channel(app.conn, app.cfg, "@alpha")

            self.assertTrue(result["ok"], result["message"])
            self.assertEqual(result["channel"], "Alpha Channel")

            row = db.get_channel_by_ytid(app.conn, CHANNEL)
            self.assertEqual(row["folder"], "Alpha Channel")
            self.assertEqual(row["url"],
                             "https://www.youtube.com/channel/" + CHANNEL)

    def test_seeding_keeps_only_the_newest_n(self):
        with harness.temp_app(seed_n=3) as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(self.sample):
                discover.add_channel(app.conn, app.cfg, "@alpha")

            eligible = db.list_videos(app.conn, status=db.NEW)
            skipped = db.list_videos(app.conn, status=db.SKIPPED)

            self.assertEqual(len(eligible), 3)
            self.assertEqual(len(skipped), 12)
            self.assertTrue(all(v["skip_reason"] == "pre-existing"
                                for v in skipped))
            # The three kept are the three newest in the feed.
            self.assertEqual(sorted(v["video_id"] for v in eligible),
                             ["vid00000001", "vid00000002", "vid00000003"])

    def test_seed_zero_keeps_nothing(self):
        with harness.temp_app(seed_n=0) as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(self.sample):
                discover.add_channel(app.conn, app.cfg, "@alpha")

            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)), 0)
            self.assertEqual(len(db.list_videos(app.conn, status=db.SKIPPED)), 15)

    def test_seed_larger_than_the_feed_keeps_everything(self):
        with harness.temp_app(seed_n=50) as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(self.sample):
                discover.add_channel(app.conn, app.cfg, "@alpha")

            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)), 15)

    def test_adding_the_same_channel_twice_does_not_duplicate(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(self.sample):
                first = discover.add_channel(app.conn, app.cfg, "@alpha")
                second = discover.add_channel(app.conn, app.cfg,
                                              "youtube.com/channel/" + CHANNEL)

            self.assertTrue(first["ok"])
            self.assertFalse(second["ok"])
            self.assertIn("already tracked", second["message"])
            self.assertEqual(len(db.list_channels(app.conn)), 1)

    def test_a_channel_id_url_needs_no_binary_and_takes_its_title_from_the_feed(self):
        with harness.temp_app() as app:
            app.scenario([])   # any yt-dlp call would fail
            with serving(self.sample):
                result = discover.add_channel(
                    app.conn, app.cfg,
                    "https://www.youtube.com/channel/" + CHANNEL)

            self.assertTrue(result["ok"], result["message"])
            self.assertEqual(app.calls(), [])
            self.assertEqual(db.get_channel_by_ytid(app.conn, CHANNEL)["title"],
                             "Alpha Channel")

    def test_a_title_with_forbidden_characters_becomes_a_safe_folder(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(CHANNEL, 'Tech/Talk: "Live?"')])
            with serving(self.sample):
                discover.add_channel(app.conn, app.cfg, "@tech")

            folder = db.get_channel_by_ytid(app.conn, CHANNEL)["folder"]
            for bad in '"*/:<>?\\|':
                self.assertNotIn(bad, folder)

    def test_an_unresolvable_url_reports_without_raising(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["--print"], "returncode": 1,
                           "stderr": "ERROR: Unable to recognize tab page"}])
            result = discover.add_channel(app.conn, app.cfg, "@ghost")

            self.assertFalse(result["ok"])
            self.assertIn("could not resolve", result["message"])
            self.assertEqual(len(db.list_channels(app.conn)), 0)

    def test_a_non_youtube_url_is_refused_before_any_subprocess(self):
        with harness.temp_app() as app:
            result = discover.add_channel(app.conn, app.cfg,
                                          "https://vimeo.com/12345")
            self.assertFalse(result["ok"])
            self.assertEqual(app.calls(), [])

    def test_add_channels_reports_one_result_per_line(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(self.sample):
                results = discover.add_channels(app.conn, app.cfg, [
                    "@alpha",
                    "https://vimeo.com/12345",
                    "@alpha",
                    "   ",
                ])

            self.assertEqual(len(results), 3)          # blank line dropped
            self.assertTrue(results[0]["ok"])
            self.assertFalse(results[1]["ok"])
            self.assertFalse(results[2]["ok"])
            self.assertEqual(results[2]["message"], "duplicate line")


class AddChannelEndpointTest(unittest.TestCase):

    @contextlib.contextmanager
    def running(self, app):
        httpd = server.make_server(server.App(app.cfg, app.conn),
                                   host="127.0.0.1", port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            httpd.server_close()

    def post(self, port, path, payload):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_single_url_form(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(harness.fixture("feed_sample.xml")):
                with self.running(app) as port:
                    status, body = self.post(port, "/api/channels",
                                             {"url": "@alpha"})

            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["added"], 1)
            self.assertEqual(len(body["results"]), 1)

    def test_paste_many_form_reports_each_line(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(harness.fixture("feed_sample.xml")):
                with self.running(app) as port:
                    status, body = self.post(port, "/api/channels", {
                        "urls": ["@alpha", "https://vimeo.com/1",
                                 "https://www.youtube.com/channel/" + CHANNEL],
                    })

            self.assertEqual(status, 200)
            self.assertEqual(body["added"], 1)
            flags = [item["ok"] for item in body["results"]]
            self.assertEqual(flags, [True, False, False])
            self.assertIn("not a YouTube address", body["results"][1]["message"])
            self.assertIn("already tracked", body["results"][2]["message"])

    def test_empty_and_malformed_bodies_are_rejected(self):
        with harness.temp_app() as app:
            with self.running(app) as port:
                self.assertEqual(self.post(port, "/api/channels", {})[0], 400)
                self.assertEqual(
                    self.post(port, "/api/channels", {"urls": []})[0], 400)


if __name__ == "__main__":
    unittest.main()
