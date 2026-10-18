"""0.6.1 -- a dead library drive pauses downloads instead of spinning.

On 2026-10-18 the USB drive behind /media/youtube fell off the bus. The dead
mount still answered statvfs, every download raised EIO, and the crash path
re-queued the row with no backoff and no attempt budget: 4.5 million retries
in a day, each one logged, until the server's 13 GB root disk was full.
"""

import errno
import unittest
from unittest import mock

import harness
from tuner import db, log, server, worker

VIDEO = "dQw4w9WgXcQ"
DEAD = OSError(errno.EIO, "Input/output error")


def runtime(app):
    return server.App(app.cfg, app.conn)


def queued_row(app, video_id=VIDEO):
    chan = app.add_channel(channel_id="UC_a", title="Alpha",
                           url="https://www.youtube.com/channel/UC_a", folder="Alpha")
    app.add_video(video_id, chan, "A video", published_at="2026-08-01T00:00:00Z")
    row = db.get_video_by_ytid(app.conn, video_id)
    db.set_video_status(app.conn, row["id"], db.QUEUED)
    return db.get_video_by_ytid(app.conn, video_id)


def dead_listdir(real):
    """os.listdir that fails like a vanished USB mount, for the library only."""
    def fake(path="."):
        if str(path).startswith(dead_listdir.root):
            raise OSError(errno.EIO, "Input/output error", path)
        return real(path)
    return fake


class DeadDrive(unittest.TestCase):

    def test_a_dead_drive_pauses_downloads_and_says_so_once(self):
        with harness.temp_app(min_free_gb=0, max_attempts=3) as app:
            queued_row(app)
            hand = worker.Worker(runtime(app))
            dead_listdir.root = app.cfg["library_root"]
            with mock.patch("tuner.worker.os.listdir", dead_listdir(worker.os.listdir)):
                for _ in range(50):
                    self.assertEqual(hand._once(), worker.LIBRARY_DOWN_SECONDS)
            row = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual((row["status"], row["attempts"]), (db.QUEUED, 0))  # the video paid nothing
            paused = [m for m in app.events() if "can't be read" in m]
            self.assertEqual(len(paused), 1)
            self.assertIn("Input/output error", paused[0])

            # Back again: one line saying so, and work resumes.
            app.scenario([harness.rule_download(video_id=VIDEO)])
            hand._once()
            self.assertEqual(sum("readable again" in m for m in app.events()), 1)
            self.assertEqual(db.get_video_by_ytid(app.conn, VIDEO)["status"], db.DOWNLOADED)

    def test_the_drive_vanishing_mid_download_costs_no_attempt(self):
        with harness.temp_app(min_free_gb=0, max_attempts=3) as app:
            queued_row(app)
            hand = worker.Worker(runtime(app))
            dead_listdir.root = app.cfg["library_root"]

            def gone_mid_download(row):
                patcher.start()  # the drive drops while yt-dlp is writing
                raise OSError(errno.EIO, "Input/output error", "/media/youtube/.tuner-tmp")
            patcher = mock.patch("tuner.worker.os.listdir", dead_listdir(worker.os.listdir))
            hand.handle = gone_mid_download
            try:
                self.assertEqual(hand._once(), worker.LIBRARY_DOWN_SECONDS)
                self.assertEqual(hand._once(), worker.LIBRARY_DOWN_SECONDS)
            finally:
                patcher.stop()
            row = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual((row["status"], row["attempts"]), (db.QUEUED, 0))
            self.assertIn("library unreachable", row["error"])


class Crashes(unittest.TestCase):

    def test_a_crash_pays_an_attempt_and_waits_like_any_failure(self):
        with harness.temp_app(min_free_gb=0, max_attempts=3) as app:
            queued_row(app)
            hand = worker.Worker(runtime(app))

            def boom(row):
                raise RuntimeError("something broke")
            hand.handle = boom

            self.assertEqual(hand._once(), worker.CRASH_PAUSE_SECONDS)
            row = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual((row["status"], row["attempts"]), (db.QUEUED, 1))
            self.assertIn(row["id"], hand.blocked_ids())        # backing off
            self.assertEqual(hand._once(), worker.IDLE_SECONDS)  # so nothing to claim

            for _ in range(5):                                    # the budget holds
                hand.clear_backoff()
                hand._once()
            row = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual((row["status"], row["attempts"]), (db.FAILED, 3))
            self.assertIn("giving up", app.events()[0])

    def test_the_regression_no_tight_loop(self):
        """Hundreds of passes over a crashing row stay inside the budget."""
        with harness.temp_app(min_free_gb=0, max_attempts=3) as app:
            queued_row(app)
            hand = worker.Worker(runtime(app))
            hand.handle = lambda row: (_ for _ in ()).throw(RuntimeError("again"))
            waits = [hand._once() for _ in range(300)]
            self.assertNotIn(0.0, waits)
            self.assertEqual(db.get_video_by_ytid(app.conn, VIDEO)["attempts"], 1)  # backoff held it
            self.assertLess(len(app.events()), 5)


class EventLog(unittest.TestCase):

    def test_the_event_log_trims_itself(self):
        with harness.temp_app() as app:
            with mock.patch.object(log, "KEEP_EVENTS", 30), mock.patch.object(log, "PRUNE_EVERY", 10):
                for i in range(200):
                    log.info(app.conn, "event %d" % i)
            n = app.conn.execute("SELECT count(*) FROM events").fetchone()[0]
            self.assertLessEqual(n, 40)
            newest = app.conn.execute("SELECT message FROM events ORDER BY id DESC LIMIT 1").fetchone()[0]
            self.assertEqual(newest, "event 199")
