"""Getting a deleted or skipped video back on purpose.

A deleted or skipped row is a tombstone so the poller cannot re-download it
by itself. Asking by hand is a different intent, and the only route back
for something pruned by keep-N, skipped as pre-existing, or caught by a
filter that has since been turned off.
"""

import contextlib
import json
import os
import threading
import unittest
import urllib.request

import harness
from tuner import db, server, web, worker


def runtime(app):
    return server.App(app.cfg, app.conn)


def row_with(app, status, video_id="dQw4w9WgXcQ", **fields):
    chan = app.add_channel(channel_id="UC_a", title="Alpha",
                           url="https://www.youtube.com/channel/UC_a",
                           folder="Alpha")
    app.add_video(video_id, chan, "A video", published_at="2026-08-01T00:00:00Z")
    row = db.get_video_by_ytid(app.conn, video_id)
    db.set_video_status(app.conn, row["id"], status, **fields)
    return db.get_video_by_ytid(app.conn, video_id)


class ButtonTest(unittest.TestCase):

    def setUp(self):
        with harness.temp_app() as app:
            self.script = web.render(app.cfg).split("<script>")[1]

    def test_the_button_is_offered_for_deleted_and_skipped(self):
        self.assertIn("video.status==='deleted'||video.status==='skipped'",
                      self.script)
        self.assertIn("Download again", self.script)

    def test_it_posts_to_the_retry_endpoint(self):
        # Two callers now: the Retry button on failed/missing rows, and the
        # Download again button on deleted/skipped ones.
        call = "post('/api/videos/'+video.id+'/retry')"
        self.assertEqual(self.script.count(call), 2)

    def test_the_delete_button_is_still_only_for_downloaded(self):
        self.assertIn("video.status==='downloaded'", self.script)


class RetryAnyStatusTest(unittest.TestCase):

    def test_a_deleted_row_can_be_re_queued(self):
        with harness.temp_app() as app:
            row = row_with(app, db.DELETED, deleted_at="2026-08-01T00:00:00Z")

            self.assertTrue(worker.Worker(runtime(app)).retry(row["id"]))

            after = db.get_video(app.conn, row["id"])
            self.assertEqual(after["status"], db.QUEUED)
            self.assertEqual(after["attempts"], 0)

    def test_a_skipped_row_can_be_re_queued_and_loses_its_reason(self):
        with harness.temp_app() as app:
            row = row_with(app, db.SKIPPED, skip_reason="pre-existing")

            worker.Worker(runtime(app)).retry(row["id"])

            after = db.get_video(app.conn, row["id"])
            self.assertEqual(after["status"], db.QUEUED)
            # Otherwise the row would still read as skipped in the UI.
            self.assertIsNone(after["skip_reason"])

    def test_a_short_skipped_by_a_filter_can_be_pulled_down_by_hand(self):
        with harness.temp_app() as app:
            row = row_with(app, db.SKIPPED, skip_reason="short")
            worker.Worker(runtime(app)).retry(row["id"])
            self.assertEqual(db.get_video(app.conn, row["id"])["status"],
                             db.QUEUED)

    def test_re_queueing_survives_the_next_poll(self):
        # process_new only touches NEW rows, so a hand-queued row is not
        # re-filtered back out from under the operator.
        with harness.temp_app() as app:
            row = row_with(app, db.SKIPPED, skip_reason="short")
            worker.Worker(runtime(app)).retry(row["id"])

            from tuner import discover
            discover.process_new(app.conn, app.cfg)

            self.assertEqual(db.get_video(app.conn, row["id"])["status"],
                             db.QUEUED)

    def test_it_downloads_when_the_worker_next_runs(self):
        with harness.temp_app(min_free_gb=0) as app:
            row = row_with(app, db.DELETED)
            app.scenario([harness.rule_download(
                "dQw4w9WgXcQ", size=2048, match=["--merge-output-format"])])

            hand = worker.Worker(runtime(app))
            hand.retry(row["id"])
            hand._once()

            after = db.get_video(app.conn, row["id"])
            self.assertEqual(after["status"], db.DOWNLOADED)
            self.assertTrue(os.path.exists(after["file_path"]))


class EndpointTest(unittest.TestCase):

    @contextlib.contextmanager
    def running(self, app):
        runner = server.App(app.cfg, app.conn)
        runner.worker = worker.Worker(runner)
        httpd = server.make_server(runner, host="127.0.0.1", port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_the_endpoint_accepts_a_deleted_row(self):
        with harness.temp_app() as app:
            row = row_with(app, db.DELETED)
            with self.running(app) as port:
                request = urllib.request.Request(
                    "http://127.0.0.1:%d/api/videos/%d/retry" % (port, row["id"]),
                    data=b"{}", headers={"Content-Type": "application/json"},
                    method="POST")
                with urllib.request.urlopen(request, timeout=5) as response:
                    body = json.loads(response.read().decode())

            self.assertTrue(body["ok"])
            self.assertEqual(db.get_video(app.conn, row["id"])["status"],
                             db.QUEUED)

    def test_the_state_carries_the_id_the_button_needs(self):
        with harness.temp_app() as app:
            row_with(app, db.DELETED)
            state = server.build_state(server.App(app.cfg, app.conn))
            self.assertIn("id", state["recent"][0])
            self.assertEqual(state["recent"][0]["status"], "deleted")


if __name__ == "__main__":
    unittest.main()
