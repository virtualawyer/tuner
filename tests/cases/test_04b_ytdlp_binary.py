"""Task 2.1 -- the fake binary, the harness, and binary discovery."""

import os
import unittest

import harness
from tuner import ytdlp


class FakeBinaryTest(unittest.TestCase):

    def test_version_returns_the_scripted_string(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_version("2026.08.01")])
            self.assertEqual(ytdlp.version(app.cfg), "2026.08.01")
            self.assertTrue(ytdlp.available(app.cfg))

    def test_missing_binary_yields_none_not_an_exception(self):
        with harness.temp_app(ytdlp_path="/nonexistent/yt-dlp") as app:
            self.assertIsNone(ytdlp.version(app.cfg))
            self.assertFalse(ytdlp.available(app.cfg))

    def test_nonzero_exit_yields_none(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["--version"], "returncode": 3,
                           "stderr": "broken"}])
            self.assertIsNone(ytdlp.version(app.cfg))

    def test_run_never_uses_a_shell(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_version()])
            ytdlp.version(app.cfg)
            call = app.last_call()
            self.assertEqual(call, ["--version"])

    def test_calls_are_recorded_in_order(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_version()])
            ytdlp.version(app.cfg)
            ytdlp.version(app.cfg)
            self.assertEqual(len(app.calls()), 2)

    def test_unmatched_invocation_falls_through_to_default(self):
        with harness.temp_app() as app:
            app.scenario([], default={"returncode": 1, "stderr": "nope"})
            code, out, err = ytdlp._run(app.cfg, ["--something-else"])
            self.assertEqual(code, 1)
            self.assertIn("nope", err)


class HarnessTest(unittest.TestCase):

    def test_temp_app_is_isolated_and_cleans_up(self):
        with harness.temp_app() as app:
            tmp = app.tmp
            self.assertTrue(os.path.isdir(app.cfg["library_root"]))
            self.assertTrue(os.path.exists(app.cfg["db_path"]))
            # The database must never sit under the library root.
            self.assertFalse(
                app.cfg["db_path"].startswith(app.cfg["library_root"]))
        self.assertFalse(os.path.exists(tmp))

    def test_fake_creates_files_in_the_home_path(self):
        with harness.temp_app() as app:
            target = os.path.join(app.tmp, "out")
            app.scenario([harness.rule_download("vid00000001", size=2048)])
            code, _, _ = ytdlp._run(app.cfg, [
                "--merge-output-format", "mp4",
                "--paths", "home:" + target,
                "https://www.youtube.com/watch?v=vid00000001",
            ])
            self.assertEqual(code, 0)
            produced = sorted(os.listdir(target))
            self.assertEqual(produced, ["vid00000001.en.srt", "vid00000001.mp4"])
            self.assertEqual(
                os.path.getsize(os.path.join(target, "vid00000001.mp4")), 2048)

    def test_harness_helpers_seed_rows(self):
        with harness.temp_app() as app:
            chan = app.add_channel(channel_id="UC_x", title="Ex")
            app.add_video("vidx0000001", chan, "One")
            self.assertIsNotNone(chan)
            self.assertEqual(len(app.events()), 0)


if __name__ == "__main__":
    unittest.main()
