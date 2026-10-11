"""Not every non-zero exit is a lost video.

From a real run of 97 failures: 12 were a subtitle fetch getting a 429
after the video itself had downloaded perfectly, and 4 were premieres with
a start time that burned three attempts each.
"""

import os
import unittest

import harness
from tuner import db, paths, server, worker, ytdlp

VIDEO = "dQw4w9WgXcQ"


def runtime(app):
    return server.App(app.cfg, app.conn)


def queued_row(app):
    chan = app.add_channel(channel_id="UC_a", title="Alpha Channel",
                           url="https://www.youtube.com/channel/UC_a",
                           folder="Alpha Channel")
    app.add_video(VIDEO, chan, "A video", published_at="2026-08-11T12:00:00Z")
    row = db.get_video_by_ytid(app.conn, VIDEO)
    db.set_video_status(app.conn, row["id"], db.QUEUED)
    return db.get_video_by_ytid(app.conn, VIDEO)


class FindMediaTest(unittest.TestCase):

    def test_the_merged_file_wins_over_its_source_streams(self):
        # A merge leaves <id>.f137.mp4 (video only) behind. Picking that
        # would file a silent video into the library.
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, VIDEO + ".f137.mp4"), size=9000)
            app.make_file(os.path.join(tmp, VIDEO + ".mp4"), size=100)

            found = worker.find_media(tmp, VIDEO)
            self.assertEqual(os.path.basename(found), VIDEO + ".mp4")

    def test_exact_only_refuses_a_half_finished_merge(self):
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, VIDEO + ".f137.mp4"), size=9000)

            self.assertIsNone(worker.find_media(tmp, VIDEO, exact_only=True))
            # Without the guard the stream file would look like a result.
            self.assertIsNotNone(worker.find_media(tmp, VIDEO))

    def test_the_old_largest_file_behaviour_still_works(self):
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, "something.mp4"), size=500)
            self.assertIsNotNone(worker.find_media(tmp))


class SalvageTest(unittest.TestCase):

    def tearDown(self):
        worker.set_current(None)

    def test_a_rate_limited_subtitle_does_not_throw_away_the_video(self):
        with harness.temp_app(min_free_gb=0, max_attempts=1) as app:
            queued_row(app)
            app.scenario([{
                "match": ["--merge-output-format"],
                "returncode": 1,
                "creates": ["%(id)s.mp4"],
                "creates_size": 4096,
                "stdout": "[download] 100% of 10.00MiB",
                "stderr": "ERROR: Unable to download video subtitles for 'en': "
                          "HTTP Error 429: Too Many Requests",
            }])

            worker.Worker(runtime(app))._once()

            row = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual(row["status"], db.DOWNLOADED)
            self.assertTrue(os.path.exists(row["file_path"]))
            self.assertTrue(any("kept the video despite an error" in m
                                for m in app.events(level="warn")))

    def test_the_warning_names_what_went_wrong(self):
        with harness.temp_app(min_free_gb=0, max_attempts=1) as app:
            queued_row(app)
            app.scenario([{
                "match": ["--merge-output-format"], "returncode": 1,
                "creates": ["%(id)s.mp4"], "creates_size": 2048,
                "stderr": "ERROR: Unable to download video subtitles for 'en': "
                          "HTTP Error 429: Too Many Requests",
            }])
            worker.Worker(runtime(app))._once()

            self.assertTrue(any("429" in m for m in app.events(level="warn")))

    def test_a_download_with_nothing_to_show_still_fails(self):
        with harness.temp_app(min_free_gb=0, max_attempts=1) as app:
            queued_row(app)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "stderr": "ERROR: unable to download video data: "
                                     "HTTP Error 403: Forbidden"}])

            worker.Worker(runtime(app))._once()

            self.assertEqual(db.get_video_by_ytid(app.conn, VIDEO)["status"],
                             db.FAILED)

    def test_a_half_merged_video_is_not_salvaged(self):
        # Only the source stream exists, so keeping it would file a video
        # with no audio.
        with harness.temp_app(min_free_gb=0, max_attempts=1) as app:
            queued_row(app)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "creates": ["%(id)s.f137.mp4"], "creates_size": 4096,
                           "stderr": "ERROR: ffmpeg exited with code 1"}])

            worker.Worker(runtime(app))._once()

            self.assertEqual(db.get_video_by_ytid(app.conn, VIDEO)["status"],
                             db.FAILED)


class UpcomingTest(unittest.TestCase):

    def test_a_premiere_is_parked_rather_than_retried(self):
        for message in (
                "ERROR: [youtube] AzDMkgzDP2Y: This live event will begin in "
                "113 minutes.",
                "ERROR: [youtube] i-J-eyPH8ec: This live event will begin in "
                "5 days."):
            permanent, reason = ytdlp.classify_failure(message)
            self.assertTrue(permanent, message)
            self.assertEqual(reason, "upcoming")

    def test_it_costs_no_attempts(self):
        with harness.temp_app(min_free_gb=0, max_attempts=3) as app:
            queued_row(app)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "stderr": "ERROR: [youtube] x: This live event "
                                     "will begin in 55 minutes."}])

            worker.Worker(runtime(app))._once()

            row = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual(row["status"], db.SKIPPED)
            self.assertEqual(row["skip_reason"], "upcoming")
            self.assertEqual(row["attempts"], 0)

    def test_a_bot_check_is_still_retried_not_parked(self):
        permanent, reason = ytdlp.classify_failure(
            "ERROR: [youtube] x: Sign in to confirm you're not a bot.")
        self.assertFalse(permanent)
        self.assertEqual(reason, "rate-limited")


class RequestPacingTest(unittest.TestCase):

    def test_the_download_asks_yt_dlp_to_slow_down(self):
        with harness.temp_app() as app:
            args = ytdlp.download_args(app.cfg, VIDEO, "/tmp/x")

        self.assertEqual(args[args.index("--sleep-requests") + 1], "1")
        self.assertEqual(args[args.index("--retries") + 1], "5")
        self.assertEqual(args[args.index("--fragment-retries") + 1], "10")


class CodecPreferenceTest(unittest.TestCase):
    """AV1 is smaller, and undecodable by older clients.

    When a client cannot decode a format Jellyfin transcodes on the server,
    which on a repurposed Chromebook is the one thing this build exists to
    avoid. H.264 direct-plays everywhere, so it is asked for first.
    """

    def chain(self, **overrides):
        with harness.temp_app(**overrides) as app:
            args = ytdlp.download_args(app.cfg, VIDEO, "/tmp/x")
        return args[args.index("--format") + 1]

    def test_h264_is_preferred_by_default(self):
        chain = self.chain()
        self.assertTrue(chain.startswith("bv*[height<=1080][vcodec^=avc1]"))

    def test_it_still_falls_back_to_any_codec(self):
        # A channel that publishes only AV1 must still download.
        chain = self.chain()
        self.assertIn("bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b", chain)

    def test_the_ceiling_holds_in_every_branch(self):
        for branch in self.chain().split("/"):
            if branch in ("bv*+ba", "b"):
                continue          # the last-resort catch-all
            self.assertIn("height<=1080", branch, branch)

    def test_the_preference_can_be_switched_off(self):
        chain = self.chain(prefer_h264=False)
        self.assertNotIn("avc1", chain)
        self.assertEqual(chain, ytdlp.FORMAT_CHAIN)

    def test_it_is_editable_from_settings(self):
        with harness.temp_app() as app:
            changed, problems = server.apply_settings(
                app.cfg, {"prefer_h264": False})
            self.assertEqual(problems, [])
            self.assertIn("prefer_h264", changed)
            self.assertFalse(app.cfg["prefer_h264"])


if __name__ == "__main__":
    unittest.main()
