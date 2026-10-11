"""Task 3.5 -- the poll loop's timing, resilience, and forced checks."""

import contextlib
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

import harness
from tuner import db, discover, feed, scheduler, server

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
    seen = []

    def opener(request, timeout=None):
        seen.append(request.full_url)
        return FakeResponse(body)

    previous = feed.OPENER
    feed.OPENER = opener
    try:
        yield seen
    finally:
        feed.OPENER = previous


def app_for(harness_app):
    return server.App(harness_app.cfg, harness_app.conn)


class IntervalTest(unittest.TestCase):

    def test_a_tight_config_is_clamped_to_the_floor(self):
        with harness.temp_app(poll_minutes=1, poll_jitter_minutes=0) as app:
            sched = scheduler.Scheduler(app_for(app))
            self.assertEqual(sched.interval_seconds(),
                             scheduler.MIN_INTERVAL_MINUTES * 60)

    def test_zero_and_nonsense_are_clamped_too(self):
        for value in (0, None):
            with harness.temp_app(poll_minutes=value,
                                  poll_jitter_minutes=0) as app:
                sched = scheduler.Scheduler(app_for(app))
                self.assertEqual(sched.interval_seconds(),
                                 scheduler.MIN_INTERVAL_MINUTES * 60)

    def test_jitter_stays_inside_its_window(self):
        with harness.temp_app(poll_minutes=30, poll_jitter_minutes=5) as app:
            sched = scheduler.Scheduler(app_for(app))
            values = set(sched.interval_seconds() for _ in range(60))
            self.assertGreaterEqual(min(values), 30 * 60)
            self.assertLessEqual(max(values), 35 * 60)
            self.assertGreater(len(values), 1)   # actually varying


class CycleTest(unittest.TestCase):

    def seed_channels(self, app):
        app.add_channel(channel_id="UC_on", title="On",
                        url="https://www.youtube.com/channel/UC_on", folder="On")
        app.add_channel(channel_id="UC_off", title="Off",
                        url="https://www.youtube.com/channel/UC_off",
                        folder="Off", enabled=False)

    def test_a_cycle_ingests_enabled_channels_only(self):
        with harness.temp_app() as app:
            self.seed_channels(app)
            sched = scheduler.Scheduler(app_for(app))

            with serving(harness.fixture("feed_sample.xml")) as seen:
                found = sched._cycle()

            self.assertEqual(len(seen), 1)
            self.assertIn("UC_on", seen[0])
            self.assertEqual(found, 15)
            self.assertEqual(sched.cycles, 1)
            self.assertIsNotNone(sched.last_cycle_at)

    def test_a_targeted_cycle_touches_only_that_channel(self):
        with harness.temp_app() as app:
            self.seed_channels(app)
            row = db.get_channel_by_ytid(app.conn, "UC_on")
            sched = scheduler.Scheduler(app_for(app))

            with serving(harness.fixture("feed_sample.xml")) as seen:
                sched._cycle(only_channel_id=row["id"])
            self.assertEqual(len(seen), 1)

    def test_a_raising_ingest_is_logged_and_the_loop_survives(self):
        with harness.temp_app() as app:
            self.seed_channels(app)
            sched = scheduler.Scheduler(app_for(app))

            original = discover.ingest_all

            def exploding(*args, **kwargs):
                raise RuntimeError("kaboom")

            discover.ingest_all = exploding
            try:
                found = sched._cycle()
            finally:
                discover.ingest_all = original

            self.assertEqual(found, 0)
            self.assertEqual(sched.cycles, 1)
            self.assertTrue(any("kaboom" in message
                                for message in app.events(level="error")))

            # The next cycle still works.
            with serving(harness.fixture("feed_sample.xml")):
                self.assertEqual(sched._cycle(), 15)


class ThreadTest(unittest.TestCase):

    def test_start_runs_a_cycle_then_stop_returns_promptly(self):
        with harness.temp_app(poll_minutes=60, poll_jitter_minutes=0) as app:
            app.add_channel(channel_id="UC_on", title="On",
                            url="https://www.youtube.com/channel/UC_on",
                            folder="On")
            sched = scheduler.Scheduler(app_for(app))

            with serving(harness.fixture("feed_sample.xml")):
                sched.start()
                deadline = time.time() + 5
                while sched.cycles < 1 and time.time() < deadline:
                    time.sleep(0.02)

                self.assertGreaterEqual(sched.cycles, 1)

                # The thread is now in a 60 minute sleep. Stopping must not
                # wait it out.
                started = time.time()
                sched.stop(timeout=5)
                self.assertLess(time.time() - started, 3)

            self.assertIsNone(sched.thread)

    def test_check_now_wakes_the_sleeping_loop(self):
        with harness.temp_app(poll_minutes=60, poll_jitter_minutes=0) as app:
            app.add_channel(channel_id="UC_on", title="On",
                            url="https://www.youtube.com/channel/UC_on",
                            folder="On")
            sched = scheduler.Scheduler(app_for(app))

            with serving(harness.fixture("feed_sample.xml")):
                sched.start()
                deadline = time.time() + 5
                while sched.cycles < 1 and time.time() < deadline:
                    time.sleep(0.02)
                first = sched.cycles

                sched.check_now()
                deadline = time.time() + 5
                while sched.cycles <= first and time.time() < deadline:
                    time.sleep(0.02)

                self.assertGreater(sched.cycles, first)
                sched.stop()

    def test_check_now_rejects_an_unknown_channel(self):
        with harness.temp_app() as app:
            sched = scheduler.Scheduler(app_for(app))
            self.assertFalse(sched.check_now(9999))
            self.assertTrue(sched.check_now())


class CheckEndpointTest(unittest.TestCase):

    @contextlib.contextmanager
    def running(self, app, with_scheduler=True):
        runtime = server.App(app.cfg, app.conn)
        if with_scheduler:
            runtime.scheduler = scheduler.Scheduler(runtime)
        httpd = server.make_server(runtime, host="127.0.0.1", port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            httpd.server_close()

    def post(self, port, path):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path), data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_check_endpoint_queues_a_cycle(self):
        with harness.temp_app() as app:
            row_id = app.add_channel(channel_id="UC_on", title="On",
                                     url="https://www.youtube.com/channel/UC_on",
                                     folder="On")
            with self.running(app) as port:
                status, body = self.post(port, "/api/channels/%d/check" % row_id)

            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])

    def test_check_endpoint_404s_for_an_unknown_channel(self):
        with harness.temp_app() as app:
            with self.running(app) as port:
                status, body = self.post(port, "/api/channels/9999/check")
            self.assertEqual(status, 404)
            self.assertFalse(body["ok"])

    def test_check_endpoint_reports_a_missing_scheduler(self):
        with harness.temp_app() as app:
            row_id = app.add_channel(channel_id="UC_on", title="On",
                                     url="https://www.youtube.com/channel/UC_on",
                                     folder="On")
            with self.running(app, with_scheduler=False) as port:
                status, _ = self.post(port, "/api/channels/%d/check" % row_id)
            self.assertEqual(status, 503)


if __name__ == "__main__":
    unittest.main()
