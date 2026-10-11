"""Task 5.3 -- claiming, deferring, and the worker loop's resilience."""

import os
import time
import unittest

import harness
from tuner import db, disk, worker


class RecordingWorker(worker.Worker):
    """Stands in for the real download, wired at tasks 5.4 and 5.5."""

    def __init__(self, app, fail_with=None):
        worker.Worker.__init__(self, app)
        self.handled = []
        self.fail_with = fail_with

    def handle(self, row):
        self.handled.append(row["video_id"])
        if self.fail_with is not None:
            raise self.fail_with
        db.set_video_status(self.app.conn, row["id"], db.DOWNLOADED,
                            file_path="/tmp/fake.mp4", file_size=10)


def runtime(app):
    from tuner import server
    return server.App(app.cfg, app.conn)


def queue(app, ids_and_dates):
    chan = app.add_channel(channel_id="UC_a", title="Alpha",
                           url="https://www.youtube.com/channel/UC_a",
                           folder="Alpha")
    for video_id, published in ids_and_dates:
        app.add_video(video_id, chan, "Title " + video_id,
                      published_at=published)
        row = db.get_video_by_ytid(app.conn, video_id)
        db.set_video_status(app.conn, row["id"], db.QUEUED)
    return chan


class ClaimTest(unittest.TestCase):

    def test_claims_oldest_first_and_never_twice(self):
        with harness.temp_app() as app:
            queue(app, [("vidnewest01", "2026-08-05T00:00:00Z"),
                        ("vidmiddle01", "2026-08-03T00:00:00Z"),
                        ("vidoldest01", "2026-08-01T00:00:00Z")])
            hand = RecordingWorker(runtime(app))

            first = hand.claim()
            second = hand.claim()
            third = hand.claim()

            self.assertEqual(first["video_id"], "vidoldest01")
            self.assertEqual(second["video_id"], "vidmiddle01")
            self.assertEqual(third["video_id"], "vidnewest01")
            self.assertIsNone(hand.claim())

    def test_claiming_flips_the_row_to_downloading(self):
        with harness.temp_app() as app:
            queue(app, [("vidoldest01", "2026-08-01T00:00:00Z")])
            hand = RecordingWorker(runtime(app))

            claimed = hand.claim()
            self.assertEqual(db.get_video(app.conn, claimed["id"])["status"],
                             db.DOWNLOADING)

    def test_an_empty_queue_claims_nothing(self):
        with harness.temp_app() as app:
            self.assertIsNone(RecordingWorker(runtime(app)).claim())


class HeadroomTest(unittest.TestCase):

    def test_no_headroom_defers_the_row_and_waits(self):
        with harness.temp_app(library_root="/definitely/not/mounted") as app:
            chan = app.add_channel(channel_id="UC_a", title="Alpha",
                                   url="https://www.youtube.com/channel/UC_a",
                                   folder="Alpha")
            app.add_video("vidoldest01", chan, "Title")
            row = db.get_video_by_ytid(app.conn, "vidoldest01")
            db.set_video_status(app.conn, row["id"], db.QUEUED)

            hand = RecordingWorker(runtime(app))
            wait = hand._once()

            self.assertEqual(wait, worker.NO_SPACE_SECONDS)
            after = db.get_video_by_ytid(app.conn, "vidoldest01")
            self.assertEqual(after["status"], db.DEFERRED)
            self.assertIn("disk space", after["error"])
            self.assertEqual(hand.handled, [])
            self.assertTrue(any("deferred" in m for m in app.events(level="warn")))

    def test_deferred_rows_return_to_the_queue_when_space_comes_back(self):
        with harness.temp_app() as app:
            chan = app.add_channel(channel_id="UC_a", title="Alpha",
                                   url="https://www.youtube.com/channel/UC_a",
                                   folder="Alpha")
            app.add_video("viddeferred", chan, "Waiting")
            row = db.get_video_by_ytid(app.conn, "viddeferred")
            db.set_video_status(app.conn, row["id"], db.DEFERRED)

            hand = RecordingWorker(runtime(app))
            moved = hand.requeue_deferred()

            self.assertEqual(moved, 1)
            self.assertEqual(
                db.get_video_by_ytid(app.conn, "viddeferred")["status"],
                db.QUEUED)

    def test_a_full_pass_downloads_when_there_is_room(self):
        with harness.temp_app(min_free_gb=0) as app:
            queue(app, [("vidoldest01", "2026-08-01T00:00:00Z")])
            hand = RecordingWorker(runtime(app))

            hand._once()

            self.assertEqual(hand.handled, ["vidoldest01"])
            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vidoldest01")["status"],
                db.DOWNLOADED)


class ResilienceTest(unittest.TestCase):

    def test_a_crash_returns_the_row_to_the_queue_and_counts_an_attempt(self):
        with harness.temp_app(min_free_gb=0) as app:
            queue(app, [("vidoldest01", "2026-08-01T00:00:00Z")])
            hand = RecordingWorker(runtime(app), fail_with=RuntimeError("boom"))

            hand._once()

            row = db.get_video_by_ytid(app.conn, "vidoldest01")
            self.assertEqual(row["status"], db.QUEUED)   # never stuck
            self.assertEqual(row["attempts"], 1)
            self.assertIn("boom", row["error"])
            self.assertTrue(any("crashed" in m for m in app.events(level="error")))

    def test_no_row_is_left_in_downloading_after_a_crash(self):
        with harness.temp_app(min_free_gb=0) as app:
            queue(app, [("vidoldest01", "2026-08-01T00:00:00Z")])
            hand = RecordingWorker(runtime(app), fail_with=RuntimeError("boom"))
            hand._once()
            self.assertEqual(db.list_videos(app.conn, status=db.DOWNLOADING), [])


class ProgressSnapshotTest(unittest.TestCase):

    def tearDown(self):
        worker.set_current(None)

    def test_current_is_set_during_work_and_cleared_after(self):
        seen = {}

        class Peeking(RecordingWorker):
            def handle(self, row):
                seen["during"] = worker.current()
                RecordingWorker.handle(self, row)

        with harness.temp_app(min_free_gb=0) as app:
            queue(app, [("vidoldest01", "2026-08-01T00:00:00Z")])
            Peeking(runtime(app))._once()

        self.assertIsNotNone(seen["during"])
        self.assertEqual(seen["during"]["video_id"], "vidoldest01")
        self.assertEqual(seen["during"]["stage"], "starting")
        self.assertIsNone(worker.current())

    def test_current_is_cleared_even_when_the_download_crashes(self):
        with harness.temp_app(min_free_gb=0) as app:
            queue(app, [("vidoldest01", "2026-08-01T00:00:00Z")])
            RecordingWorker(runtime(app), fail_with=RuntimeError("boom"))._once()
        self.assertIsNone(worker.current())

    def test_current_returns_a_copy(self):
        worker.set_current({"video_id": "x", "percent": 1})
        snapshot = worker.current()
        snapshot["percent"] = 99
        self.assertEqual(worker.current()["percent"], 1)


class ThreadTest(unittest.TestCase):

    def test_start_drains_the_queue_then_stop_returns_promptly(self):
        with harness.temp_app(min_free_gb=0) as app:
            queue(app, [("vidoldest01", "2026-08-01T00:00:00Z"),
                        ("vidnewest01", "2026-08-05T00:00:00Z")])
            hand = RecordingWorker(runtime(app))
            hand.start()

            deadline = time.time() + 5
            while len(hand.handled) < 2 and time.time() < deadline:
                time.sleep(0.02)

            self.assertEqual(hand.handled, ["vidoldest01", "vidnewest01"])

            started = time.time()
            hand.stop(timeout=5)
            self.assertLess(time.time() - started, 3)
            self.assertIsNone(hand.thread)

    def test_stop_is_safe_when_never_started(self):
        with harness.temp_app() as app:
            RecordingWorker(runtime(app)).stop()


if __name__ == "__main__":
    unittest.main()
