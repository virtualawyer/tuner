"""Task 2.4 -- the metadata probe and its failure classification."""

import json
import unittest

import harness
from tuner import ytdlp

VIDEO = "dQw4w9WgXcQ"


class ProbeTest(unittest.TestCase):

    def test_realistic_payload_normalizes(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_probe(
                video_id=VIDEO, title="How a Chromebook became a NAS",
                duration=1284, width=1920, height=1080,
                channel_id="UCabcdefghijklmnopqrstuv", channel="Alpha",
                upload_date="20260801", match=["-J"])])

            meta = ytdlp.probe(app.cfg, VIDEO)

            self.assertEqual(meta["id"], VIDEO)
            self.assertEqual(meta["title"], "How a Chromebook became a NAS")
            self.assertEqual(meta["duration"], 1284)
            self.assertEqual(meta["width"], 1920)
            self.assertEqual(meta["height"], 1080)
            self.assertEqual(meta["live_status"], "not_live")
            self.assertFalse(meta["was_live"])
            self.assertEqual(meta["upload_date"], "20260801")
            self.assertEqual(meta["channel"], "Alpha")

    def test_probe_asks_for_json_without_the_playlist(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_probe(video_id=VIDEO, match=["-J"])])
            ytdlp.probe(app.cfg, VIDEO)

            call = app.last_call()
            self.assertIn("-J", call)
            self.assertIn("--no-playlist", call)
            self.assertIn("https://www.youtube.com/watch?v=" + VIDEO, call)

    def test_only_the_documented_fields_survive(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_probe(
                video_id=VIDEO, match=["-J"],
                extra={"formats": [{"huge": "payload"}], "thumbnails": [1, 2],
                       "automatic_captions": {"en": []}})])

            meta = ytdlp.probe(app.cfg, VIDEO)
            self.assertEqual(set(meta.keys()), set(ytdlp.PROBE_FIELDS))
            self.assertNotIn("formats", meta)

    def test_float_duration_is_coerced_to_int(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["-J"], "returncode": 0,
                           "stdout": json.dumps({"id": VIDEO, "title": "T",
                                                 "duration": 59.6})}])
            self.assertEqual(ytdlp.probe(app.cfg, VIDEO)["duration"], 59)

    def test_null_duration_and_missing_live_status_are_tolerated(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["-J"], "returncode": 0,
                           "stdout": json.dumps({"id": VIDEO, "title": "Live",
                                                 "duration": None})}])
            meta = ytdlp.probe(app.cfg, VIDEO)
            self.assertIsNone(meta["duration"])
            self.assertEqual(meta["live_status"], "not_live")

    def test_missing_title_falls_back(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["-J"], "returncode": 0,
                           "stdout": json.dumps({"id": VIDEO})}])
            self.assertEqual(ytdlp.probe(app.cfg, VIDEO)["title"], "(untitled)")


class ClassificationTest(unittest.TestCase):

    def assertPermanent(self, stderr, reason):
        permanent, got = ytdlp.classify_failure(stderr)
        self.assertTrue(permanent, stderr)
        self.assertEqual(got, reason)

    def assertTransient(self, stderr, reason=None):
        permanent, got = ytdlp.classify_failure(stderr)
        self.assertFalse(permanent, stderr)
        if reason:
            self.assertEqual(got, reason)

    def test_permanent_patterns(self):
        self.assertPermanent("ERROR: Private video. Sign in if you've been "
                             "granted access to this video", "private")
        self.assertPermanent("ERROR: Join this channel to get access to "
                             "members-only content", "members-only")
        self.assertPermanent("ERROR: Sign in to confirm your age. This video "
                             "may be inappropriate for some users.",
                             "age-restricted")
        self.assertPermanent("ERROR: Video unavailable", "unavailable")
        self.assertPermanent("ERROR: This video has been removed by the "
                             "uploader", "removed")

    def test_bot_check_is_transient_not_permanent(self):
        # A rate limit reads like an access failure. Treating it as
        # permanent would skip real videos for good.
        self.assertTransient("ERROR: Sign in to confirm you're not a bot",
                             "rate-limited")
        self.assertTransient("HTTP Error 429: Too Many Requests",
                             "rate-limited")

    def test_ordinary_failures_are_transient(self):
        self.assertTransient("ERROR: unable to download video data: "
                             "HTTP Error 503", "error")
        self.assertTransient("", "error")

    def test_timeout_is_transient(self):
        permanent, reason = ytdlp.classify_failure("yt-dlp timed out", code=124)
        self.assertFalse(permanent)
        self.assertEqual(reason, "timeout")


class ProbeFailureTest(unittest.TestCase):

    def test_private_video_raises_permanent_with_reason(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["-J"], "returncode": 1,
                           "stderr": "ERROR: Private video"}])
            with self.assertRaises(ytdlp.PermanentProbeError) as caught:
                ytdlp.probe(app.cfg, VIDEO)
            self.assertEqual(caught.exception.reason, "private")

    def test_network_failure_raises_transient(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["-J"], "returncode": 1,
                           "stderr": "ERROR: HTTP Error 503"}])
            with self.assertRaises(ytdlp.TransientProbeError):
                ytdlp.probe(app.cfg, VIDEO)

    def test_malformed_json_is_transient_not_a_crash(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["-J"], "returncode": 0,
                           "stdout": "{not json"}])
            with self.assertRaises(ytdlp.TransientProbeError) as caught:
                ytdlp.probe(app.cfg, VIDEO)
            self.assertEqual(caught.exception.reason, "malformed")

    def test_empty_output_is_transient(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["-J"], "returncode": 0, "stdout": ""}])
            with self.assertRaises(ytdlp.TransientProbeError) as caught:
                ytdlp.probe(app.cfg, VIDEO)
            self.assertEqual(caught.exception.reason, "empty")

    def test_missing_binary_is_transient(self):
        with harness.temp_app(ytdlp_path="/nonexistent/yt-dlp") as app:
            with self.assertRaises(ytdlp.TransientProbeError):
                ytdlp.probe(app.cfg, VIDEO)


if __name__ == "__main__":
    unittest.main()
