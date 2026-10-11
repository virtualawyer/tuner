"""Download the last X episodes, and keep X of them.

Two numbers per channel: how far back to reach, and how many to hold.
Both inherit the global setting when left blank. Raising the first one on
an existing channel pulls back what the seed cutoff skipped.
"""

import contextlib
import json
import threading
import unittest
import urllib.error
import urllib.request

import harness
from tuner import config, db, discover, feed, filters, server, web

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


def added(app, seed_n=None, keep_n=None):
    app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
    with serving(harness.fixture("feed_sample.xml")):
        discover.add_channel(app.conn, app.cfg, "@alpha", seed_n=seed_n,
                             keep_n=keep_n)
    return db.get_channel_by_ytid(app.conn, CHANNEL)


class SchemaTest(unittest.TestCase):

    def test_seed_n_inherits_like_keep_n(self):
        with harness.temp_app(seed_n=3, keep_n=5) as app:
            row_id = app.add_channel(channel_id="UC_a", title="A")
            row = db.get_channel(app.conn, row_id)

            resolved = filters.settings_for(app.cfg, row)
            self.assertEqual(resolved["seed_n"], 3)
            self.assertEqual(resolved["keep_n"], 5)
            self.assertIsNone(row["seed_n"])

    def test_a_per_channel_value_wins(self):
        with harness.temp_app(seed_n=3) as app:
            row_id = app.add_channel(channel_id="UC_a", title="A", seed_n=10)
            row = db.get_channel(app.conn, row_id)
            self.assertEqual(filters.settings_for(app.cfg, row)["seed_n"], 10)

    def test_an_existing_database_gains_the_column(self):
        # A live box upgrades in place; CREATE TABLE IF NOT EXISTS would do
        # nothing for it.
        conn = db.connect(":memory:")
        conn.execute("""CREATE TABLE channels (
            id INTEGER PRIMARY KEY, channel_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL, url TEXT NOT NULL, folder TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, keep_n INTEGER,
            skip_shorts INTEGER, skip_live INTEGER, added_at TEXT NOT NULL,
            last_checked_at TEXT, last_error TEXT, etag TEXT,
            last_modified TEXT)""")
        conn.commit()

        db.init_schema(conn)

        columns = set(r["name"] for r in
                      conn.execute("PRAGMA table_info(channels)"))
        self.assertIn("seed_n", columns)
        db.init_schema(conn)   # and again, harmlessly
        conn.close()


class SeedOnAddTest(unittest.TestCase):

    def test_the_add_honours_a_per_channel_seed(self):
        with harness.temp_app(seed_n=3) as app:
            added(app, seed_n=6)
            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)), 6)
            self.assertEqual(len(db.list_videos(app.conn, status=db.SKIPPED)), 9)

    def test_without_one_it_falls_back_to_the_global(self):
        with harness.temp_app(seed_n=2) as app:
            added(app)
            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)), 2)

    def test_a_keep_override_is_stored_too(self):
        with harness.temp_app(keep_n=5) as app:
            row = added(app, keep_n=2)
            self.assertEqual(row["keep_n"], 2)
            self.assertEqual(filters.settings_for(app.cfg, row)["keep_n"], 2)


class BackfillTest(unittest.TestCase):

    def test_raising_it_pulls_back_what_the_cutoff_skipped(self):
        with harness.temp_app(seed_n=3) as app:
            row = added(app)
            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)), 3)

            db.update_channel(app.conn, row["id"], seed_n=8)
            row = db.get_channel(app.conn, row["id"])
            pulled = discover.backfill(app.conn, app.cfg, row)

            self.assertEqual(pulled, 5)
            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)), 8)

    def test_it_reaches_for_the_newest_ones(self):
        with harness.temp_app(seed_n=3) as app:
            row = added(app)
            db.update_channel(app.conn, row["id"], seed_n=5)
            discover.backfill(app.conn, app.cfg,
                              db.get_channel(app.conn, row["id"]))

            eligible = sorted(v["video_id"] for v in
                              db.list_videos(app.conn, status=db.NEW))
            self.assertEqual(eligible, ["vid00000001", "vid00000002",
                                        "vid00000003", "vid00000004",
                                        "vid00000005"])

    def test_a_filtered_video_stays_filtered(self):
        # Only the seed cutoff is undone. A Short was skipped because a
        # filter said so, and asking for more back-catalogue does not
        # overrule that.
        with harness.temp_app(seed_n=3) as app:
            row = added(app)
            victim = db.get_video_by_ytid(app.conn, "vid00000005")
            db.set_video_status(app.conn, victim["id"], db.SKIPPED,
                                skip_reason="short")

            db.update_channel(app.conn, row["id"], seed_n=10)
            discover.backfill(app.conn, app.cfg,
                              db.get_channel(app.conn, row["id"]))

            after = db.get_video_by_ytid(app.conn, "vid00000005")
            self.assertEqual(after["status"], db.SKIPPED)
            self.assertEqual(after["skip_reason"], "short")

    def test_a_downloaded_or_deleted_video_is_left_alone(self):
        with harness.temp_app(seed_n=3) as app:
            row = added(app)
            keeper = db.get_video_by_ytid(app.conn, "vid00000001")
            gone = db.get_video_by_ytid(app.conn, "vid00000002")
            db.set_video_status(app.conn, keeper["id"], db.DOWNLOADED,
                                file_size=10)
            db.set_video_status(app.conn, gone["id"], db.DELETED)

            db.update_channel(app.conn, row["id"], seed_n=10)
            discover.backfill(app.conn, app.cfg,
                              db.get_channel(app.conn, row["id"]))

            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000001")["status"],
                db.DOWNLOADED)
            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000002")["status"],
                db.DELETED)

    def test_lowering_it_deletes_nothing(self):
        # Reaching less far back is not an instruction to throw anything
        # away. That is what keep-N is for.
        with harness.temp_app(seed_n=10) as app:
            row = added(app)
            before = len(db.list_videos(app.conn, status=db.NEW))

            db.update_channel(app.conn, row["id"], seed_n=2)
            discover.backfill(app.conn, app.cfg,
                              db.get_channel(app.conn, row["id"]))

            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)),
                             before)

    def test_zero_pulls_nothing(self):
        with harness.temp_app(seed_n=3) as app:
            row = added(app)
            db.update_channel(app.conn, row["id"], seed_n=0)
            self.assertEqual(
                discover.backfill(app.conn, app.cfg,
                                  db.get_channel(app.conn, row["id"])), 0)


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

    def send(self, port, path, payload, method="POST"):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_the_add_endpoint_takes_both_numbers(self):
        with harness.temp_app(seed_n=3, keep_n=5) as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            with serving(harness.fixture("feed_sample.xml")):
                with self.running(app) as port:
                    status, body = self.send(port, "/api/channels", {
                        "url": "@alpha", "seed_n": "7", "keep_n": "2"})

            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            row = db.get_channel_by_ytid(app.conn, CHANNEL)
            self.assertEqual(row["seed_n"], 7)
            self.assertEqual(row["keep_n"], 2)
            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)), 7)

    def test_patching_seed_n_pulls_videos_back_and_says_how_many(self):
        with harness.temp_app(seed_n=3) as app:
            row = added(app)
            with self.running(app) as port:
                status, body = self.send(port, "/api/channels/%d" % row["id"],
                                         {"seed_n": 9}, method="PATCH")

            self.assertEqual(status, 200)
            self.assertEqual(body["pulled_back"], 6)
            self.assertEqual(body["channel"]["seed_n"], 9)
            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)), 9)

    def test_blanking_it_returns_to_inherit(self):
        with harness.temp_app(seed_n=4) as app:
            row = added(app, seed_n=9)
            with self.running(app) as port:
                _, body = self.send(port, "/api/channels/%d" % row["id"],
                                    {"seed_n": None}, method="PATCH")

            self.assertIsNone(db.get_channel(app.conn, row["id"])["seed_n"])
            self.assertEqual(body["channel"]["effective"]["seed_n"], 4)

    def test_a_bad_value_is_refused(self):
        with harness.temp_app() as app:
            row = added(app)
            with self.running(app) as port:
                self.assertEqual(
                    self.send(port, "/api/channels/%d" % row["id"],
                              {"seed_n": "many"}, method="PATCH")[0], 400)
                self.assertEqual(
                    self.send(port, "/api/channels/%d" % row["id"],
                              {"seed_n": -3}, method="PATCH")[0], 400)


class UiTest(unittest.TestCase):

    def setUp(self):
        with harness.temp_app() as app:
            self.markup = web.render(app.cfg)

    def test_the_add_form_offers_both_numbers(self):
        self.assertIn('id="add-seed"', self.markup)
        self.assertIn('id="add-keep"', self.markup)
        self.assertIn("Download the last", self.markup)
        self.assertIn("keep", self.markup)

    def test_a_channel_row_offers_both_too(self):
        script = self.markup.split("<script>")[1]
        self.assertIn("Download last", script)
        self.assertIn("seed_n:seed.value", script.replace(" ", ""))
        self.assertIn("channel.effective.seed_n", script)

    def test_the_add_form_shows_the_inherited_values_as_placeholders(self):
        script = self.markup.split("<script>")[1]
        self.assertIn("placeholder=s.settings.seed_n", script.replace(" ", ""))

    def test_the_state_carries_the_raw_and_resolved_values(self):
        with harness.temp_app(seed_n=3) as app:
            app.add_channel(channel_id="UC_a", title="A", seed_n=8)
            state = server.build_state(server.App(app.cfg, app.conn))
            channel = state["channels"][0]

        self.assertEqual(channel["seed_n"], 8)
        self.assertEqual(channel["effective"]["seed_n"], 8)


if __name__ == "__main__":
    unittest.main()
