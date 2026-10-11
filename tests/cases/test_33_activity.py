"""Task 8.4 -- the activity card, the queue, and the recent list.

The page renders only from /api/state, so the contract worth testing is
that every field the script reads actually exists in the payload.
"""

import re
import unittest

import harness
from tuner import db, server, web

VIDEO = "dQw4w9WgXcQ"


def seeded(app):
    chan = app.add_channel(channel_id="UC_a", title="Alpha Channel",
                           url="https://www.youtube.com/channel/UC_a",
                           folder="Alpha Channel")
    return chan


def add(app, chan, video_id, status, **fields):
    app.add_video(video_id, chan, "Title " + video_id,
                  published_at="2026-08-01T00:00:00Z")
    row = db.get_video_by_ytid(app.conn, video_id)
    db.set_video_status(app.conn, row["id"], status, **fields)
    return db.get_video_by_ytid(app.conn, video_id)


class MarkupTest(unittest.TestCase):

    def setUp(self):
        with harness.temp_app() as app:
            self.markup = web.render(app.cfg)
        self.script = self.markup.split("<script>")[1]

    def test_every_section_exists(self):
        for element_id in ("current", "current-empty", "queue", "queue-card",
                           "queue-empty", "queue-count", "recent",
                           "recent-card", "recent-empty", "log"):
            self.assertIn('id="%s"' % element_id, self.markup)

    def test_the_log_is_monospace_and_scrolls(self):
        style = self.markup.split("<style>")[1].split("</style>")[0]
        self.assertIn(".log{max-height:280px;overflow-y:auto}", style)
        self.assertIn('class="log mono"', self.markup)


class PayloadContractTest(unittest.TestCase):
    """Every state field the script reads has to be in the payload."""

    def setUp(self):
        with harness.temp_app() as app:
            self.script = web.render(app.cfg).split("<script>")[1]
            self.state = server.build_state(server.App(app.cfg, app.conn))

    def test_the_top_level_keys_the_script_reads_all_exist(self):
        for key in ("counts", "disk", "library", "channels", "activity",
                    "recent", "events", "version", "ytdlp_version"):
            self.assertIn(key, self.state)

    def test_the_activity_block_has_both_halves(self):
        self.assertIn("current", self.state["activity"])
        self.assertIn("queue", self.state["activity"])

    def test_every_video_field_the_script_reads_exists(self):
        with harness.temp_app() as app:
            chan = seeded(app)
            add(app, chan, VIDEO, db.DOWNLOADED, file_size=4096,
                downloaded_at="2026-08-11T12:00:00Z")
            state = server.build_state(server.App(app.cfg, app.conn))
            video = state["recent"][0]

        for field in re.findall(r"video\.([a-z_]+)", self.script):
            self.assertIn(field, video, "state payload has no video.%s" % field)

    def test_every_progress_field_the_card_reads_exists(self):
        from tuner import worker
        row = {"video_id": VIDEO, "title": "A video"}
        snapshot = worker.blank_progress(row)
        for field in ("video_id", "title", "percent", "size", "rate", "eta",
                      "stage"):
            self.assertIn(field, snapshot)


class StateContentTest(unittest.TestCase):

    def test_the_queue_lists_queued_and_deferred_oldest_first(self):
        with harness.temp_app() as app:
            chan = seeded(app)
            app.add_video("vidnewest01", chan, "Newer",
                          published_at="2026-08-05T00:00:00Z")
            app.add_video("vidoldest01", chan, "Older",
                          published_at="2026-08-01T00:00:00Z")
            for video_id in ("vidnewest01", "vidoldest01"):
                row = db.get_video_by_ytid(app.conn, video_id)
                db.set_video_status(app.conn, row["id"], db.QUEUED)
            add(app, chan, "viddeferred", db.DEFERRED)

            state = server.build_state(server.App(app.cfg, app.conn))
            queue = state["activity"]["queue"]

            self.assertEqual(len(queue), 3)
            self.assertEqual(queue[0]["title"], "Older")
            self.assertIn("deferred", [item["status"] for item in queue])

    def test_a_downloaded_video_carries_what_the_row_shows(self):
        with harness.temp_app() as app:
            chan = seeded(app)
            add(app, chan, VIDEO, db.DOWNLOADED, file_size=4096,
                downloaded_at="2026-08-11T12:00:00Z")

            state = server.build_state(server.App(app.cfg, app.conn))
            video = state["recent"][0]

            self.assertEqual(video["status"], "downloaded")
            self.assertEqual(video["file_size"], 4096)
            self.assertEqual(video["channel"], "Alpha Channel")

    def test_a_skipped_video_carries_its_reason(self):
        with harness.temp_app() as app:
            chan = seeded(app)
            add(app, chan, VIDEO, db.SKIPPED, skip_reason="short")

            state = server.build_state(server.App(app.cfg, app.conn))
            self.assertEqual(state["recent"][0]["skip_reason"], "short")

    def test_a_failed_video_carries_its_error_and_attempts(self):
        with harness.temp_app() as app:
            chan = seeded(app)
            add(app, chan, VIDEO, db.FAILED, error="ERROR: fragment not found",
                attempts=3)

            video = server.build_state(
                server.App(app.cfg, app.conn))["recent"][0]
            self.assertIn("fragment", video["error"])
            self.assertEqual(video["attempts"], 3)

    def test_recent_is_capped_at_fifty(self):
        with harness.temp_app() as app:
            chan = seeded(app)
            for index in range(60):
                app.add_video("vid%08d" % index, chan, "Video %d" % index,
                              published_at="2026-08-01T00:00:00Z")

            state = server.build_state(server.App(app.cfg, app.conn))
            self.assertEqual(len(state["recent"]), 50)

    def test_the_current_download_reaches_the_payload(self):
        from tuner import worker
        try:
            worker.set_current({"video_id": VIDEO, "title": "A video",
                                "percent": 42.5, "size": "10.00MiB",
                                "rate": "1.20MiB/s", "eta": "00:09",
                                "stage": "downloading"})
            with harness.temp_app() as app:
                state = server.build_state(server.App(app.cfg, app.conn))
            self.assertEqual(state["activity"]["current"]["percent"], 42.5)
            self.assertEqual(state["activity"]["current"]["stage"],
                             "downloading")
        finally:
            worker.set_current(None)

    def test_no_current_download_is_null(self):
        from tuner import worker
        worker.set_current(None)
        with harness.temp_app() as app:
            state = server.build_state(server.App(app.cfg, app.conn))
        self.assertIsNone(state["activity"]["current"])

    def test_events_reach_the_log(self):
        with harness.temp_app() as app:
            from tuner import log
            log.warn(app.conn, "something worth seeing")
            state = server.build_state(server.App(app.cfg, app.conn))

        self.assertEqual(state["events"][0]["message"], "something worth seeing")
        self.assertEqual(state["events"][0]["level"], "warn")


if __name__ == "__main__":
    unittest.main()
