"""Spacing feed requests out.

Thirty-two channels polled back to back is thirty-two requests to
youtube.com in about two seconds, which comes back as a mix of 404s and
500s no matter how valid the channel ids are. The poll interval was never
the problem; the burst inside each cycle was.
"""

import contextlib
import threading
import unittest

import harness
from tuner import config, db, discover, feed, server


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


@contextlib.contextmanager
def counting_pauses():
    """Record every pause instead of actually waiting."""
    waits = []

    def fake_pause(seconds, stop_event=None):
        waits.append(seconds)
        return bool(stop_event is not None and stop_event.is_set())

    original = discover.pause
    discover.pause = fake_pause
    try:
        yield waits
    finally:
        discover.pause = original


def add_channels(app, count):
    for index in range(count):
        app.add_channel(channel_id="UC_%02d" % index, title="Channel %d" % index,
                        url="https://www.youtube.com/channel/UC_%02d" % index,
                        folder="Channel %d" % index)


class DelayTest(unittest.TestCase):

    def test_the_default_leaves_a_gap(self):
        self.assertEqual(config.defaults()["feed_delay_seconds"], 3)

    def test_it_is_read_from_config_and_survives_nonsense(self):
        self.assertEqual(discover.feed_delay({"feed_delay_seconds": 7}), 7.0)
        self.assertEqual(discover.feed_delay({"feed_delay_seconds": "lots"}), 3.0)
        self.assertEqual(discover.feed_delay({"feed_delay_seconds": -5}), 0.0)
        self.assertEqual(discover.feed_delay({}), 3.0)


class SpacingTest(unittest.TestCase):

    def test_a_cycle_pauses_between_channels_but_not_before_the_first(self):
        with harness.temp_app(feed_delay_seconds=3) as app:
            add_channels(app, 5)
            with serving(harness.fixture("feed_sample.xml")):
                with counting_pauses() as waits:
                    discover.ingest_all(app.conn, app.cfg)

            # Five channels, four gaps.
            self.assertEqual(len(waits), 4)
            for seconds in waits:
                self.assertGreaterEqual(seconds, 3)
                self.assertLessEqual(seconds, 4.5)   # jitter is half again

    def test_the_gaps_are_not_all_identical(self):
        with harness.temp_app(feed_delay_seconds=3) as app:
            add_channels(app, 12)
            with serving(harness.fixture("feed_sample.xml")):
                with counting_pauses() as waits:
                    discover.ingest_all(app.conn, app.cfg)
            self.assertGreater(len(set(waits)), 1)

    def test_a_zero_delay_never_waits(self):
        with harness.temp_app(feed_delay_seconds=0) as app:
            add_channels(app, 4)
            with serving(harness.fixture("feed_sample.xml")):
                with counting_pauses() as waits:
                    discover.ingest_all(app.conn, app.cfg)
            self.assertEqual([w for w in waits if w > 0], [])

    def test_one_channel_needs_no_gap_at_all(self):
        with harness.temp_app(feed_delay_seconds=3) as app:
            add_channels(app, 1)
            with serving(harness.fixture("feed_sample.xml")):
                with counting_pauses() as waits:
                    discover.ingest_all(app.conn, app.cfg)
            self.assertEqual(waits, [])

    def test_a_targeted_check_is_immediate(self):
        # Pressing Check now on one channel should not sit through a gap.
        with harness.temp_app(feed_delay_seconds=3) as app:
            add_channels(app, 5)
            row = db.get_channel_by_ytid(app.conn, "UC_03")
            with serving(harness.fixture("feed_sample.xml")):
                with counting_pauses() as waits:
                    discover.ingest_all(app.conn, app.cfg,
                                        only_channel_id=row["id"])
            self.assertEqual(waits, [])

    def test_every_channel_is_still_ingested(self):
        with harness.temp_app(feed_delay_seconds=3) as app:
            add_channels(app, 6)
            with serving(harness.fixture("feed_sample.xml")):
                with counting_pauses():
                    discover.ingest_all(app.conn, app.cfg)

            for row in db.list_channels(app.conn):
                self.assertIsNotNone(row["last_checked_at"])


class ShutdownTest(unittest.TestCase):

    def test_stopping_mid_cycle_does_not_wait_out_the_gaps(self):
        # Thirty channels at three seconds is a ninety second cycle. A
        # restart must not have to sit through the rest of it.
        stop = threading.Event()
        stop.set()

        with harness.temp_app(feed_delay_seconds=3) as app:
            add_channels(app, 30)
            with serving(harness.fixture("feed_sample.xml")):
                with counting_pauses() as waits:
                    discover.ingest_all(app.conn, app.cfg, stop_event=stop)

            self.assertEqual(waits, [])
            # It bailed before touching any channel.
            checked = [r for r in db.list_channels(app.conn)
                       if r["last_checked_at"]]
            self.assertEqual(checked, [])

    def test_the_scheduler_hands_its_stop_event_down(self):
        with harness.temp_app() as app:
            from tuner import scheduler
            sched = scheduler.Scheduler(server.App(app.cfg, app.conn))
            seen = {}

            original = discover.ingest_all

            def spy(conn, cfg, only_channel_id=None, stop_event=None):
                seen["stop_event"] = stop_event
                return 0

            discover.ingest_all = spy
            try:
                sched._cycle()
            finally:
                discover.ingest_all = original

            self.assertIs(seen["stop_event"], sched._stop)


class AddBurstTest(unittest.TestCase):

    def test_a_paste_of_many_urls_is_spaced_too(self):
        # Each add is a yt-dlp resolve plus a feed fetch, so a paste of
        # forty is the same burst by another route.
        with harness.temp_app(feed_delay_seconds=3) as app:
            app.scenario([harness.rule_resolve("UCabcdefghijklmnopqrstuv",
                                               "Alpha")])
            with serving(harness.fixture("feed_sample.xml")):
                with counting_pauses() as waits:
                    discover.add_channels(app.conn, app.cfg,
                                          ["@one", "@two", "@three"])
            self.assertEqual(len(waits), 2)
            # Capped so a big paste still returns to the browser.
            for seconds in waits:
                self.assertLessEqual(seconds, 1.0)

    def test_a_single_add_is_immediate(self):
        with harness.temp_app(feed_delay_seconds=3) as app:
            app.scenario([harness.rule_resolve("UCabcdefghijklmnopqrstuv",
                                               "Alpha")])
            with serving(harness.fixture("feed_sample.xml")):
                with counting_pauses() as waits:
                    discover.add_channels(app.conn, app.cfg, ["@one"])
            self.assertEqual(waits, [])


class SettingTest(unittest.TestCase):

    def test_it_is_editable_and_bounded(self):
        with harness.temp_app() as app:
            changed, problems = server.apply_settings(
                app.cfg, {"feed_delay_seconds": 8})
            self.assertEqual(problems, [])
            self.assertEqual(app.cfg["feed_delay_seconds"], 8)

            _, problems = server.apply_settings(
                app.cfg, {"feed_delay_seconds": 999})
            self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
