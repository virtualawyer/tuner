"""Task 8.2 -- the add form and the per-URL results it renders."""

import contextlib
import json
import threading
import unittest
import urllib.error
import urllib.request

import harness
from tuner import db, feed, server, web

CHANNEL = "UCabcdefghijklmnopqrstuv"


class FakeResponse(object):

    def __init__(self, body):
        self.body = body.encode("utf-8")
        self.status = 200
        self.headers = {}

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


class MarkupTest(unittest.TestCase):

    def setUp(self):
        with harness.temp_app() as app:
            self.markup = web.render(app.cfg)

    def test_both_input_paths_exist(self):
        for element_id in ("add-url", "add-btn", "many-toggle", "many-wrap",
                           "add-many", "add-many-btn", "many-cancel",
                           "add-results"):
            self.assertIn('id="%s"' % element_id, self.markup)

    def test_the_paste_many_box_starts_hidden(self):
        self.assertIn('id="many-wrap" class="hidden"', self.markup)

    def test_the_placeholder_shows_the_accepted_forms(self):
        self.assertIn("@handle", self.markup)
        self.assertIn("/channel/UC", self.markup)

    def test_results_are_written_as_text_not_markup(self):
        # A channel title is data. It must never be interpolated as HTML.
        script = self.markup.split("<script>")[1]
        self.assertIn("textContent", script)
        self.assertNotIn("innerHTML", script)


class EndpointTest(unittest.TestCase):

    @contextlib.contextmanager
    def running(self, app):
        httpd = server.make_server(server.App(app.cfg, app.conn),
                                   host="127.0.0.1", port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            httpd.server_close()

    def post(self, port, payload):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/channels" % port,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_three_urls_one_good_one_duplicate_one_rejected(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(harness.fixture("feed_sample.xml")):
                with self.running(app) as port:
                    status, body = self.post(port, {"urls": [
                        "@alpha",
                        "https://www.youtube.com/channel/" + CHANNEL,
                        "https://vimeo.com/12345",
                    ]})

            self.assertEqual(status, 200)
            self.assertEqual(body["added"], 1)
            self.assertEqual(len(body["results"]), 3)

            first, duplicate, rejected = body["results"]
            self.assertTrue(first["ok"])
            self.assertIn("Alpha Channel", first["message"])

            self.assertFalse(duplicate["ok"])
            self.assertIn("already tracked", duplicate["message"])

            self.assertFalse(rejected["ok"])
            self.assertIn("not a YouTube address", rejected["message"])

    def test_every_result_carries_a_readable_message(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["--print"], "returncode": 1,
                           "stderr": "ERROR: Unable to recognize tab page"}])
            with self.running(app) as port:
                _, body = self.post(port, {"urls": ["@ghost", "", "   "]})

            self.assertEqual(len(body["results"]), 1)   # blanks dropped
            self.assertFalse(body["results"][0]["ok"])
            self.assertTrue(body["results"][0]["message"])

    def test_the_success_message_names_what_was_queued(self):
        with harness.temp_app(seed_n=3) as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(harness.fixture("feed_sample.xml")):
                with self.running(app) as port:
                    _, body = self.post(port, {"url": "@alpha"})

            message = body["results"][0]["message"]
            self.assertIn("3 queued", message)
            self.assertIn("12 older skipped", message)

    def test_a_paste_of_the_same_line_twice_is_reported_once_as_duplicate(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(harness.fixture("feed_sample.xml")):
                with self.running(app) as port:
                    _, body = self.post(port, {"urls": ["@alpha", "@alpha"]})

            self.assertEqual(body["added"], 1)
            self.assertEqual(body["results"][1]["message"], "duplicate line")
            self.assertEqual(len(db.list_channels(app.conn)), 1)

    def test_a_huge_paste_is_refused(self):
        with harness.temp_app() as app:
            with self.running(app) as port:
                status, body = self.post(port, {"urls": ["@x"] * 201})
            self.assertEqual(status, 400)
            self.assertIn("too many", body["error"])


if __name__ == "__main__":
    unittest.main()
