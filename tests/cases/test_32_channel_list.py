"""Task 8.3 -- the channel list and its inline controls."""

import contextlib
import json
import threading
import unittest
import urllib.error
import urllib.request

import harness
from tuner import db, server, web


def seeded(app):
    row_id = app.add_channel(channel_id="UC_a", title="Alpha Channel",
                             url="https://www.youtube.com/channel/UC_a",
                             folder="Alpha Channel")
    app.add_video("vidaaaaaaaa", row_id, "One")
    return row_id


class MarkupTest(unittest.TestCase):

    def setUp(self):
        with harness.temp_app() as app:
            self.markup = web.render(app.cfg)

    def test_the_list_and_its_empty_state_exist(self):
        self.assertIn('id="channels"', self.markup)
        self.assertIn('id="channels-empty"', self.markup)
        self.assertIn('id="channels-count"', self.markup)

    def test_the_card_is_built_from_state_fields_only(self):
        script = self.markup.split("<script>")[1]
        for field in ("channel.title", "channel.folder", "channel.keep_n",
                      "channel.skip_shorts", "channel.skip_live",
                      "channel.effective", "channel.last_checked_at",
                      "channel.last_error", "channel.enabled", "channel.bytes"):
            self.assertIn(field, script)

    def test_removal_asks_before_deleting_files(self):
        script = self.markup.split("<script>")[1]
        self.assertIn("window.confirm", script)
        self.assertIn("delete_files=1", script)

    def test_the_list_is_not_redrawn_while_a_control_has_focus(self):
        script = self.markup.split("<script>")[1]
        self.assertIn("activeElement", script)


class PatchTest(unittest.TestCase):

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

    def patch(self, port, row_id, payload):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/channels/%d" % (port, row_id),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="PATCH")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_only_the_supplied_field_changes(self):
        with harness.temp_app() as app:
            row_id = seeded(app)
            db.update_channel(app.conn, row_id, keep_n=7)

            with self.running(app) as port:
                status, body = self.patch(port, row_id, {"enabled": False})

            self.assertEqual(status, 200)
            row = db.get_channel(app.conn, row_id)
            self.assertEqual(row["enabled"], 0)
            self.assertEqual(row["keep_n"], 7)          # untouched
            self.assertIsNone(row["skip_shorts"])       # still inheriting

    def test_an_inherit_value_is_left_alone_when_not_sent(self):
        with harness.temp_app() as app:
            row_id = seeded(app)
            with self.running(app) as port:
                self.patch(port, row_id, {"keep_n": 3})

            row = db.get_channel(app.conn, row_id)
            self.assertEqual(row["keep_n"], 3)
            self.assertIsNone(row["skip_shorts"])
            self.assertIsNone(row["skip_live"])

    def test_null_puts_a_filter_back_to_inherit(self):
        with harness.temp_app() as app:
            row_id = seeded(app)
            db.update_channel(app.conn, row_id, skip_shorts=0)

            with self.running(app) as port:
                self.patch(port, row_id, {"skip_shorts": None})

            self.assertIsNone(db.get_channel(app.conn, row_id)["skip_shorts"])

    def test_null_keep_n_returns_to_inherit(self):
        with harness.temp_app(keep_n=5) as app:
            row_id = seeded(app)
            db.update_channel(app.conn, row_id, keep_n=2)

            with self.running(app) as port:
                _, body = self.patch(port, row_id, {"keep_n": None})

            self.assertIsNone(db.get_channel(app.conn, row_id)["keep_n"])
            self.assertEqual(body["channel"]["effective"]["keep_n"], 5)

    def test_booleans_are_accepted_in_the_shapes_the_page_sends(self):
        with harness.temp_app() as app:
            row_id = seeded(app)
            with self.running(app) as port:
                for value, expected in ((True, 1), (False, 0), ("1", 1),
                                        ("true", 1), (0, 0)):
                    self.patch(port, row_id, {"skip_live": value})
                    self.assertEqual(
                        db.get_channel(app.conn, row_id)["skip_live"], expected)

    def test_the_reply_carries_the_refreshed_channel(self):
        with harness.temp_app(keep_n=5) as app:
            row_id = seeded(app)
            with self.running(app) as port:
                _, body = self.patch(port, row_id, {"keep_n": 2})

            self.assertTrue(body["ok"])
            self.assertEqual(body["channel"]["keep_n"], 2)
            self.assertEqual(body["channel"]["effective"]["keep_n"], 2)
            self.assertIn("counts", body["channel"])

    def test_bad_values_are_refused(self):
        with harness.temp_app() as app:
            row_id = seeded(app)
            with self.running(app) as port:
                self.assertEqual(
                    self.patch(port, row_id, {"keep_n": "many"})[0], 400)
                self.assertEqual(
                    self.patch(port, row_id, {"keep_n": -1})[0], 400)
                self.assertEqual(
                    self.patch(port, row_id, {"enabled": None})[0], 400)
                self.assertEqual(self.patch(port, row_id, {})[0], 400)

    def test_an_unknown_channel_is_404(self):
        with harness.temp_app() as app:
            with self.running(app) as port:
                self.assertEqual(self.patch(port, 9999, {"keep_n": 1})[0], 404)

    def test_a_disabled_channel_stops_being_polled(self):
        with harness.temp_app() as app:
            row_id = seeded(app)
            with self.running(app) as port:
                self.patch(port, row_id, {"enabled": False})

            self.assertEqual(db.list_channels(app.conn, enabled_only=True), [])


if __name__ == "__main__":
    unittest.main()
