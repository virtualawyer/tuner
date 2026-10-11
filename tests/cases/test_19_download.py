"""Task 5.4 -- the download invocation and progress parsing."""

import os
import unittest

import harness
from tuner import paths, ytdlp

VIDEO = "dQw4w9WgXcQ"


class ArgumentTest(unittest.TestCase):

    def args(self, **overrides):
        with harness.temp_app(**overrides) as app:
            return ytdlp.download_args(app.cfg, VIDEO, "/tmp/tuner-x")

    def test_the_quality_ceiling_and_container(self):
        args = self.args()
        chain = args[args.index("--format") + 1]
        self.assertIn("height<=1080", chain)
        self.assertEqual(args[args.index("--merge-output-format") + 1], "mp4")
        self.assertEqual(args[args.index("--remux-video") + 1], "mp4")

    def test_exfat_safe_filenames_are_requested(self):
        args = self.args()
        self.assertIn("--windows-filenames", args)
        self.assertEqual(args[args.index("--trim-filenames") + 1], "200")

    def test_subtitles_and_embedding_are_on_by_default(self):
        args = self.args()
        self.assertIn("--write-subs", args)
        self.assertIn("--write-auto-subs", args)
        self.assertEqual(args[args.index("--sub-langs") + 1], "en.*,-live_chat")
        self.assertEqual(args[args.index("--convert-subs") + 1], "srt")
        self.assertIn("--embed-thumbnail", args)
        self.assertIn("--embed-metadata", args)

    def test_subtitles_can_be_switched_off(self):
        args = self.args(subtitles=False)
        for flag in ("--write-subs", "--write-auto-subs", "--sub-langs",
                     "--convert-subs"):
            self.assertNotIn(flag, args)
        # Embedding is unaffected.
        self.assertIn("--embed-metadata", args)

    def test_output_goes_to_the_temp_directory(self):
        args = self.args()
        self.assertIn("home:/tmp/tuner-x", args)
        self.assertIn("temp:/tmp/tuner-x", args)
        self.assertEqual(args[args.index("--output") + 1], "%(id)s.%(ext)s")

    def test_the_watch_url_is_last(self):
        self.assertEqual(self.args()[-1],
                         "https://www.youtube.com/watch?v=" + VIDEO)

    def test_no_playlist_and_no_user_config(self):
        args = self.args()
        self.assertIn("--no-playlist", args)
        self.assertIn("--ignore-config", args)
        self.assertIn("--newline", args)


class ParseProgressTest(unittest.TestCase):

    def test_a_normal_progress_line(self):
        update = ytdlp.parse_progress(
            "[download]   4.2% of   12.34MiB at    1.23MiB/s ETA 00:09")
        self.assertEqual(update["percent"], 4.2)
        self.assertEqual(update["size"], "12.34MiB")
        self.assertEqual(update["rate"], "1.23MiB/s")
        self.assertEqual(update["eta"], "00:09")
        self.assertEqual(update["stage"], "downloading")

    def test_an_estimated_size(self):
        update = ytdlp.parse_progress(
            "[download]  50.0% of ~ 120.00MiB at    2.00MiB/s ETA 00:30")
        self.assertEqual(update["percent"], 50.0)
        self.assertEqual(update["size"], "120.00MiB")

    def test_the_completion_line(self):
        update = ytdlp.parse_progress(
            "[download] 100% of   10.00MiB in 00:05")
        self.assertEqual(update["percent"], 100.0)
        self.assertEqual(update["eta"], "00:05")

    def test_unknown_rate_and_eta_become_none(self):
        update = ytdlp.parse_progress(
            "[download]   0.0% of   10.00MiB at  Unknown B/s ETA Unknown")
        self.assertIsNone(update["rate"])
        self.assertIsNone(update["eta"])

    def test_post_processing_lines_report_a_stage(self):
        for line, stage in (
                ('[Merger] Merging formats into "x.mp4"', "merging streams"),
                ("[EmbedThumbnail] mp4 cover art added", "embedding thumbnail"),
                ("[Metadata] Adding metadata to \"x.mp4\"", "writing metadata"),
                ("[SubtitlesConvertor] Converting subtitles",
                 "converting subtitles")):
            update = ytdlp.parse_progress(line)
            self.assertEqual(update, {"stage": stage}, line)

    def test_uninteresting_lines_parse_to_nothing(self):
        for line in ("[youtube] Extracting URL: https://...",
                     "[download] Destination: x.f137.mp4",
                     "[info] Downloading 1 format(s): 137+140",
                     "", "   "):
            self.assertIsNone(ytdlp.parse_progress(line))


class DownloadTest(unittest.TestCase):

    def test_a_scripted_download_reports_progress_and_stages(self):
        seen = []
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.scenario([harness.rule_download(VIDEO, match=["--merge-output-format"])])

            code, tail = ytdlp.download(app.cfg, VIDEO, tmp,
                                        on_progress=seen.append)

            self.assertEqual(code, 0)
            self.assertTrue(seen)

            percents = [u["percent"] for u in seen if "percent" in u]
            self.assertEqual(percents[-1], 100.0)

            stages = [u["stage"] for u in seen]
            self.assertIn("merging streams", stages)
            self.assertIn("embedding thumbnail", stages)

    def test_the_files_land_in_the_temp_directory(self):
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.scenario([harness.rule_download(VIDEO, size=2048,
                                                match=["--merge-output-format"])])

            ytdlp.download(app.cfg, VIDEO, tmp)

            produced = sorted(os.listdir(tmp))
            self.assertEqual(produced, ["%s.en.srt" % VIDEO, "%s.mp4" % VIDEO])

    def test_the_running_process_is_handed_back_for_termination(self):
        captured = {}
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.scenario([harness.rule_download(VIDEO, match=["--merge-output-format"])])

            ytdlp.download(app.cfg, VIDEO, tmp,
                           on_start=lambda proc: captured.setdefault("proc", proc))

            self.assertIn("proc", captured)
            self.assertIsNotNone(captured["proc"].pid)

    def test_a_failure_returns_the_code_and_the_tail(self):
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "stdout": "[youtube] Extracting URL",
                           "stderr": "ERROR: fragment 3 not found"}])

            code, tail = ytdlp.download(app.cfg, VIDEO, tmp)

            self.assertEqual(code, 1)
            self.assertIn("Extracting URL", tail)

    def test_the_tail_is_bounded(self):
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            noise = ["[download] line %d" % i for i in range(200)]
            app.scenario([{"match": ["--merge-output-format"], "returncode": 1,
                           "progress_lines": noise}])

            _, tail = ytdlp.download(app.cfg, VIDEO, tmp)
            self.assertLessEqual(len(tail.splitlines()), ytdlp.TAIL_LINES)
            self.assertIn("line 199", tail)

    def test_a_missing_binary_is_reported_not_raised(self):
        with harness.temp_app(ytdlp_path="/nonexistent/yt-dlp") as app:
            code, tail = ytdlp.download(app.cfg, VIDEO, "/tmp")
            self.assertEqual(code, 127)
            self.assertIn("not found", tail)

    def test_a_raising_progress_callback_does_not_break_the_download(self):
        def explode(update):
            raise RuntimeError("ui blew up")

        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.scenario([harness.rule_download(VIDEO, match=["--merge-output-format"])])

            code, _ = ytdlp.download(app.cfg, VIDEO, tmp, on_progress=explode)
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
