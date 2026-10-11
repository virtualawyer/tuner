"""Task 6.2 -- keep the newest N per channel."""

import os
import unittest

import harness
from tuner import db, retention, worker

VIDEO_IDS = ["vid%08d" % n for n in range(1, 12)]


def runtime(app):
    from tuner import server
    return server.App(app.cfg, app.conn)


def downloaded_channel(app, count, folder="Alpha", channel_id="UC_a", **cols):
    """A channel with `count` downloaded videos, oldest first by date."""
    chan = app.add_channel(channel_id=channel_id, title=folder,
                           url="https://www.youtube.com/channel/" + channel_id,
                           folder=folder, **cols)
    for index in range(count):
        video_id = "%s%05d" % (channel_id[-3:].ljust(6, "x"), index)
        path = app.make_file("%s/2026-08-%02d - Video %d [%s].mp4"
                             % (folder, index + 1, index, video_id), size=1000)
        app.add_video(video_id, chan, "Video %d" % index,
                      published_at="2026-08-%02dT00:00:00Z" % (index + 1))
        row = db.get_video_by_ytid(app.conn, video_id)
        db.set_video_status(app.conn, row["id"], db.DOWNLOADED,
                            file_path=path, file_size=1000)
    return db.get_channel(app.conn, chan)


class PruneTest(unittest.TestCase):

    def test_seven_downloaded_with_keep_five_deletes_the_two_oldest(self):
        with harness.temp_app(keep_n=5) as app:
            channel = downloaded_channel(app, 7)

            result = retention.prune_channel(app.conn, app.cfg, channel)

            self.assertEqual(result["deleted"], 2)
            self.assertEqual(result["freed"], 2000)

            remaining = db.list_videos(app.conn, status=db.DOWNLOADED)
            self.assertEqual(len(remaining), 5)

            # The survivors are the five newest.
            titles = sorted(row["title"] for row in remaining)
            self.assertEqual(titles, ["Video 2", "Video 3", "Video 4",
                                      "Video 5", "Video 6"])

            deleted = db.list_videos(app.conn, status=db.DELETED)
            self.assertEqual(len(deleted), 2)
            for row in deleted:
                self.assertFalse(os.path.exists(row["file_path"]))

    def test_the_deleted_rows_survive_as_tombstones(self):
        with harness.temp_app(keep_n=5) as app:
            channel = downloaded_channel(app, 7)
            retention.prune_channel(app.conn, app.cfg, channel)

            # Still 7 rows in total: nothing is forgotten, so nothing is
            # re-downloaded on the next poll.
            self.assertEqual(len(db.list_videos(app.conn)), 7)

    def test_fewer_than_keep_n_deletes_nothing(self):
        with harness.temp_app(keep_n=5) as app:
            channel = downloaded_channel(app, 3)
            result = retention.prune_channel(app.conn, app.cfg, channel)

            self.assertEqual(result["deleted"], 0)
            self.assertEqual(len(db.list_videos(app.conn, status=db.DOWNLOADED)), 3)

    def test_keep_n_zero_means_unlimited_not_delete_everything(self):
        with harness.temp_app(keep_n=0) as app:
            channel = downloaded_channel(app, 6)
            result = retention.prune_channel(app.conn, app.cfg, channel)

            self.assertEqual(result["deleted"], 0)
            self.assertEqual(len(db.list_videos(app.conn, status=db.DOWNLOADED)), 6)

    def test_a_per_channel_keep_n_of_zero_is_also_unlimited(self):
        with harness.temp_app(keep_n=2) as app:
            channel = downloaded_channel(app, 6)
            db.update_channel(app.conn, channel["id"], keep_n=0)
            channel = db.get_channel(app.conn, channel["id"])

            self.assertEqual(
                retention.prune_channel(app.conn, app.cfg, channel)["deleted"], 0)

    def test_a_per_channel_override_beats_the_global(self):
        with harness.temp_app(keep_n=10) as app:
            channel = downloaded_channel(app, 6)
            db.update_channel(app.conn, channel["id"], keep_n=2)
            channel = db.get_channel(app.conn, channel["id"])

            self.assertEqual(
                retention.prune_channel(app.conn, app.cfg, channel)["deleted"], 4)
            self.assertEqual(len(db.list_videos(app.conn, status=db.DOWNLOADED)), 2)

    def test_only_downloaded_rows_are_considered(self):
        with harness.temp_app(keep_n=2) as app:
            channel = downloaded_channel(app, 3)
            for row in db.list_videos(app.conn, status=db.DOWNLOADED)[:1]:
                db.set_video_status(app.conn, row["id"], db.SKIPPED,
                                    skip_reason="short")

            retention.prune_channel(app.conn, app.cfg, channel)
            self.assertEqual(len(db.list_videos(app.conn, status=db.DOWNLOADED)), 2)
            self.assertEqual(len(db.list_videos(app.conn, status=db.DELETED)), 0)

    def test_sidecars_go_with_the_pruned_video(self):
        with harness.temp_app(keep_n=1) as app:
            channel = downloaded_channel(app, 2)
            oldest = db.list_videos(app.conn, status=db.DOWNLOADED,
                                    order="published_at ASC")[0]
            sidecar = oldest["file_path"][:-len(".mp4")] + ".en.srt"
            app.make_file(sidecar, size=10)

            retention.prune_channel(app.conn, app.cfg, channel)
            self.assertFalse(os.path.exists(sidecar))

    def test_an_event_names_the_rule(self):
        with harness.temp_app(keep_n=1) as app:
            channel = downloaded_channel(app, 2)
            retention.prune_channel(app.conn, app.cfg, channel)
            self.assertTrue(any("keep newest 1" in m for m in app.events()))


class PruneAllTest(unittest.TestCase):

    def test_every_channel_is_pruned_independently(self):
        with harness.temp_app(keep_n=2) as app:
            downloaded_channel(app, 4, folder="Alpha", channel_id="UC_a")
            downloaded_channel(app, 3, folder="Beta", channel_id="UC_b")

            totals = retention.prune_all(app.conn, app.cfg)

            self.assertEqual(totals["deleted"], 3)     # 2 from Alpha, 1 from Beta
            self.assertEqual(len(db.list_videos(app.conn, status=db.DOWNLOADED)), 4)

    def test_nothing_to_do_is_free(self):
        with harness.temp_app(keep_n=5) as app:
            downloaded_channel(app, 2)
            self.assertEqual(retention.prune_all(app.conn, app.cfg),
                             {"deleted": 0, "freed": 0})


class AfterDownloadTest(unittest.TestCase):

    def test_a_finished_download_prunes_its_channel_at_once(self):
        with harness.temp_app(keep_n=2, min_free_gb=0) as app:
            channel = downloaded_channel(app, 2)

            # A third video arrives and downloads.
            app.add_video("dQw4w9WgXcQ", channel["id"], "The newest",
                          published_at="2026-09-01T00:00:00Z")
            row = db.get_video_by_ytid(app.conn, "dQw4w9WgXcQ")
            db.set_video_status(app.conn, row["id"], db.QUEUED)
            app.scenario([harness.rule_download("dQw4w9WgXcQ", size=2048,
                                                match=["--merge-output-format"])])

            worker.Worker(runtime(app))._once()

            downloaded = db.list_videos(app.conn, status=db.DOWNLOADED)
            self.assertEqual(len(downloaded), 2)
            titles = sorted(row["title"] for row in downloaded)
            self.assertEqual(titles, ["The newest", "Video 1"])


if __name__ == "__main__":
    unittest.main()
