"""Task 5.6 -- the attempts ladder, backoff, and the retry endpoint."""

import contextlib
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

import harness
from tuner import db, server, worker

VIDEO = "dQw4w9WgXcQ"


def runtime(app):
    return server.App(app.cfg, app.conn)


def queued_row(app, video_id=VIDEO):
    chan = app.add_channel(channel_id="UC_a", title="Alpha",
                           url="https://www.youtube.com/channel/UC_a",
                           folder="Alpha")
    app.add_video(video_id, chan, "A video", published_at="2026-08-01T00:00:00Z")
    row = db.get_video_by_ytid(app.conn, video_id)
    db.set_video_status(app.conn, row["id"], db.QUEUED)
    return db.get_video_by_ytid(app.conn, video_id)


class LadderTest(unittest.TestCase):

    def test_three_failures_walk_queued_queued_failed(self):
        with harness.temp_app(min_free_gb=0, max_attempts=3) as app:
            queued_row(app)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "stdout": "ERROR: unable to download video data"}])
            hand = worker.Worker(runtime(app))

            for expected, attempts in ((db.QUEUED, 1), (db.QUEUED, 2),
                                       (db.FAILED, 3)):
                hand.clear_backoff()          # skip the wait for the test
                hand._once()
                row = db.get_video_by_ytid(app.conn, VIDEO)
                self.assertEqual(row["status"], expected)
                self.assertEqual(row["attempts"], attempts)

            self.assertIn("unable to download",
                          db.get_video_by_ytid(app.conn, VIDEO)["error"])

    def test_a_permanent_refusal_skips_at_once_without_burning_an_attempt(self):
        with harness.temp_app(min_free_gb=0, max_attempts=3) as app:
            queued_row(app)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "stdout": "ERROR: Private video. Sign in if you've "
                                     "been granted access"}])

            worker.Worker(runtime(app))._once()

            row = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual(row["status"], db.SKIPPED)
            self.assertEqual(row["skip_reason"], "private")
            self.assertEqual(row["attempts"], 0)

    def test_a_rate_limit_is_retried_rather_than_skipped(self):
        with harness.temp_app(min_free_gb=0, max_attempts=3) as app:
            queued_row(app)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "stdout": "ERROR: Sign in to confirm you're not a bot"}])

            worker.Worker(runtime(app))._once()

            row = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual(row["status"], db.QUEUED)
            self.assertEqual(row["attempts"], 1)

    def test_max_attempts_of_one_fails_immediately(self):
        with harness.temp_app(min_free_gb=0, max_attempts=1) as app:
            queued_row(app)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "stdout": "ERROR: network unreachable"}])

            worker.Worker(runtime(app))._once()
            self.assertEqual(db.get_video_by_ytid(app.conn, VIDEO)["status"],
                             db.FAILED)


class BackoffTest(unittest.TestCase):

    def test_backoff_grows_with_attempts_and_is_capped(self):
        with harness.temp_app() as app:
            hand = worker.Worker(runtime(app))
            self.assertEqual(hand.back_off(1, 1), 60)
            self.assertEqual(hand.back_off(1, 2), 120)
            self.assertEqual(hand.back_off(1, 99), worker.BACKOFF_MAX_SECONDS)

    def test_a_backing_off_row_is_not_claimed(self):
        with harness.temp_app(min_free_gb=0) as app:
            row = queued_row(app)
            hand = worker.Worker(runtime(app))
            hand.back_off(row["id"], 1)

            self.assertIsNone(hand.claim())

    def test_the_queue_moves_on_to_another_video(self):
        with harness.temp_app(min_free_gb=0) as app:
            first = queued_row(app, "vidfirst001")
            app.add_video("vidsecond01", first["channel_row_id"], "Second",
                          published_at="2026-08-02T00:00:00Z")
            second = db.get_video_by_ytid(app.conn, "vidsecond01")
            db.set_video_status(app.conn, second["id"], db.QUEUED)

            hand = worker.Worker(runtime(app))
            hand.back_off(first["id"], 1)

            claimed = hand.claim()
            self.assertEqual(claimed["video_id"], "vidsecond01")

    def test_an_expired_backoff_is_forgotten(self):
        with harness.temp_app(min_free_gb=0) as app:
            row = queued_row(app)
            hand = worker.Worker(runtime(app))
            hand._backoff[row["id"]] = time.time() - 1

            self.assertEqual(hand.blocked_ids(), set())
            self.assertIsNotNone(hand.claim())

    def test_backoff_does_not_survive_a_restart(self):
        # In memory on purpose: a restart is a fresh start, not a penalty.
        with harness.temp_app(min_free_gb=0) as app:
            row = queued_row(app)
            first = worker.Worker(runtime(app))
            first.back_off(row["id"], 5)

            self.assertIsNone(first.claim())
            self.assertIsNotNone(worker.Worker(runtime(app)).claim())


class RetryTest(unittest.TestCase):

    def test_retry_resets_the_budget_and_clears_the_backoff(self):
        with harness.temp_app(min_free_gb=0, max_attempts=1) as app:
            row = queued_row(app)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "stdout": "ERROR: network unreachable"}])
            hand = worker.Worker(runtime(app))
            hand._once()

            self.assertEqual(db.get_video(app.conn, row["id"])["status"],
                             db.FAILED)

            self.assertTrue(hand.retry(row["id"]))
            after = db.get_video(app.conn, row["id"])
            self.assertEqual(after["status"], db.QUEUED)
            self.assertEqual(after["attempts"], 0)
            self.assertIsNone(after["error"])
            self.assertEqual(hand.blocked_ids(), set())

    def test_retry_also_rescues_a_missing_file(self):
        with harness.temp_app() as app:
            row = queued_row(app)
            db.set_video_status(app.conn, row["id"], db.MISSING)

            hand = worker.Worker(runtime(app))
            self.assertTrue(hand.retry(row["id"]))
            self.assertEqual(db.get_video(app.conn, row["id"])["status"],
                             db.QUEUED)

    def test_retry_on_an_unknown_row(self):
        with harness.temp_app() as app:
            self.assertFalse(worker.Worker(runtime(app)).retry(9999))


class RetryEndpointTest(unittest.TestCase):

    @contextlib.contextmanager
    def running(self, app, with_worker=True):
        runner = server.App(app.cfg, app.conn)
        if with_worker:
            runner.worker = worker.Worker(runner)
        httpd = server.make_server(runner, host="127.0.0.1", port=0)
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

    def test_the_endpoint_requeues(self):
        with harness.temp_app() as app:
            row = queued_row(app)
            db.set_video_status(app.conn, row["id"], db.FAILED, attempts=3)

            with self.running(app) as port:
                status, body = self.post(port, "/api/videos/%d/retry" % row["id"])

            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            after = db.get_video(app.conn, row["id"])
            self.assertEqual(after["status"], db.QUEUED)
            self.assertEqual(after["attempts"], 0)

    def test_unknown_video_and_missing_worker(self):
        with harness.temp_app() as app:
            with self.running(app) as port:
                self.assertEqual(self.post(port, "/api/videos/9999/retry")[0], 404)
            with self.running(app, with_worker=False) as port:
                self.assertEqual(self.post(port, "/api/videos/1/retry")[0], 503)


if __name__ == "__main__":
    unittest.main()
