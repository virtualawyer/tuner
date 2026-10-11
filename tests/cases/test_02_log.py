"""Task 1.3 -- event log ordering and startup reconciliation."""

import os
import shutil
import tempfile
import unittest

from tuner import db, log


class LogTest(unittest.TestCase):

    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_schema(self.conn)
        self.chan = db.add_channel(self.conn, "UC_a", "Alpha",
                                   "https://youtube.com/@alpha", "Alpha")

    def tearDown(self):
        self.conn.close()

    def test_events_come_back_newest_first(self):
        log.info(self.conn, "first")
        log.warn(self.conn, "second")
        log.error(self.conn, "third")

        rows = log.recent(self.conn)
        self.assertEqual([r["message"] for r in rows], ["third", "second", "first"])
        self.assertEqual([r["level"] for r in rows], ["error", "warn", "info"])

    def test_recent_honours_limit(self):
        for index in range(10):
            log.info(self.conn, "line %d" % index)
        self.assertEqual(len(log.recent(self.conn, limit=4)), 4)

    def test_event_carries_context(self):
        log.info(self.conn, "with context", channel_row_id=self.chan,
                 video_id="vid00000001")
        row = log.recent(self.conn)[0]
        self.assertEqual(row["channel_row_id"], self.chan)
        self.assertEqual(row["video_id"], "vid00000001")

    def test_unknown_level_falls_back_to_info(self):
        log.event(self.conn, "shouting", "message")
        self.assertEqual(log.recent(self.conn)[0]["level"], "info")

    def test_event_without_connection_does_not_raise(self):
        self.assertIsNone(log.event(None, log.INFO, "no database here"))

    def test_prune_keeps_the_newest(self):
        for index in range(20):
            log.info(self.conn, "line %d" % index)
        log.prune(self.conn, keep=5)
        rows = log.recent(self.conn)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["message"], "line 19")

    def test_now_iso_is_re_exported(self):
        self.assertRegex(log.now_iso(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ReconcileTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tuner-rec-")
        self.conn = db.connect(":memory:")
        db.init_schema(self.conn)
        self.chan = db.add_channel(self.conn, "UC_a", "Alpha",
                                   "https://youtube.com/@alpha", "Alpha")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_file(self, name):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x")
        return path

    def test_downloaded_row_with_missing_file_becomes_missing(self):
        db.add_video(self.conn, "v1", self.chan, "Gone")
        db.set_video_status(self.conn, 1, db.DOWNLOADED,
                            file_path=os.path.join(self.tmp, "absent.mp4"))

        result = log.reconcile(self.conn)
        self.assertEqual(result["missing"], 1)
        self.assertEqual(db.get_video(self.conn, 1)["status"], db.MISSING)

    def test_reconcile_is_idempotent(self):
        db.add_video(self.conn, "v1", self.chan, "Gone")
        db.set_video_status(self.conn, 1, db.DOWNLOADED,
                            file_path=os.path.join(self.tmp, "absent.mp4"))

        log.reconcile(self.conn)
        second = log.reconcile(self.conn)
        # Already missing, so the second pass finds nothing and logs nothing new.
        self.assertEqual(second["missing"], 0)
        warnings = [r for r in log.recent(self.conn) if r["level"] == "warn"]
        self.assertEqual(len(warnings), 1)

    def test_present_file_is_left_alone(self):
        path = self.make_file("here.mp4")
        db.add_video(self.conn, "v1", self.chan, "Here")
        db.set_video_status(self.conn, 1, db.DOWNLOADED, file_path=path)

        result = log.reconcile(self.conn)
        self.assertEqual(result["missing"], 0)
        self.assertEqual(db.get_video(self.conn, 1)["status"], db.DOWNLOADED)

    def test_interrupted_download_is_requeued(self):
        db.add_video(self.conn, "v1", self.chan, "Half done")
        db.set_video_status(self.conn, 1, db.DOWNLOADING)

        result = log.reconcile(self.conn)
        self.assertEqual(result["requeued"], 1)
        self.assertEqual(db.get_video(self.conn, 1)["status"], db.QUEUED)

    def test_other_statuses_are_untouched(self):
        db.add_video(self.conn, "v1", self.chan, "Skipped")
        db.add_video(self.conn, "v2", self.chan, "Deleted")
        db.set_video_status(self.conn, 1, db.SKIPPED, skip_reason="short")
        db.set_video_status(self.conn, 2, db.DELETED)

        log.reconcile(self.conn)
        self.assertEqual(db.get_video(self.conn, 1)["status"], db.SKIPPED)
        self.assertEqual(db.get_video(self.conn, 2)["status"], db.DELETED)


if __name__ == "__main__":
    unittest.main()
