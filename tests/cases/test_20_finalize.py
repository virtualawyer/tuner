"""Task 5.5 -- moving finished files out of scratch and into the library."""

import os
import unittest

import harness
from tuner import LIBRARY_CHANGED, db, paths, worker

VIDEO = "dQw4w9WgXcQ"


def runtime(app):
    from tuner import server
    return server.App(app.cfg, app.conn)


def queued_row(app, title="A video", published="2026-08-11T12:00:00+00:00"):
    chan = app.add_channel(channel_id="UC_a", title="Alpha Channel",
                           url="https://www.youtube.com/channel/UC_a",
                           folder="Alpha Channel")
    app.add_video(VIDEO, chan, title, published_at=published)
    row = db.get_video_by_ytid(app.conn, VIDEO)
    db.set_video_status(app.conn, row["id"], db.QUEUED)
    return db.get_video_by_ytid(app.conn, VIDEO)


class HelperTest(unittest.TestCase):

    def test_find_media_picks_the_largest(self):
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, VIDEO + ".f137.mp4"), size=100)
            app.make_file(os.path.join(tmp, VIDEO + ".mp4"), size=5000)
            app.make_file(os.path.join(tmp, VIDEO + ".en.srt"), size=20)

            self.assertEqual(os.path.basename(worker.find_media(tmp)),
                             VIDEO + ".mp4")

    def test_find_media_ignores_non_media(self):
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, VIDEO + ".en.srt"), size=9000)
            self.assertIsNone(worker.find_media(tmp))

    def test_find_media_on_a_missing_folder(self):
        self.assertIsNone(worker.find_media("/definitely/not/here"))

    def test_suffix_after_id_keeps_the_language_tag(self):
        self.assertEqual(worker.suffix_after_id(VIDEO + ".en.srt", VIDEO),
                         ".en.srt")
        self.assertEqual(worker.suffix_after_id(VIDEO + ".en-orig.srt", VIDEO),
                         ".en-orig.srt")
        self.assertEqual(worker.suffix_after_id("stray.srt", VIDEO), ".stray.srt")

    def test_discard_tmp_refuses_anything_outside_scratch(self):
        with harness.temp_app() as app:
            outside = app.make_file("keepme.mp4")
            self.assertFalse(worker.discard_tmp(app.cfg,
                                                os.path.dirname(outside)))
            self.assertTrue(os.path.exists(outside))
            self.assertFalse(worker.discard_tmp(app.cfg,
                                                paths.tmp_root(app.cfg)))

    def test_sweep_clears_scratch_left_by_a_crash(self):
        with harness.temp_app() as app:
            paths.tmp_dir(app.cfg, "leftover001")
            paths.tmp_dir(app.cfg, "leftover002")
            self.assertEqual(worker.sweep_tmp(app.cfg), 2)
            self.assertEqual(os.listdir(paths.tmp_root(app.cfg)), [])


class FinalizeTest(unittest.TestCase):

    def setUp(self):
        LIBRARY_CHANGED.clear()

    def tearDown(self):
        LIBRARY_CHANGED.clear()
        worker.set_current(None)

    def test_a_finished_download_lands_with_its_sidecars(self):
        with harness.temp_app(min_free_gb=0) as app:
            row = queued_row(app)
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, VIDEO + ".mp4"), size=4096)
            app.make_file(os.path.join(tmp, VIDEO + ".en.srt"), size=64)
            app.make_file(os.path.join(tmp, VIDEO + ".en-orig.srt"), size=32)

            hand = worker.Worker(runtime(app))
            channel = db.get_channel(app.conn, row["channel_row_id"])
            final = hand.finalize(row, channel,
                                  os.path.join(tmp, VIDEO + ".mp4"), tmp)

            expected = os.path.join(app.cfg["library_root"], "Alpha Channel",
                                    "2026-08-11 - A video [%s].mp4" % VIDEO)
            self.assertEqual(final, expected)
            self.assertTrue(os.path.exists(expected))

            stem = expected[:-len(".mp4")]
            self.assertTrue(os.path.exists(stem + ".en.srt"))
            self.assertTrue(os.path.exists(stem + ".en-orig.srt"))

    def test_the_row_records_the_path_and_a_real_size(self):
        with harness.temp_app(min_free_gb=0) as app:
            row = queued_row(app)
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, VIDEO + ".mp4"), size=4096)

            hand = worker.Worker(runtime(app))
            channel = db.get_channel(app.conn, row["channel_row_id"])
            hand.finalize(row, channel, os.path.join(tmp, VIDEO + ".mp4"), tmp)

            after = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual(after["status"], db.DOWNLOADED)
            self.assertEqual(after["file_size"], 4096)
            self.assertTrue(os.path.exists(after["file_path"]))
            self.assertIsNotNone(after["downloaded_at"])

    def test_the_scratch_folder_is_gone_afterwards(self):
        with harness.temp_app(min_free_gb=0) as app:
            row = queued_row(app)
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, VIDEO + ".mp4"), size=1024)

            hand = worker.Worker(runtime(app))
            channel = db.get_channel(app.conn, row["channel_row_id"])
            hand.finalize(row, channel, os.path.join(tmp, VIDEO + ".mp4"), tmp)

            self.assertFalse(os.path.exists(tmp))

    def test_a_non_mp4_result_keeps_its_real_extension(self):
        with harness.temp_app(min_free_gb=0) as app:
            row = queued_row(app)
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, VIDEO + ".mkv"), size=2048)

            hand = worker.Worker(runtime(app))
            channel = db.get_channel(app.conn, row["channel_row_id"])
            final = hand.finalize(row, channel,
                                  os.path.join(tmp, VIDEO + ".mkv"), tmp)

            self.assertTrue(final.endswith(".mkv"))
            self.assertTrue(os.path.exists(final))


class EndToEndTest(unittest.TestCase):

    def setUp(self):
        LIBRARY_CHANGED.clear()

    def tearDown(self):
        LIBRARY_CHANGED.clear()
        worker.set_current(None)

    def test_a_scripted_download_runs_all_the_way_into_the_library(self):
        with harness.temp_app(min_free_gb=0) as app:
            queued_row(app)
            app.scenario([harness.rule_download(VIDEO, size=8192,
                                                match=["--merge-output-format"])])

            hand = worker.Worker(runtime(app))
            hand._once()

            after = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual(after["status"], db.DOWNLOADED)
            self.assertEqual(after["file_size"], 8192)
            self.assertTrue(os.path.exists(after["file_path"]))
            self.assertTrue(os.path.exists(
                after["file_path"][:-len(".mp4")] + ".en.srt"))
            self.assertTrue(LIBRARY_CHANGED.is_set())
            self.assertEqual(hand.completed, 1)

    def test_exit_zero_with_no_media_is_a_failure_not_a_success(self):
        # max_attempts=1 so the attempts ladder from 5.6 lands on FAILED in
        # one pass; the point here is that it is never counted as a success.
        with harness.temp_app(min_free_gb=0, max_attempts=1) as app:
            queued_row(app)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 0,
                           "progress_lines": ["[download] 100% of 1.00MiB"]}])

            worker.Worker(runtime(app))._once()

            after = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual(after["status"], db.FAILED)
            self.assertIn("produced no media file", after["error"])
            self.assertFalse(LIBRARY_CHANGED.is_set())

    def test_a_failed_download_leaves_the_library_untouched(self):
        with harness.temp_app(min_free_gb=0, max_attempts=1) as app:
            queued_row(app)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "stdout": "ERROR: fragment not found"}])

            worker.Worker(runtime(app))._once()

            after = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual(after["status"], db.FAILED)
            channel_folder = os.path.join(app.cfg["library_root"],
                                          "Alpha Channel")
            self.assertFalse(os.path.exists(channel_folder)
                             and os.listdir(channel_folder))

    def test_progress_reaches_the_state_snapshot(self):
        seen = []

        class Peeking(worker.Worker):
            def _progress(self, update):
                worker.Worker._progress(self, update)
                seen.append(worker.current())

        with harness.temp_app(min_free_gb=0) as app:
            queued_row(app)
            app.scenario([harness.rule_download(VIDEO,
                                                match=["--merge-output-format"])])
            Peeking(runtime(app))._once()

        self.assertTrue(seen)
        self.assertEqual(seen[-1]["video_id"], VIDEO)
        self.assertEqual(seen[-1]["title"], "A video")
        self.assertIn("embedding thumbnail", [s["stage"] for s in seen])


if __name__ == "__main__":
    unittest.main()
