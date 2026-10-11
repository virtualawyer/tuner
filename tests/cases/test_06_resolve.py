"""Task 2.3 -- resolving a pasted reference to a channel id.

Channel resolution reads the **playlist level** of a -J payload. An earlier
version printed a per-entry template under --flat-playlist and got NA|NA
back for ordinary handles, because flat video stubs carry no channel_id.
The regression tests below pin that shut.
"""

import json
import unittest

import harness
from tuner import urls, ytdlp

CHANNEL = "UCabcdefghijklmnopqrstuv"


class ResolveTest(unittest.TestCase):

    def test_a_handle_resolves_from_the_playlist_level(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha Channel")])
            got = ytdlp.resolve_channel(app.cfg, urls.parse("@alpha"))

            self.assertEqual(got["channel_id"], CHANNEL)
            self.assertEqual(got["title"], "Alpha Channel")
            self.assertEqual(got["canonical_url"],
                             "https://www.youtube.com/channel/" + CHANNEL)

    def test_it_asks_for_channel_metadata_not_a_video_stub(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(CHANNEL, "Alpha")])
            ytdlp.resolve_channel(app.cfg, urls.parse("@alpha"))

            call = app.last_call()
            self.assertIn("-J", call)
            self.assertIn("--flat-playlist", call)
            # 0 selects no entries: one quick request for the channel's own
            # metadata rather than an extraction of its newest video.
            self.assertEqual(call[call.index("--playlist-items") + 1], "0")
            self.assertIn("https://www.youtube.com/@alpha", call)
            self.assertNotIn("--print", call)

    def test_the_id_is_taken_from_the_playlist_id_when_channel_id_is_absent(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(payload={
                "_type": "playlist", "id": CHANNEL,
                "title": "Alpha Channel - Videos", "entries": []})])

            got = ytdlp.resolve_channel(app.cfg, urls.parse("@alpha"))
            self.assertEqual(got["channel_id"], CHANNEL)
            # The tab suffix is not part of the channel name.
            self.assertEqual(got["title"], "Alpha Channel")

    def test_a_bare_flat_playlist_falls_back_to_pulling_one_entry(self):
        # Regression: the payload that produced NA|NA. The first attempt
        # carries no usable id, so a second attempt asks for one entry.
        with harness.temp_app() as app:
            app.scenario([
                {"match": ["--playlist-items", "0"], "returncode": 0,
                 "stdout": json.dumps({"_type": "playlist",
                                       "id": "videos", "title": "Videos",
                                       "entries": []})},
                {"match": ["--playlist-items", "1"], "returncode": 0,
                 "stdout": json.dumps({
                     "_type": "playlist", "id": "videos", "title": "Videos",
                     "entries": [{"_type": "url", "id": "vid00000001",
                                  "channel_id": CHANNEL,
                                  "channel": "Alpha Channel"}]})},
            ])

            got = ytdlp.resolve_channel(app.cfg, urls.parse("@alpha"))

            self.assertEqual(got["channel_id"], CHANNEL)
            self.assertEqual(len(app.calls()), 2)

    def test_the_handle_is_never_mistaken_for_a_channel_id(self):
        # uploader_id is "@name" on a handle URL, which is not an id.
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(payload={
                "_type": "playlist", "id": "videos",
                "uploader_id": "@alpha", "uploader": "Alpha Channel",
                "entries": []})])

            with self.assertRaises(ytdlp.ResolveError):
                ytdlp.resolve_channel(app.cfg, urls.parse("@alpha"))

    def test_channel_id_url_skips_the_subprocess_entirely(self):
        with harness.temp_app() as app:
            app.scenario([])   # any invocation would fail
            got = ytdlp.resolve_channel(
                app.cfg, urls.parse("https://www.youtube.com/channel/" + CHANNEL))

            self.assertEqual(got["channel_id"], CHANNEL)
            # Title is unknown here on purpose; the feed supplies it.
            self.assertIsNone(got["title"])
            self.assertEqual(app.calls(), [])

    def test_video_url_resolves_its_parent_channel_without_the_playlist(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(
                CHANNEL, "Alpha", match=["-J", "--no-playlist"],
                payload={"id": "dQw4w9WgXcQ", "channel_id": CHANNEL,
                         "channel": "Alpha", "title": "Some video"})])

            got = ytdlp.resolve_channel(
                app.cfg, urls.parse("https://youtu.be/dQw4w9WgXcQ"))

            self.assertEqual(got["channel_id"], CHANNEL)
            self.assertEqual(got["title"], "Alpha")
            call = app.last_call()
            self.assertIn("--no-playlist", call)
            self.assertNotIn("--flat-playlist", call)

    def test_a_title_containing_a_pipe_survives(self):
        # The old format split on a pipe, so this used to truncate.
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(CHANNEL, "News | Daily")])
            got = ytdlp.resolve_channel(app.cfg, urls.parse("@news"))
            self.assertEqual(got["title"], "News | Daily")

    def test_a_missing_title_is_reported_as_unknown(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(payload={
                "_type": "playlist", "id": CHANNEL, "entries": []})])
            got = ytdlp.resolve_channel(app.cfg, urls.parse("@alpha"))
            self.assertEqual(got["channel_id"], CHANNEL)
            self.assertIsNone(got["title"])

    def test_na_fields_are_treated_as_absent(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(payload={
                "_type": "playlist", "id": CHANNEL, "channel_id": "NA",
                "channel": "NA", "entries": []})])

            got = ytdlp.resolve_channel(app.cfg, urls.parse("@alpha"))
            self.assertEqual(got["channel_id"], CHANNEL)
            self.assertIsNone(got["title"])


class ResolveFailureTest(unittest.TestCase):

    def test_nonzero_exit_raises_with_the_stderr_attached(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["-J"], "returncode": 1,
                           "stderr": "ERROR: Unable to recognize tab page"}])
            with self.assertRaises(ytdlp.ResolveError) as caught:
                ytdlp.resolve_channel(app.cfg, urls.parse("@ghost"))
            self.assertIn("Unable to recognize tab page", str(caught.exception))

    def test_empty_output_raises(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["-J"], "stdout": "", "returncode": 0}])
            with self.assertRaises(ytdlp.ResolveError):
                ytdlp.resolve_channel(app.cfg, urls.parse("@ghost"))

    def test_unreadable_json_raises(self):
        with harness.temp_app() as app:
            app.scenario([{"match": ["-J"], "stdout": "{not json",
                           "returncode": 0}])
            with self.assertRaises(ytdlp.ResolveError):
                ytdlp.resolve_channel(app.cfg, urls.parse("@ghost"))

    def test_a_payload_with_no_id_anywhere_raises(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_resolve(payload={
                "_type": "playlist", "id": "videos", "title": "Videos",
                "entries": []})])
            with self.assertRaises(ytdlp.ResolveError) as caught:
                ytdlp.resolve_channel(app.cfg, urls.parse("@ghost"))
            self.assertIn("no channel id", str(caught.exception))

    def test_missing_binary_raises_resolve_error(self):
        with harness.temp_app(ytdlp_path="/nonexistent/yt-dlp") as app:
            with self.assertRaises(ytdlp.ResolveError) as caught:
                ytdlp.resolve_channel(app.cfg, urls.parse("@alpha"))
            self.assertIn("not found", str(caught.exception))

    def test_unknown_reference_raises_before_any_subprocess(self):
        with harness.temp_app() as app:
            with self.assertRaises(ytdlp.ResolveError):
                ytdlp.resolve_channel(app.cfg, urls.parse("https://vimeo.com/1"))
            self.assertEqual(app.calls(), [])


if __name__ == "__main__":
    unittest.main()
