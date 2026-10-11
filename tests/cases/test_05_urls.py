"""Task 2.2 -- every URL form the add field accepts, and what it rejects."""

import unittest

from tuner import urls

CHANNEL = "UCabcdefghijklmnopqrstuv"   # 24 characters, the real shape
VIDEO = "dQw4w9WgXcQ"                  # 11 characters, the real shape


class ParseTest(unittest.TestCase):

    def assertParses(self, raw, kind, value):
        got = urls.parse(raw)
        self.assertEqual(got["kind"], kind, "%s -> %s" % (raw, got))
        self.assertEqual(got["value"], value, "%s -> %s" % (raw, got))
        self.assertTrue(got["canonical_url"].startswith("https://"))

    def test_every_accepted_form(self):
        cases = [
            # (input, kind, value)
            ("https://www.youtube.com/channel/" + CHANNEL, urls.CHANNEL_ID, CHANNEL),
            ("https://youtube.com/@veritasium", urls.HANDLE, "@veritasium"),
            ("https://www.youtube.com/c/Computerphile", urls.LEGACY, "Computerphile"),
            ("https://www.youtube.com/user/numberphile", urls.LEGACY, "numberphile"),
            ("https://www.youtube.com/@veritasium/videos", urls.HANDLE, "@veritasium"),
            ("https://www.youtube.com/c/Computerphile/streams", urls.LEGACY, "Computerphile"),
            ("https://www.youtube.com/watch?v=" + VIDEO, urls.VIDEO, VIDEO),
            ("https://youtu.be/" + VIDEO, urls.VIDEO, VIDEO),
            ("@veritasium", urls.HANDLE, "@veritasium"),
            (CHANNEL, urls.CHANNEL_ID, CHANNEL),
            ("www.youtube.com/@veritasium", urls.HANDLE, "@veritasium"),
        ]
        for raw, kind, value in cases:
            self.assertParses(raw, kind, value)

    def test_channel_tabs_are_stripped(self):
        for tab in ("videos", "streams", "shorts", "featured", "about",
                    "playlists", "community"):
            self.assertParses("https://www.youtube.com/@ch/" + tab,
                              urls.HANDLE, "@ch")

    def test_shorts_url_is_a_video_not_a_channel_tab(self):
        # /@handle/shorts is a tab; /shorts/<id> is a video. The difference
        # matters: one adds a channel, the other would add nothing.
        self.assertParses("https://www.youtube.com/shorts/" + VIDEO,
                          urls.VIDEO, VIDEO)
        self.assertParses("https://www.youtube.com/@ch/shorts",
                          urls.HANDLE, "@ch")

    def test_live_and_embed_forms_are_videos(self):
        self.assertParses("https://www.youtube.com/live/" + VIDEO, urls.VIDEO, VIDEO)
        self.assertParses("https://www.youtube.com/embed/" + VIDEO, urls.VIDEO, VIDEO)

    def test_extra_query_and_fragment_are_ignored(self):
        self.assertParses(
            "https://www.youtube.com/watch?v=%s&t=42s&list=PL123#frag" % VIDEO,
            urls.VIDEO, VIDEO)
        self.assertParses("https://www.youtube.com/@ch?sub_confirmation=1",
                          urls.HANDLE, "@ch")

    def test_mobile_host_and_whitespace(self):
        self.assertParses("  https://m.youtube.com/@ch  ", urls.HANDLE, "@ch")

    def test_a_feed_url_round_trips(self):
        self.assertParses(urls.feed_url(CHANNEL), urls.CHANNEL_ID, CHANNEL)

    def test_canonical_urls(self):
        self.assertEqual(urls.parse("@ch")["canonical_url"],
                         "https://www.youtube.com/@ch")
        self.assertEqual(urls.parse(CHANNEL)["canonical_url"],
                         "https://www.youtube.com/channel/" + CHANNEL)
        self.assertEqual(
            urls.parse("https://www.youtube.com/user/numberphile")["canonical_url"],
            "https://www.youtube.com/user/numberphile")
        self.assertEqual(
            urls.parse("https://www.youtube.com/c/Computerphile")["canonical_url"],
            "https://www.youtube.com/c/Computerphile")


class RejectTest(unittest.TestCase):

    def assertRejected(self, raw):
        got = urls.parse(raw)
        self.assertEqual(got["kind"], urls.UNKNOWN, "%s -> %s" % (raw, got))
        self.assertIsNone(got["value"])
        self.assertIn("reason", got)

    def test_non_youtube_host(self):
        self.assertRejected("https://vimeo.com/12345")
        self.assertRejected("https://youtube.com.evil.test/@ch")
        self.assertRejected("https://notyoutube.com/@ch")

    def test_empty_and_nonsense(self):
        self.assertRejected("")
        self.assertRejected("   ")
        self.assertRejected(None)
        self.assertRejected("just some words")

    def test_youtube_pages_that_are_not_channels(self):
        self.assertRejected("https://www.youtube.com/results?search_query=cats")
        self.assertRejected("https://www.youtube.com/")
        self.assertRejected("https://www.youtube.com/watch?v=tooshort")
        self.assertRejected("https://www.youtube.com/channel/")

    def test_bare_word_is_not_a_channel_id(self):
        # Only a full 24 character UC id counts as a bare id.
        self.assertRejected("UCshort")


class BuilderTest(unittest.TestCase):

    def test_feed_url(self):
        self.assertEqual(
            urls.feed_url(CHANNEL),
            "https://www.youtube.com/feeds/videos.xml?channel_id=" + CHANNEL)

    def test_watch_url(self):
        self.assertEqual(urls.watch_url(VIDEO),
                         "https://www.youtube.com/watch?v=" + VIDEO)


if __name__ == "__main__":
    unittest.main()
