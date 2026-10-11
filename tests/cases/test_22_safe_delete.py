"""Task 6.1 -- the delete that cannot reach outside the library."""

import os
import shutil
import tempfile
import unittest

import harness
from tuner import LIBRARY_CHANGED, db, retention


class InsideTest(unittest.TestCase):

    def test_paths_inside_and_outside(self):
        with harness.temp_app() as app:
            root = app.cfg["library_root"]
            self.assertTrue(retention.is_inside(root,
                                                os.path.join(root, "a", "b.mp4")))
            self.assertFalse(retention.is_inside(root, root))
            self.assertFalse(retention.is_inside(root, "/etc/passwd"))

    def test_a_sibling_prefix_is_not_inside(self):
        with harness.temp_app() as app:
            root = app.cfg["library_root"]
            self.assertFalse(retention.is_inside(root, root + "-other/x.mp4"))

    def test_the_filesystem_root_is_never_a_valid_library(self):
        self.assertFalse(retention.is_inside("/", "/etc/passwd"))


class SafeDeleteTest(unittest.TestCase):

    def setUp(self):
        LIBRARY_CHANGED.clear()

    def tearDown(self):
        LIBRARY_CHANGED.clear()

    def test_a_media_file_and_its_sidecars_go_together(self):
        with harness.temp_app() as app:
            stem = "Alpha/2026-08-11 - A video [dQw4w9WgXcQ]"
            media = app.make_file(stem + ".mp4", size=4096)
            srt = app.make_file(stem + ".en.srt", size=100)
            orig = app.make_file(stem + ".en-orig.srt", size=50)

            freed = retention.safe_delete(app.cfg, media)

            self.assertEqual(freed, 4096 + 100 + 50)
            for path in (media, srt, orig):
                self.assertFalse(os.path.exists(path))
            self.assertTrue(LIBRARY_CHANGED.is_set())

    def test_an_unrelated_neighbour_survives(self):
        with harness.temp_app() as app:
            media = app.make_file("Alpha/2026-08-11 - One [aaaaaaaaaaa].mp4")
            other = app.make_file("Alpha/2026-08-12 - Two [bbbbbbbbbbb].mp4")

            retention.safe_delete(app.cfg, media)

            self.assertFalse(os.path.exists(media))
            self.assertTrue(os.path.exists(other))

    def test_an_emptied_channel_folder_is_removed(self):
        with harness.temp_app() as app:
            media = app.make_file("Alpha/only [aaaaaaaaaaa].mp4")
            retention.safe_delete(app.cfg, media)
            self.assertFalse(os.path.exists(
                os.path.join(app.cfg["library_root"], "Alpha")))

    def test_a_folder_with_other_videos_stays(self):
        with harness.temp_app() as app:
            media = app.make_file("Alpha/one [aaaaaaaaaaa].mp4")
            app.make_file("Alpha/two [bbbbbbbbbbb].mp4")
            retention.safe_delete(app.cfg, media)
            self.assertTrue(os.path.isdir(
                os.path.join(app.cfg["library_root"], "Alpha")))

    def test_the_library_root_itself_is_never_removed(self):
        with harness.temp_app() as app:
            media = app.make_file("loose [aaaaaaaaaaa].mp4")
            retention.safe_delete(app.cfg, media)
            self.assertTrue(os.path.isdir(app.cfg["library_root"]))

    def test_a_missing_file_frees_nothing_and_does_not_raise(self):
        with harness.temp_app() as app:
            path = os.path.join(app.cfg["library_root"], "Alpha", "gone.mp4")
            self.assertEqual(retention.safe_delete(app.cfg, path), 0)


class RefusalTest(unittest.TestCase):

    def setUp(self):
        self.outside = tempfile.mkdtemp(prefix="tuner-outside-")

    def tearDown(self):
        shutil.rmtree(self.outside, ignore_errors=True)

    def make_outside(self, name="precious.txt"):
        path = os.path.join(self.outside, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("do not delete me")
        return path

    def test_an_absolute_path_outside_is_refused(self):
        victim = self.make_outside()
        with harness.temp_app() as app:
            with self.assertRaises(retention.UnsafePathError):
                retention.safe_delete(app.cfg, victim, conn=app.conn)
            self.assertTrue(os.path.exists(victim))
            self.assertTrue(any("refused to delete" in m
                                for m in app.events(level="error")))

    def test_a_system_path_is_refused(self):
        with harness.temp_app() as app:
            with self.assertRaises(retention.UnsafePathError):
                retention.safe_delete(app.cfg, "/etc/passwd")
            self.assertTrue(os.path.exists("/etc/passwd"))

    def test_a_traversal_out_of_the_library_is_refused(self):
        victim = self.make_outside()
        with harness.temp_app() as app:
            crafted = os.path.join(app.cfg["library_root"], "..", "..",
                                   victim.lstrip("/"))
            with self.assertRaises(retention.UnsafePathError):
                retention.safe_delete(app.cfg, crafted)
            self.assertTrue(os.path.exists(victim))

    def test_a_symlink_pointing_out_of_the_library_is_refused(self):
        victim = self.make_outside()
        with harness.temp_app() as app:
            link = os.path.join(app.cfg["library_root"], "sneaky.mp4")
            os.symlink(victim, link)

            with self.assertRaises(retention.UnsafePathError):
                retention.safe_delete(app.cfg, link)
            self.assertTrue(os.path.exists(victim))

    def test_an_empty_path_is_refused(self):
        with harness.temp_app() as app:
            for bad in ("", None):
                with self.assertRaises(retention.UnsafePathError):
                    retention.safe_delete(app.cfg, bad)


class DeleteVideoTest(unittest.TestCase):

    def row(self, app, path):
        chan = app.add_channel(channel_id="UC_a", title="Alpha",
                               url="https://www.youtube.com/channel/UC_a",
                               folder="Alpha")
        app.add_video("dQw4w9WgXcQ", chan, "A video")
        row = db.get_video_by_ytid(app.conn, "dQw4w9WgXcQ")
        db.set_video_status(app.conn, row["id"], db.DOWNLOADED,
                            file_path=path, file_size=4096)
        return db.get_video_by_ytid(app.conn, "dQw4w9WgXcQ")

    def test_the_row_survives_as_a_tombstone(self):
        with harness.temp_app() as app:
            media = app.make_file("Alpha/a [dQw4w9WgXcQ].mp4", size=4096)
            row = self.row(app, media)

            freed = retention.delete_video(app.conn, app.cfg, row, "keep-N")

            self.assertEqual(freed, 4096)
            after = db.get_video_by_ytid(app.conn, "dQw4w9WgXcQ")
            self.assertEqual(after["status"], db.DELETED)
            self.assertIsNotNone(after["deleted_at"])
            self.assertFalse(os.path.exists(media))
            self.assertTrue(any("keep-N" in m for m in app.events()))

    def test_an_unsafe_path_marks_nothing(self):
        with harness.temp_app() as app:
            row = self.row(app, "/etc/passwd")
            self.assertEqual(retention.delete_video(app.conn, app.cfg, row, "x"), 0)
            self.assertEqual(
                db.get_video_by_ytid(app.conn, "dQw4w9WgXcQ")["status"],
                db.DOWNLOADED)


class AuditTest(unittest.TestCase):

    def test_retention_owns_the_only_unlink(self):
        root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "tuner")
        offenders = []
        for name in sorted(os.listdir(root)):
            if not name.endswith(".py") or name == "retention.py":
                continue
            with open(os.path.join(root, name), "r", encoding="utf-8") as handle:
                body = handle.read()
            if "os.unlink" in body or "os.remove(" in body:
                offenders.append(name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
