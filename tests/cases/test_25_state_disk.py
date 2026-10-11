"""Task 6.4 -- what /api/state says about storage."""

import collections
import os
import unittest

import harness
from tuner import db, disk, server

FakeStat = collections.namedtuple(
    "FakeStat", "f_blocks f_frsize f_bavail f_bfree")


def stat_for(total_gb, free_gb):
    frsize = 4096
    return FakeStat(f_blocks=int(total_gb * disk.GB) // frsize,
                    f_frsize=frsize,
                    f_bavail=int(free_gb * disk.GB) // frsize,
                    f_bfree=int(free_gb * disk.GB) // frsize)


def seed(app, channel_id, folder, sizes, keep_n=None):
    chan = app.add_channel(channel_id=channel_id, title=folder,
                           url="https://www.youtube.com/channel/" + channel_id,
                           folder=folder, keep_n=keep_n)
    for index, size in enumerate(sizes):
        video_id = "%s%05d" % (channel_id[-1] * 6, index)
        app.add_video(video_id, chan, "Video %d" % index,
                      published_at="2026-08-%02dT00:00:00Z" % (index + 1))
        row = db.get_video_by_ytid(app.conn, video_id)
        db.set_video_status(app.conn, row["id"], db.DOWNLOADED,
                            file_path="/fake/%s.mp4" % video_id,
                            file_size=size)
    return chan


class LibraryReportTest(unittest.TestCase):

    def test_totals_come_from_recorded_sizes(self):
        with harness.temp_app() as app:
            seed(app, "UC_a", "Alpha", [1000, 2000])
            seed(app, "UC_b", "Beta", [500])

            state = server.build_state(server.App(app.cfg, app.conn))

            self.assertEqual(state["library"]["bytes"], 3500)
            self.assertEqual(state["library"]["videos"], 3)
            self.assertEqual(state["library"]["root"], app.cfg["library_root"])

    def test_per_channel_breakdown(self):
        with harness.temp_app(keep_n=5) as app:
            seed(app, "UC_a", "Alpha", [1000, 2000])
            seed(app, "UC_b", "Beta", [500], keep_n=2)

            state = server.build_state(server.App(app.cfg, app.conn))
            rows = dict((item["title"], item)
                        for item in state["library"]["per_channel"])

            self.assertEqual(rows["Alpha"]["bytes"], 3000)
            self.assertEqual(rows["Alpha"]["videos"], 2)
            self.assertEqual(rows["Alpha"]["keep_n"], 5)     # inherited
            self.assertEqual(rows["Beta"]["bytes"], 500)
            self.assertEqual(rows["Beta"]["keep_n"], 2)      # overridden

    def test_deleted_videos_stop_counting(self):
        with harness.temp_app() as app:
            seed(app, "UC_a", "Alpha", [1000, 2000])
            row = db.list_videos(app.conn, status=db.DOWNLOADED)[0]
            db.set_video_status(app.conn, row["id"], db.DELETED, file_size=None)

            state = server.build_state(server.App(app.cfg, app.conn))
            self.assertEqual(state["library"]["videos"], 1)

    def test_an_empty_library_reports_zeros(self):
        with harness.temp_app() as app:
            state = server.build_state(server.App(app.cfg, app.conn))
            self.assertEqual(state["library"]["bytes"], 0)
            self.assertEqual(state["library"]["per_channel"], [])

    def test_the_report_does_not_walk_the_filesystem(self):
        # Walking 2.8 TB of exFAT on a 2 second poll would be unusable, so
        # the figures come from the recorded sizes. The paths here do not
        # exist at all, and the totals still come out right.
        with harness.temp_app() as app:
            seed(app, "UC_a", "Alpha", [4096])
            state = server.build_state(server.App(app.cfg, app.conn))
            self.assertEqual(state["library"]["bytes"], 4096)


class LowSpaceTest(unittest.TestCase):

    def state_with(self, total_gb, free_gb, min_free_gb=10):
        original = os.statvfs
        os.statvfs = lambda path: stat_for(total_gb, free_gb)
        try:
            with harness.temp_app(min_free_gb=min_free_gb) as app:
                return server.build_state(server.App(app.cfg, app.conn))
        finally:
            os.statvfs = original

    def test_ample_space_is_not_flagged(self):
        state = self.state_with(2800, 1200)
        self.assertFalse(state["disk"]["low"])
        self.assertTrue(state["disk"]["ok"])

    def test_below_the_floor_is_flagged(self):
        state = self.state_with(2800, 4)
        self.assertTrue(state["disk"]["low"])

    def test_exactly_at_the_floor_is_flagged(self):
        # The worker refuses to start at the floor, so the page has to say
        # so rather than looking idle for no reason.
        self.assertTrue(self.state_with(100, 10, min_free_gb=10)["disk"]["low"])

    def test_the_configured_floor_is_reported(self):
        self.assertEqual(self.state_with(100, 50, min_free_gb=25)
                         ["disk"]["min_free_gb"], 25)

    def test_an_unmounted_drive_is_not_reported_as_low(self):
        # It is unknown, not low. The ok flag is what the page keys on.
        with harness.temp_app(library_root="/definitely/not/mounted") as app:
            state = server.build_state(server.App(app.cfg, app.conn))
            self.assertFalse(state["disk"]["ok"])
            self.assertFalse(state["disk"]["low"])


class CountsTest(unittest.TestCase):

    def test_every_status_the_page_shows_is_counted(self):
        with harness.temp_app() as app:
            chan = seed(app, "UC_a", "Alpha", [100, 200, 300])
            rows = db.list_videos(app.conn, status=db.DOWNLOADED)
            db.set_video_status(app.conn, rows[0]["id"], db.SKIPPED,
                                skip_reason="short")
            db.set_video_status(app.conn, rows[1]["id"], db.MISSING)

            state = server.build_state(server.App(app.cfg, app.conn))
            counts = state["counts"]

            self.assertEqual(counts["channels"], 1)
            self.assertEqual(counts["downloaded"], 1)
            self.assertEqual(counts["skipped"], 1)
            self.assertEqual(counts["missing"], 1)
            for key in ("queued", "failed", "deferred"):
                self.assertEqual(counts[key], 0)

    def test_channels_carry_their_resolved_settings(self):
        with harness.temp_app(keep_n=5, skip_shorts=True, skip_live=True) as app:
            chan = seed(app, "UC_a", "Alpha", [100])
            db.update_channel(app.conn, chan, skip_shorts=0)

            state = server.build_state(server.App(app.cfg, app.conn))
            channel = state["channels"][0]

            self.assertEqual(channel["skip_shorts"], 0)       # the override
            self.assertIsNone(channel["skip_live"])           # inheriting
            self.assertFalse(channel["effective"]["skip_shorts"])
            self.assertTrue(channel["effective"]["skip_live"])
            self.assertEqual(channel["effective"]["keep_n"], 5)


if __name__ == "__main__":
    unittest.main()
