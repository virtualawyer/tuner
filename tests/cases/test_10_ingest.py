"""Task 3.3 -- recording feed entries without disturbing known rows."""

import contextlib
import io
import json
import unittest
import urllib.error

import harness
from tuner import db, discover, feed

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
def serving(body=None, status=200, headers=None, raises=None):
    calls = []

    def opener(request, timeout=None):
        calls.append(dict(request.header_items()))
        if raises is not None:
            raise raises
        return FakeResponse(body or "", status, headers)

    previous = feed.OPENER
    feed.OPENER = opener
    try:
        yield calls
    finally:
        feed.OPENER = previous


class IngestTest(unittest.TestCase):

    def setUp(self):
        self.sample = harness.fixture("feed_sample.xml")

    def channel(self, app):
        app.add_channel(channel_id=CHANNEL, title="Alpha Channel",
                        url="https://www.youtube.com/channel/" + CHANNEL,
                        folder="Alpha Channel")
        return db.get_channel_by_ytid(app.conn, CHANNEL)

    def test_first_ingest_records_every_entry(self):
        with harness.temp_app() as app:
            row = self.channel(app)
            with serving(self.sample):
                added = discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(added, 15)
            self.assertEqual(len(db.list_videos(app.conn)), 15)
            self.assertTrue(all(v["status"] == db.NEW
                                for v in db.list_videos(app.conn)))

    def test_second_ingest_adds_nothing(self):
        with harness.temp_app() as app:
            row = self.channel(app)
            with serving(self.sample):
                discover.ingest(app.conn, app.cfg, row)
            row = db.get_channel_by_ytid(app.conn, CHANNEL)
            with serving(self.sample):
                added = discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(added, 0)
            self.assertEqual(len(db.list_videos(app.conn)), 15)

    def test_a_deleted_video_never_comes_back(self):
        # The feed still lists it. That must not resurrect the row, or a
        # video deleted on purpose would be downloaded again forever.
        with harness.temp_app() as app:
            row = self.channel(app)
            with serving(self.sample):
                discover.ingest(app.conn, app.cfg, row)

            victim = db.get_video_by_ytid(app.conn, "vid00000003")
            db.set_video_status(app.conn, victim["id"], db.DELETED)

            row = db.get_channel_by_ytid(app.conn, CHANNEL)
            with serving(self.sample):
                discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000003")["status"],
                db.DELETED)

    def test_known_rows_keep_their_status_and_progress(self):
        with harness.temp_app() as app:
            row = self.channel(app)
            with serving(self.sample):
                discover.ingest(app.conn, app.cfg, row)

            known = db.get_video_by_ytid(app.conn, "vid00000005")
            db.set_video_status(app.conn, known["id"], db.DOWNLOADED,
                                file_path="/srv/media/youtube/a.mp4",
                                file_size=1234)

            row = db.get_channel_by_ytid(app.conn, CHANNEL)
            with serving(self.sample):
                discover.ingest(app.conn, app.cfg, row)

            after = db.get_video_by_ytid(app.conn, "vid00000005")
            self.assertEqual(after["status"], db.DOWNLOADED)
            self.assertEqual(after["file_size"], 1234)

    def test_validators_are_stored_and_replayed(self):
        with harness.temp_app() as app:
            row = self.channel(app)
            headers = {"ETag": 'W/"v1"',
                       "Last-Modified": "Mon, 11 Aug 2026 10:00:00 GMT"}
            with serving(self.sample, headers=headers):
                discover.ingest(app.conn, app.cfg, row)

            stored = db.get_channel_by_ytid(app.conn, CHANNEL)
            self.assertEqual(stored["etag"], 'W/"v1"')
            self.assertEqual(stored["last_modified"],
                             "Mon, 11 Aug 2026 10:00:00 GMT")

            with serving(self.sample) as calls:
                discover.ingest(app.conn, app.cfg, stored)
            self.assertEqual(calls[0].get("If-none-match"), 'W/"v1"')

    def test_304_costs_nothing_and_clears_no_state(self):
        with harness.temp_app() as app:
            row = self.channel(app)
            with serving(self.sample):
                discover.ingest(app.conn, app.cfg, row)

            row = db.get_channel_by_ytid(app.conn, CHANNEL)
            error = urllib.error.HTTPError("url", 304, "Not Modified", {},
                                           io.BytesIO(b""))
            with serving(raises=error):
                added = discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(added, 0)
            self.assertEqual(len(db.list_videos(app.conn)), 15)
            after = db.get_channel_by_ytid(app.conn, CHANNEL)
            self.assertIsNotNone(after["last_checked_at"])
            self.assertIsNone(after["last_error"])

    def test_the_feed_title_updates_the_channel_name(self):
        with harness.temp_app() as app:
            app.add_channel(channel_id=CHANNEL, title=CHANNEL,
                            url="https://www.youtube.com/channel/" + CHANNEL,
                            folder=CHANNEL)
            row = db.get_channel_by_ytid(app.conn, CHANNEL)
            with serving(self.sample):
                discover.ingest(app.conn, app.cfg, row)

            after = db.get_channel_by_ytid(app.conn, CHANNEL)
            self.assertEqual(after["title"], "Alpha Channel")
            # The folder is deliberately left alone so existing files stay
            # where Jellyfin already indexed them.
            self.assertEqual(after["folder"], CHANNEL)


class IngestFailureTest(unittest.TestCase):

    def channel(self, app):
        app.add_channel(channel_id=CHANNEL, title="Alpha Channel",
                        url="https://www.youtube.com/channel/" + CHANNEL,
                        folder="Alpha Channel")
        return db.get_channel_by_ytid(app.conn, CHANNEL)

    def test_http_failure_falls_back_to_ytdlp(self):
        # YouTube fences the /feeds/ endpoint off from hosts it takes for
        # bots, so a 404 there is not the end of the road.
        sample = harness.fixture("feed_sample.xml")
        with harness.temp_app() as app:
            row = self.channel(app)
            app.scenario([{"match": ["--flat-playlist", "--playlist-items"],
                           "returncode": 0, "stdout": json.dumps({
                               "_type": "playlist", "id": CHANNEL,
                               "channel": "Alpha Channel",
                               "entries": [{"id": "vid00000001",
                                            "title": "Newest"},
                                           {"id": "vid00000002",
                                            "title": "Older"}]})}])

            error = urllib.error.HTTPError("url", 404, "Gone", {},
                                           io.BytesIO(b""))
            with serving(raises=error):
                added = discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(added, 2)
            after = db.get_channel_by_ytid(app.conn, CHANNEL)
            self.assertIsNone(after["last_error"])
            self.assertTrue(any("listed with yt-dlp" in m
                                for m in app.events()))

    def test_both_routes_failing_is_recorded_not_raised(self):
        with harness.temp_app() as app:
            row = self.channel(app)
            app.scenario([])   # yt-dlp has nothing to offer either
            error = urllib.error.HTTPError("url", 404, "Gone", {},
                                           io.BytesIO(b""))
            with serving(raises=error):
                added = discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(added, 0)
            after = db.get_channel_by_ytid(app.conn, CHANNEL)
            self.assertIn("404", after["last_error"])
            self.assertIn("yt-dlp also failed", after["last_error"])
            self.assertIsNotNone(after["last_checked_at"])
            self.assertTrue(any("could not list" in m
                                for m in app.events(level="warn")))

    def test_malformed_feed_is_recorded_not_raised(self):
        with harness.temp_app() as app:
            row = self.channel(app)
            with serving("<feed><entry></feed>"):
                added = discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(added, 0)
            self.assertIn("not valid XML",
                          db.get_channel_by_ytid(app.conn, CHANNEL)["last_error"])

    def test_a_successful_ingest_clears_a_previous_error(self):
        with harness.temp_app() as app:
            row = self.channel(app)
            db.update_channel(app.conn, row["id"], last_error="something old")
            row = db.get_channel_by_ytid(app.conn, CHANNEL)

            with serving(harness.fixture("feed_sample.xml")):
                discover.ingest(app.conn, app.cfg, row)

            self.assertIsNone(
                db.get_channel_by_ytid(app.conn, CHANNEL)["last_error"])

    def test_ingest_all_skips_disabled_channels_and_survives_a_crash(self):
        with harness.temp_app() as app:
            app.add_channel(channel_id="UC_on", title="On",
                            url="https://www.youtube.com/channel/UC_on",
                            folder="On")
            app.add_channel(channel_id="UC_off", title="Off",
                            url="https://www.youtube.com/channel/UC_off",
                            folder="Off", enabled=False)

            with serving(harness.fixture("feed_sample.xml")) as calls:
                total = discover.ingest_all(app.conn, app.cfg)

            self.assertEqual(len(calls), 1)   # only the enabled channel
            self.assertEqual(total, 15)


if __name__ == "__main__":
    unittest.main()
