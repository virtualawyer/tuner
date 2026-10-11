"""Task 6.3 -- deleting a video by hand and removing a channel."""

import contextlib
import json
import os
import threading
import unittest
import urllib.error
import urllib.request

import harness
from tuner import db, retention, server, worker


def runtime(app):
    return server.App(app.cfg, app.conn)


def channel_with_files(app, count=3, folder="Alpha", channel_id="UC_a"):
    chan = app.add_channel(channel_id=channel_id, title=folder,
                           url="https://www.youtube.com/channel/" + channel_id,
                           folder=folder)
    paths = []
    for index in range(count):
        video_id = "vid%s%05d" % (channel_id[-1], index)
        path = app.make_file("%s/2026-08-%02d - Video %d [%s].mp4"
                             % (folder, index + 1, index, video_id), size=1000)
        app.add_video(video_id, chan, "Video %d" % index,
                      published_at="2026-08-%02dT00:00:00Z" % (index + 1))
        row = db.get_video_by_ytid(app.conn, video_id)
        db.set_video_status(app.conn, row["id"], db.DOWNLOADED,
                            file_path=path, file_size=1000)
        paths.append(path)
    return chan, paths


class DeleteOneTest(unittest.TestCase):

    def test_deleting_frees_the_file_and_keeps_the_tombstone(self):
        with harness.temp_app() as app:
            chan, paths = channel_with_files(app, 2)
            row = db.get_video_by_ytid(app.conn, "vida00000")

            freed = retention.delete_one(app.conn, app.cfg, row["id"])

            self.assertEqual(freed, 1000)
            self.assertFalse(os.path.exists(paths[0]))
            after = db.get_video(app.conn, row["id"])
            self.assertEqual(after["status"], db.DELETED)
            self.assertTrue(os.path.exists(paths[1]))

    def test_deleting_twice_is_harmless(self):
        with harness.temp_app() as app:
            channel_with_files(app, 1)
            row = db.get_video_by_ytid(app.conn, "vida00000")
            retention.delete_one(app.conn, app.cfg, row["id"])
            self.assertEqual(retention.delete_one(app.conn, app.cfg, row["id"]), 0)

    def test_an_unknown_video(self):
        with harness.temp_app() as app:
            self.assertIsNone(retention.delete_one(app.conn, app.cfg, 9999))


class RemoveChannelTest(unittest.TestCase):

    def test_removing_without_the_flag_keeps_the_files(self):
        with harness.temp_app() as app:
            chan, paths = channel_with_files(app, 3)

            result = retention.remove_channel(app.conn, app.cfg, chan)

            self.assertEqual(result["files_deleted"], 0)
            self.assertEqual(result["freed"], 0)
            for path in paths:
                self.assertTrue(os.path.exists(path))
            # Rows go with the channel through the cascade.
            self.assertEqual(len(db.list_channels(app.conn)), 0)
            self.assertEqual(len(db.list_videos(app.conn)), 0)

    def test_removing_with_the_flag_takes_the_files_too(self):
        with harness.temp_app() as app:
            chan, paths = channel_with_files(app, 3)

            result = retention.remove_channel(app.conn, app.cfg, chan,
                                              delete_files=True)

            self.assertEqual(result["files_deleted"], 3)
            self.assertEqual(result["freed"], 3000)
            for path in paths:
                self.assertFalse(os.path.exists(path))
            self.assertFalse(os.path.exists(
                os.path.join(app.cfg["library_root"], "Alpha")))

    def test_other_channels_are_untouched(self):
        with harness.temp_app() as app:
            chan_a, paths_a = channel_with_files(app, 2, "Alpha", "UC_a")
            _, paths_b = channel_with_files(app, 2, "Beta", "UC_b")

            retention.remove_channel(app.conn, app.cfg, chan_a,
                                     delete_files=True)

            self.assertEqual(len(db.list_channels(app.conn)), 1)
            for path in paths_b:
                self.assertTrue(os.path.exists(path))

    def test_an_unknown_channel(self):
        with harness.temp_app() as app:
            self.assertIsNone(retention.remove_channel(app.conn, app.cfg, 9999))


class MissingFileTest(unittest.TestCase):

    def test_a_missing_row_can_be_re_queued(self):
        with harness.temp_app() as app:
            chan, paths = channel_with_files(app, 1)
            os.unlink(paths[0])

            from tuner import log
            log.reconcile(app.conn, app.cfg)

            row = db.get_video_by_ytid(app.conn, "vida00000")
            self.assertEqual(row["status"], db.MISSING)

            self.assertTrue(worker.Worker(runtime(app)).retry(row["id"]))
            self.assertEqual(db.get_video(app.conn, row["id"])["status"],
                             db.QUEUED)


class EndpointTest(unittest.TestCase):

    @contextlib.contextmanager
    def running(self, app):
        runner = server.App(app.cfg, app.conn)
        runner.worker = worker.Worker(runner)
        httpd = server.make_server(runner, host="127.0.0.1", port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            httpd.server_close()

    def request(self, port, path, method):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path),
            data=b"{}" if method == "POST" else None,
            headers={"Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_delete_video_endpoint_reports_what_it_freed(self):
        with harness.temp_app() as app:
            channel_with_files(app, 1)
            row = db.get_video_by_ytid(app.conn, "vida00000")

            with self.running(app) as port:
                status, body = self.request(
                    port, "/api/videos/%d/delete" % row["id"], "POST")

            self.assertEqual(status, 200)
            self.assertEqual(body["freed"], 1000)
            self.assertEqual(body["freed_human"], "1000 B")

    def test_delete_channel_endpoint_honours_the_flag(self):
        with harness.temp_app() as app:
            chan, paths = channel_with_files(app, 2)

            with self.running(app) as port:
                status, body = self.request(
                    port, "/api/channels/%d?delete_files=1" % chan, "DELETE")

            self.assertEqual(status, 200)
            self.assertEqual(body["files_deleted"], 2)
            for path in paths:
                self.assertFalse(os.path.exists(path))

    def test_delete_channel_endpoint_defaults_to_keeping_files(self):
        with harness.temp_app() as app:
            chan, paths = channel_with_files(app, 2)

            with self.running(app) as port:
                status, body = self.request(port, "/api/channels/%d" % chan,
                                            "DELETE")

            self.assertEqual(status, 200)
            self.assertEqual(body["files_deleted"], 0)
            self.assertTrue(os.path.exists(paths[0]))

    def test_unknown_ids_are_404(self):
        with harness.temp_app() as app:
            with self.running(app) as port:
                self.assertEqual(
                    self.request(port, "/api/videos/9999/delete", "POST")[0], 404)
                self.assertEqual(
                    self.request(port, "/api/channels/9999", "DELETE")[0], 404)


if __name__ == "__main__":
    unittest.main()
