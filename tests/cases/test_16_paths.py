"""Task 5.1 -- exFAT-safe naming and the download's path plan."""

import os
import unittest

import harness
from tuner import db, paths

FORBIDDEN = '"*/:<>?\\|'
VIDEO = "dQw4w9WgXcQ"


class SanitizeTest(unittest.TestCase):

    def test_every_forbidden_character_is_replaced(self):
        for bad in FORBIDDEN:
            cleaned = paths.sanitize("before%safter" % bad)
            self.assertNotIn(bad, cleaned)
            self.assertTrue(cleaned.startswith("before"))

    def test_control_characters_are_replaced(self):
        self.assertNotIn("\n", paths.sanitize("two\nlines"))
        self.assertNotIn("\t", paths.sanitize("a\tb"))

    def test_whitespace_runs_collapse(self):
        self.assertEqual(paths.sanitize("too    many   spaces"),
                         "too many spaces")

    def test_leading_and_trailing_dots_and_spaces_go(self):
        self.assertEqual(paths.sanitize("  ...name...  "), "name")
        self.assertEqual(paths.sanitize(".hidden"), "hidden")

    def test_a_name_of_only_dots_falls_back(self):
        self.assertEqual(paths.sanitize("..."), "untitled")
        self.assertEqual(paths.sanitize("   "), "untitled")
        self.assertEqual(paths.sanitize(""), "untitled")
        self.assertEqual(paths.sanitize(None), "untitled")

    def test_unicode_is_preserved(self):
        self.assertEqual(paths.sanitize("Übertragung Folge 3"),
                         "Übertragung Folge 3")

    def test_sanitize_is_idempotent(self):
        once = paths.sanitize('a/b:c "d"')
        self.assertEqual(paths.sanitize(once), once)


class StemTest(unittest.TestCase):

    def test_shape(self):
        stem = paths.build_stem("A normal title", VIDEO, date="2026-08-11")
        self.assertEqual(stem, "2026-08-11 - A normal title [%s]" % VIDEO)

    def test_a_long_title_truncates_but_keeps_the_id(self):
        stem = paths.build_stem("x" * 400, VIDEO, date="2026-08-11")
        self.assertLessEqual(len(stem) + len(".mp4"), paths.MAX_FILENAME)
        self.assertTrue(stem.endswith("[%s]" % VIDEO))
        self.assertTrue(stem.startswith("2026-08-11 - "))

    def test_a_long_title_leaves_room_for_the_longest_sidecar(self):
        stem = paths.build_stem("y" * 400, VIDEO, date="2026-08-11")
        self.assertLessEqual(len(stem) + len(".en-orig.srt"),
                             paths.MAX_FILENAME)

    def test_truncation_does_not_leave_trailing_punctuation(self):
        stem = paths.build_stem("word " * 100, VIDEO, date="2026-08-11")
        title_part = stem.split(" - ", 1)[1].rsplit(" [", 1)[0]
        self.assertEqual(title_part, title_part.rstrip(" .-"))

    def test_forbidden_characters_never_reach_the_stem(self):
        stem = paths.build_stem('Tech/Talk: "Live?" <now>', VIDEO,
                                date="2026-08-11")
        for bad in FORBIDDEN:
            self.assertNotIn(bad, stem)

    def test_a_title_of_only_dots_still_produces_a_name(self):
        stem = paths.build_stem("...", VIDEO, date="2026-08-11")
        self.assertIn("untitled", stem)
        self.assertIn(VIDEO, stem)

    def test_no_date_means_no_prefix(self):
        self.assertEqual(paths.build_stem("Title", VIDEO),
                         "Title [%s]" % VIDEO)

    def test_a_bad_video_id_is_refused(self):
        for bad in ("../../etc/passwd", "has space", "", None, "a/b"):
            with self.assertRaises(paths.PathError):
                paths.build_stem("Title", bad)


class PlanTest(unittest.TestCase):

    def channel_and_video(self, app, title="A video", folder="Alpha Channel",
                          published="2026-08-11T12:00:00+00:00"):
        chan = app.add_channel(channel_id="UC_a", title="Alpha Channel",
                               url="https://www.youtube.com/channel/UC_a",
                               folder=folder)
        app.add_video(VIDEO, chan, title, published_at=published)
        return (db.get_channel(app.conn, chan),
                db.get_video_by_ytid(app.conn, VIDEO))

    def test_final_paths_lands_under_the_channel_folder(self):
        with harness.temp_app() as app:
            channel, video = self.channel_and_video(app)
            plan = paths.final_paths(app.cfg, channel, video)

            self.assertEqual(
                plan.video,
                os.path.join(app.cfg["library_root"], "Alpha Channel",
                             "2026-08-11 - A video [%s].mp4" % VIDEO))
            self.assertTrue(os.path.isdir(plan.directory))

    def test_sidecars_share_the_stem(self):
        with harness.temp_app() as app:
            channel, video = self.channel_and_video(app)
            plan = paths.final_paths(app.cfg, channel, video)

            self.assertEqual(plan.sidecar(".en.srt"),
                             plan.video[:-len(".mp4")] + ".en.srt")
            self.assertEqual(plan.sidecar("en.srt"), plan.sidecar(".en.srt"))

    def test_the_date_falls_back_to_discovery_when_unpublished(self):
        with harness.temp_app() as app:
            channel, video = self.channel_and_video(app, published=None)
            plan = paths.final_paths(app.cfg, channel, video)
            self.assertRegex(os.path.basename(plan.video),
                             r"^\d{4}-\d{2}-\d{2} - ")

    def test_a_channel_folder_with_forbidden_characters_is_cleaned(self):
        with harness.temp_app() as app:
            channel, video = self.channel_and_video(app, folder='Bad/Name:Here')
            plan = paths.final_paths(app.cfg, channel, video)
            for bad in FORBIDDEN:
                self.assertNotIn(bad, os.path.basename(plan.directory))

    def test_tmp_dir_sits_on_the_library_filesystem(self):
        with harness.temp_app() as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            self.assertTrue(tmp.startswith(app.cfg["library_root"]))
            self.assertTrue(os.path.isdir(tmp))
            self.assertIn(paths.TMP_DIRNAME, tmp)

    def test_tmp_dir_refuses_a_crafted_video_id(self):
        with harness.temp_app() as app:
            with self.assertRaises(paths.PathError):
                paths.tmp_dir(app.cfg, "../../../etc")

    def test_the_whole_filename_stays_within_the_cap(self):
        with harness.temp_app() as app:
            channel, video = self.channel_and_video(app, title="z" * 500)
            plan = paths.final_paths(app.cfg, channel, video)
            self.assertLessEqual(len(os.path.basename(plan.video)),
                                 paths.MAX_FILENAME)


if __name__ == "__main__":
    unittest.main()
