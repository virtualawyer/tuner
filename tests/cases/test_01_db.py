"""Task 1.2 -- schema, constraints, and the repository seam."""

import unittest

from tuner import db


def fresh():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    return conn


class SchemaTest(unittest.TestCase):

    def setUp(self):
        self.conn = fresh()
        self.chan = db.add_channel(self.conn, "UC_test_channel_0001",
                                   "Test Channel", "https://youtube.com/@test",
                                   "Test Channel")

    def tearDown(self):
        self.conn.close()

    def test_init_schema_is_idempotent(self):
        db.init_schema(self.conn)
        tables = set(row["name"] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"))
        for expected in ("channels", "videos", "events", "settings"):
            self.assertIn(expected, tables)

    def test_duplicate_channel_id_is_rejected(self):
        again = db.add_channel(self.conn, "UC_test_channel_0001", "Other",
                               "https://youtube.com/@other", "Other")
        self.assertIsNone(again)
        self.assertEqual(len(db.list_channels(self.conn)), 1)

    def test_duplicate_video_id_is_rejected(self):
        first = db.add_video(self.conn, "vid00000001", self.chan, "One")
        second = db.add_video(self.conn, "vid00000001", self.chan, "One again")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(db.list_videos(self.conn)), 1)

    def test_cascade_delete_removes_videos(self):
        db.add_video(self.conn, "vid00000001", self.chan, "One")
        db.add_video(self.conn, "vid00000002", self.chan, "Two")
        self.assertEqual(len(db.list_videos(self.conn)), 2)

        db.delete_channel(self.conn, self.chan)
        self.assertEqual(len(db.list_videos(self.conn)), 0)
        self.assertEqual(len(db.list_channels(self.conn)), 0)

    def test_foreign_keys_pragma_is_on(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)


class RepositoryTest(unittest.TestCase):

    def setUp(self):
        self.conn = fresh()
        self.chan = db.add_channel(self.conn, "UC_a", "Alpha",
                                   "https://youtube.com/@alpha", "Alpha")
        self.other = db.add_channel(self.conn, "UC_b", "Beta",
                                    "https://youtube.com/@beta", "Beta")

    def tearDown(self):
        self.conn.close()

    def test_update_channel_touches_only_named_fields(self):
        db.update_channel(self.conn, self.chan, keep_n=9)
        row = db.get_channel(self.conn, self.chan)
        self.assertEqual(row["keep_n"], 9)
        self.assertEqual(row["title"], "Alpha")
        self.assertIsNone(row["skip_shorts"])  # inherit stays inherit

    def test_update_channel_rejects_unknown_column(self):
        with self.assertRaises(ValueError):
            db.update_channel(self.conn, self.chan, nonsense=1)

    def test_channels_default_to_inherit_and_enabled(self):
        row = db.get_channel(self.conn, self.chan)
        self.assertEqual(row["enabled"], 1)
        self.assertIsNone(row["keep_n"])
        self.assertIsNone(row["skip_shorts"])
        self.assertIsNone(row["skip_live"])

    def test_list_videos_filters_by_status_and_channel(self):
        db.add_video(self.conn, "v1", self.chan, "A", "2026-08-01T00:00:00Z")
        db.add_video(self.conn, "v2", self.chan, "B", "2026-08-02T00:00:00Z")
        db.add_video(self.conn, "v3", self.other, "C", "2026-08-03T00:00:00Z")
        db.set_video_status(self.conn, 1, db.QUEUED)

        self.assertEqual(len(db.list_videos(self.conn, status=db.QUEUED)), 1)
        self.assertEqual(len(db.list_videos(self.conn, channel_row_id=self.chan)), 2)
        self.assertEqual(
            len(db.list_videos(self.conn, status=[db.NEW, db.QUEUED])), 3)

    def test_set_video_status_validates(self):
        db.add_video(self.conn, "v1", self.chan, "A")
        with self.assertRaises(ValueError):
            db.set_video_status(self.conn, 1, "invented")
        with self.assertRaises(ValueError):
            db.set_video_status(self.conn, 1, db.QUEUED, nonsense=1)

    def test_claim_next_queued_takes_oldest_and_flips_status(self):
        db.add_video(self.conn, "v1", self.chan, "Newer", "2026-08-05T00:00:00Z")
        db.add_video(self.conn, "v2", self.chan, "Older", "2026-08-01T00:00:00Z")
        for row in db.list_videos(self.conn):
            db.set_video_status(self.conn, row["id"], db.QUEUED)

        claimed = db.claim_next_queued(self.conn)
        self.assertEqual(claimed["title"], "Older")
        self.assertEqual(db.get_video(self.conn, claimed["id"])["status"],
                         db.DOWNLOADING)

        second = db.claim_next_queued(self.conn)
        self.assertEqual(second["title"], "Newer")
        self.assertNotEqual(second["id"], claimed["id"])

        self.assertIsNone(db.claim_next_queued(self.conn))

    def test_claim_honours_exclusions(self):
        db.add_video(self.conn, "v1", self.chan, "Backed off",
                     "2026-08-01T00:00:00Z")
        db.set_video_status(self.conn, 1, db.QUEUED)
        self.assertIsNone(db.claim_next_queued(self.conn, exclude_ids={1}))
        self.assertIsNotNone(db.claim_next_queued(self.conn))

    def test_bump_attempts(self):
        db.add_video(self.conn, "v1", self.chan, "A")
        self.assertEqual(db.bump_attempts(self.conn, 1), 1)
        self.assertEqual(db.bump_attempts(self.conn, 1), 2)

    def test_library_bytes_counts_only_downloaded(self):
        db.add_video(self.conn, "v1", self.chan, "A")
        db.add_video(self.conn, "v2", self.chan, "B")
        db.set_video_status(self.conn, 1, db.DOWNLOADED, file_size=1000)
        db.set_video_status(self.conn, 2, db.DELETED, file_size=9999)

        totals = db.library_bytes(self.conn)
        self.assertEqual(totals["bytes"], 1000)
        self.assertEqual(totals["videos"], 1)

    def test_settings_upsert(self):
        db.set_setting(self.conn, "k", "one")
        db.set_setting(self.conn, "k", "two")
        self.assertEqual(db.get_setting(self.conn, "k"), "two")
        self.assertEqual(db.get_setting(self.conn, "absent", "fallback"),
                         "fallback")

    def test_now_iso_shape(self):
        stamp = db.now_iso()
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


if __name__ == "__main__":
    unittest.main()
