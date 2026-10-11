"""Task 3.2 -- reading the Atom feed."""

import unittest

import harness
from tuner import feed

CHANNEL = "UCabcdefghijklmnopqrstuv"


class ParseTest(unittest.TestCase):

    def setUp(self):
        self.body = harness.fixture("feed_sample.xml")
        self.parsed = feed.parse(self.body)

    def test_channel_identity(self):
        self.assertEqual(self.parsed["channel_id"], CHANNEL)
        self.assertEqual(self.parsed["channel_title"], "Alpha Channel")

    def test_all_fifteen_entries_are_read(self):
        # YouTube caps the feed at 15, which is the whole point of the
        # seed-then-track-forward design.
        self.assertEqual(len(self.parsed["entries"]), 15)

    def test_entry_shape(self):
        entry = self.parsed["entries"][0]
        for key in ("video_id", "title", "published", "updated", "thumbnail"):
            self.assertIn(key, entry)
        self.assertTrue(entry["thumbnail"].startswith("https://"))

    def test_entries_come_back_newest_first(self):
        published = [entry["published"] for entry in self.parsed["entries"]]
        self.assertEqual(published, sorted(published, reverse=True))
        self.assertEqual(self.parsed["entries"][0]["video_id"], "vid00000001")

    def test_escaped_ampersand_and_angle_brackets_survive(self):
        titles = [entry["title"] for entry in self.parsed["entries"]]
        self.assertIn("Fixing exFAT & the case of the vanishing permissions",
                      titles)
        self.assertIn("Docker bind mounts <vs> volumes", titles)

    def test_unicode_title_survives(self):
        titles = [entry["title"] for entry in self.parsed["entries"]]
        self.assertIn("Wir bauen einen Medienserver -- Folge 3: Übertragung",
                      titles)

    def test_video_ids_are_unique(self):
        ids = [entry["video_id"] for entry in self.parsed["entries"]]
        self.assertEqual(len(ids), len(set(ids)))


class ParseFailureTest(unittest.TestCase):

    def test_malformed_xml_raises_feed_error(self):
        with self.assertRaises(feed.FeedError) as caught:
            feed.parse("<feed><entry></feed>")
        self.assertIn("not valid XML", str(caught.exception))

    def test_empty_body_raises_feed_error(self):
        with self.assertRaises(feed.FeedError):
            feed.parse("")
        with self.assertRaises(feed.FeedError):
            feed.parse(None)

    def test_feed_with_no_entries_parses_to_an_empty_list(self):
        body = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<feed xmlns="http://www.w3.org/2005/Atom"'
                ' xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
                '<yt:channelId>%s</yt:channelId>'
                '<title>Quiet Channel</title>'
                '</feed>' % CHANNEL)
        parsed = feed.parse(body)
        self.assertEqual(parsed["entries"], [])
        self.assertEqual(parsed["channel_title"], "Quiet Channel")

    def test_entry_without_a_video_id_is_skipped(self):
        body = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<feed xmlns="http://www.w3.org/2005/Atom"'
                ' xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
                '<title>Odd Channel</title>'
                '<entry><title>No id here</title></entry>'
                '<entry><yt:videoId>vid00000001</yt:videoId>'
                '<title>Real one</title></entry>'
                '</feed>')
        parsed = feed.parse(body)
        self.assertEqual(len(parsed["entries"]), 1)
        self.assertEqual(parsed["entries"][0]["video_id"], "vid00000001")

    def test_entry_without_a_title_falls_back(self):
        body = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<feed xmlns="http://www.w3.org/2005/Atom"'
                ' xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
                '<title>C</title>'
                '<entry><yt:videoId>vid00000001</yt:videoId></entry>'
                '</feed>')
        self.assertEqual(feed.parse(body)["entries"][0]["title"], "(untitled)")


if __name__ == "__main__":
    unittest.main()
