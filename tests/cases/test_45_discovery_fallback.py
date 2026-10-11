"""Discovery survives YouTube fencing off the RSS endpoint.

Found on a real box: every channel's feed returned 404 or 500 while the
channel ids were all valid, plain curl failed the same way, and yt-dlp kept
working. YouTube bot-mitigates /feeds/ per host. RSS stays the cheap first
choice; yt-dlp stands in when it is refused.
"""

import contextlib
import io
import json
import unittest
import urllib.error

import harness
from tuner import db, discover, feed, ytdlp

CHANNEL = "UCabcdefghijklmnopqrstuv"


@contextlib.contextmanager
def feed_raises(error):
    def opener(request, timeout=None):
        raise error

    previous = feed.OPENER
    feed.OPENER = opener
    try:
        yield
    finally:
        feed.OPENER = previous


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
def feed_serves(body):
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        return FakeResponse(body)

    previous = feed.OPENER
    feed.OPENER = opener
    try:
        yield calls
    finally:
        feed.OPENER = previous


def listing_rule(count=3, channel_title="Alpha Channel"):
    return {"match": ["--flat-playlist", "--playlist-items"],
            "returncode": 0,
            "stdout": json.dumps({
                "_type": "playlist", "id": CHANNEL, "channel": channel_title,
                "entries": [{"id": "vid%08d" % (n + 1),
                             "title": "Video %d" % (n + 1)}
                            for n in range(count)]})}


def channel(app):
    app.add_channel(channel_id=CHANNEL, title="Alpha Channel",
                    url="https://www.youtube.com/channel/" + CHANNEL,
                    folder="Alpha Channel")
    return db.get_channel_by_ytid(app.conn, CHANNEL)


class ListRecentTest(unittest.TestCase):

    def test_it_asks_for_the_newest_fifteen(self):
        with harness.temp_app() as app:
            app.scenario([listing_rule()])
            ytdlp.list_recent(app.cfg, CHANNEL)

            call = app.last_call()
            self.assertIn("-J", call)
            self.assertIn("--flat-playlist", call)
            self.assertEqual(call[call.index("--playlist-items") + 1], "1-15")
            self.assertIn("https://www.youtube.com/channel/" + CHANNEL, call)

    def test_it_returns_the_same_shape_the_feed_parser_does(self):
        with harness.temp_app() as app:
            app.scenario([listing_rule(count=2)])
            parsed = ytdlp.list_recent(app.cfg, CHANNEL)

        self.assertEqual(parsed["channel_id"], CHANNEL)
        self.assertEqual(parsed["channel_title"], "Alpha Channel")
        self.assertEqual(len(parsed["entries"]), 2)
        for key in ("video_id", "title", "published", "thumbnail"):
            self.assertIn(key, parsed["entries"][0])
        # Flat entries carry no date; the probe supplies it later.
        self.assertIsNone(parsed["entries"][0]["published"])

    def test_junk_entries_are_dropped(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["--flat-playlist"], "returncode": 0,
                           "stdout": json.dumps({
                               "_type": "playlist", "id": CHANNEL,
                               "entries": [{"id": "vid00000001", "title": "Real"},
                                           {"id": "not a video id"},
                                           "a string",
                                           {"title": "no id at all"}]})}])
            parsed = ytdlp.list_recent(app.cfg, CHANNEL)

        self.assertEqual([e["video_id"] for e in parsed["entries"]],
                         ["vid00000001"])

    def test_a_failure_raises_rather_than_returning_nothing(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["--flat-playlist"], "returncode": 1,
                           "stderr": "ERROR: unable to reach YouTube"}])
            with self.assertRaises(ytdlp.YtdlpError):
                ytdlp.list_recent(app.cfg, CHANNEL)


class FallbackTest(unittest.TestCase):

    def test_rss_is_still_preferred_when_it_works(self):
        with harness.temp_app() as app:
            row = channel(app)
            app.scenario([])   # any yt-dlp call would fail
            with feed_serves(harness.fixture("feed_sample.xml")) as calls:
                added = discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(added, 15)
            self.assertEqual(len(calls), 1)
            self.assertEqual(app.calls(), [])   # yt-dlp never ran

    def test_a_fenced_off_feed_falls_back(self):
        for code in (404, 500, 429):
            with harness.temp_app() as app:
                row = channel(app)
                app.scenario([listing_rule(count=4)])
                error = urllib.error.HTTPError("url", code, "no", {},
                                               io.BytesIO(b""))
                with feed_raises(error):
                    added = discover.ingest(app.conn, app.cfg, row)

                self.assertEqual(added, 4, "HTTP %d did not fall back" % code)
                self.assertIsNone(
                    db.get_channel_by_ytid(app.conn, CHANNEL)["last_error"])

    def test_the_fallback_is_said_out_loud(self):
        with harness.temp_app() as app:
            row = channel(app)
            app.scenario([listing_rule()])
            error = urllib.error.HTTPError("url", 404, "no", {}, io.BytesIO(b""))
            with feed_raises(error):
                discover.ingest(app.conn, app.cfg, row)

            self.assertTrue(any("listed with yt-dlp" in m and "404" in m
                                for m in app.events()))

    def test_both_failing_records_both_reasons(self):
        with harness.temp_app() as app:
            row = channel(app)
            app.scenario([])
            error = urllib.error.HTTPError("url", 404, "no", {}, io.BytesIO(b""))
            with feed_raises(error):
                added = discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(added, 0)
            trouble = db.get_channel_by_ytid(app.conn, CHANNEL)["last_error"]
            self.assertIn("404", trouble)
            self.assertIn("yt-dlp also failed", trouble)

    def test_discovery_can_be_pinned_to_one_route(self):
        with harness.temp_app(discovery="ytdlp") as app:
            row = channel(app)
            app.scenario([listing_rule(count=2)])
            with feed_serves(harness.fixture("feed_sample.xml")) as calls:
                added = discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(added, 2)
            self.assertEqual(calls, [])        # RSS never tried

        with harness.temp_app(discovery="rss") as app:
            row = channel(app)
            app.scenario([listing_rule()])
            error = urllib.error.HTTPError("url", 404, "no", {}, io.BytesIO(b""))
            with feed_raises(error):
                discover.ingest(app.conn, app.cfg, row)

            self.assertEqual(app.calls(), [])  # yt-dlp never tried
            self.assertIn("404",
                          db.get_channel_by_ytid(app.conn, CHANNEL)["last_error"])


class PublishedDateTest(unittest.TestCase):

    def test_the_probe_supplies_the_date_a_flat_listing_lacks(self):
        # Without this the filename would carry today's date rather than the
        # upload date.
        with harness.temp_app() as app:
            row = channel(app)
            app.scenario([
                listing_rule(count=1),
                harness.rule_probe(video_id="vid00000001", duration=600,
                                   upload_date="20260714", match=["-J", "vid00000001"]),
            ])
            error = urllib.error.HTTPError("url", 404, "no", {}, io.BytesIO(b""))
            with feed_raises(error):
                discover.ingest(app.conn, app.cfg, row)

            self.assertIsNone(
                db.get_video_by_ytid(app.conn, "vid00000001")["published_at"])

            discover.process_new(app.conn, app.cfg)

            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000001")["published_at"],
                "2026-07-14T00:00:00Z")

    def test_a_date_from_the_feed_is_not_overwritten(self):
        with harness.temp_app() as app:
            row = channel(app)
            app.scenario([harness.rule_probe(video_id="vid00000001",
                                             duration=600,
                                             upload_date="20200101",
                                             match=["-J", "vid00000001"])])
            with feed_serves(harness.fixture("feed_sample.xml")):
                discover.ingest(app.conn, app.cfg, row)

            before = db.get_video_by_ytid(app.conn, "vid00000001")["published_at"]
            discover.process_new(app.conn, app.cfg, limit=1)
            after = db.get_video_by_ytid(app.conn, "vid00000001")["published_at"]

            self.assertEqual(after, before)
            self.assertNotEqual(after, "2020-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
