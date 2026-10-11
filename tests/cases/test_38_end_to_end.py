"""Task 9.5 -- the whole path, once, with every transition checked.

Add a channel, seed it, ingest the feed, probe and filter, download,
finalize into the library, prune by keep-N, mark watched from Jellyfin,
delete, and refresh. Everything runs against the fake yt-dlp and a stub
Jellyfin; nothing here touches the network.
"""

import contextlib
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import harness
from tuner import (LIBRARY_CHANGED, db, discover, feed, jellyfin, retention,
                   scheduler, server, worker)

CHANNEL = "UCabcdefghijklmnopqrstuv"


class FakeFeedResponse(object):

    def __init__(self, body):
        self.body = body.encode("utf-8")
        self.status = 200
        self.headers = {}

    def read(self):
        return self.body

    def getcode(self):
        return self.status

    def close(self):
        pass


@contextlib.contextmanager
def serving_feed(body):
    def opener(request, timeout=None):
        return FakeFeedResponse(body)

    previous = feed.OPENER
    feed.OPENER = opener
    try:
        yield
    finally:
        feed.OPENER = previous


@contextlib.contextmanager
def stub_jellyfin(played_items):
    """A Jellyfin that reports the given items as played."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append(self.path)
            body = json.dumps({"Items": played_items,
                               "TotalRecordCount": len(played_items)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            seen.append(self.path)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield ("http://127.0.0.1:%d" % httpd.server_address[1], seen)
    finally:
        httpd.shutdown()
        httpd.server_close()


def played(video_id, path, when):
    return {"Id": "jf-" + video_id, "Name": "watched",
            "Path": path, "UserData": {"Played": True,
                                       "LastPlayedDate": when}}


class EndToEndTest(unittest.TestCase):

    def setUp(self):
        LIBRARY_CHANGED.clear()

    def tearDown(self):
        LIBRARY_CHANGED.clear()
        worker.set_current(None)

    def test_the_whole_path(self):
        sample = harness.fixture("feed_sample.xml")

        with harness.temp_app(seed_n=5, keep_n=2, min_free_gb=0,
                              watched_delete=True, watched_grace_hours=24,
                              jellyfin_user_id="user-1") as app:
            app.scenario([
                harness.rule_resolve(CHANNEL, "Alpha Channel"),
                # The three newest are ordinary videos.
                harness.rule_probe(video_id="vid00000001", title="Newest",
                                   duration=1284, match=["-J", "vid00000001"]),
                harness.rule_probe(video_id="vid00000002", title="Second",
                                   duration=900, match=["-J", "vid00000002"]),
                harness.rule_probe(video_id="vid00000003", title="Third",
                                   duration=700, match=["-J", "vid00000003"]),
                # Then a Short and a finished livestream.
                harness.rule_probe(video_id="vid00000004", title="A Short",
                                   duration=30, width=1080, height=1920,
                                   match=["-J", "vid00000004"]),
                harness.rule_probe(video_id="vid00000005", title="The stream",
                                   duration=7200, live_status="was_live",
                                   match=["-J", "vid00000005"]),
                harness.rule_download("vid00000001", size=3000,
                                      match=["--merge-output-format",
                                             "vid00000001"]),
                harness.rule_download("vid00000002", size=2000,
                                      match=["--merge-output-format",
                                             "vid00000002"]),
                harness.rule_download("vid00000003", size=1000,
                                      match=["--merge-output-format",
                                             "vid00000003"]),
            ])

            # --- 1. add the channel -------------------------------------
            with serving_feed(sample):
                result = discover.add_channel(app.conn, app.cfg, "@alpha")
            self.assertTrue(result["ok"], result["message"])

            channel = db.get_channel_by_ytid(app.conn, CHANNEL)
            self.assertEqual(channel["title"], "Alpha Channel")

            # --- 2. seeding: 5 eligible, the other 10 pre-existing -------
            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)), 5)
            pre_existing = db.list_videos(app.conn, status=db.SKIPPED)
            self.assertEqual(len(pre_existing), 10)
            self.assertTrue(all(row["skip_reason"] == "pre-existing"
                                for row in pre_existing))

            # --- 3. probe and filter ------------------------------------
            counts = discover.process_new(app.conn, app.cfg)
            self.assertEqual(counts["queued"], 3)
            self.assertEqual(counts["skipped"], 2)

            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000004")["skip_reason"],
                "short")
            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000005")["skip_reason"],
                "was_live")
            # The probe supplied the real titles.
            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000001")["title"], "Newest")

            # --- 4. download all three ----------------------------------
            hand = worker.Worker(server.App(app.cfg, app.conn))
            for _ in range(3):
                hand._once()

            self.assertEqual(hand.completed, 3)
            self.assertEqual(db.list_videos(app.conn, status=db.QUEUED), [])

            # --- 5. finalize: real files with sidecars ------------------
            # keep_n is 2, and the prune runs after each download, so the
            # oldest of the three is already gone.
            downloaded = db.list_videos(app.conn, status=db.DOWNLOADED)
            self.assertEqual(len(downloaded), 2)
            for row in downloaded:
                self.assertTrue(os.path.exists(row["file_path"]))
                self.assertTrue(os.path.exists(
                    row["file_path"][:-len(".mp4")] + ".en.srt"))
                self.assertIn("[%s]" % row["video_id"],
                              os.path.basename(row["file_path"]))
                self.assertGreater(row["file_size"], 0)

            # --- 6. prune: the oldest was deleted, its row kept ---------
            pruned = db.get_video_by_ytid(app.conn, "vid00000003")
            self.assertEqual(pruned["status"], db.DELETED)
            self.assertIsNotNone(pruned["deleted_at"])
            self.assertTrue(any("keep newest 2" in message
                                for message in app.events()))

            # Scratch space is clean.
            from tuner import paths
            self.assertEqual(os.listdir(paths.tmp_root(app.cfg)), [])

            survivor = db.get_video_by_ytid(app.conn, "vid00000001")

            # --- 7. Jellyfin reports it watched -------------------------
            container_path = survivor["file_path"].replace(
                app.cfg["library_root"], "/media/youtube")
            items = [played("vid00000001", container_path,
                            "2026-08-01T09:00:00Z")]

            with stub_jellyfin(items) as (url, seen):
                app.cfg["jellyfin_url"] = url
                app.cfg["jellyfin_api_key"] = "key"

                # Matched by the [videoid] token, not by path: Jellyfin's
                # path is the container one and does not match ours.
                self.assertNotEqual(container_path, survivor["file_path"])

                outcome = retention.sync_watched(app.conn, app.cfg)
                self.assertEqual(outcome["marked"], 1)
                self.assertEqual(outcome["deleted"], 1)

                # --- 8. deleted, and Jellyfin asked to rescan -----------
                after = db.get_video_by_ytid(app.conn, "vid00000001")
                self.assertEqual(after["status"], db.DELETED)
                self.assertFalse(os.path.exists(survivor["file_path"]))

                sched = scheduler.Scheduler(server.App(app.cfg, app.conn))
                self.assertTrue(sched.maybe_refresh_library())
                self.assertIn("/Library/Refresh", seen)

            # --- 9. the final tally -------------------------------------
            totals = db.count_videos_by_status(app.conn)
            self.assertEqual(totals[db.DOWNLOADED], 1)   # vid00000002
            self.assertEqual(totals[db.DELETED], 2)      # pruned + watched
            self.assertEqual(totals[db.SKIPPED], 12)     # 10 old + short + live
            self.assertEqual(sum(totals.values()), 15)

            # --- 10. a re-poll changes nothing --------------------------
            channel = db.get_channel_by_ytid(app.conn, CHANNEL)
            with serving_feed(sample):
                self.assertEqual(discover.ingest(app.conn, app.cfg, channel), 0)
            self.assertEqual(db.count_videos_by_status(app.conn), totals)

    def test_the_state_payload_survives_the_whole_path(self):
        # Whatever happened above, the page still has to render.
        sample = harness.fixture("feed_sample.xml")
        with harness.temp_app(seed_n=2, min_free_gb=0) as app:
            app.scenario([
                harness.rule_resolve(CHANNEL, "Alpha Channel"),
                harness.rule_probe(match=["-J"], duration=600),
                harness.rule_download(match=["--merge-output-format"]),
            ])
            with serving_feed(sample):
                discover.add_channel(app.conn, app.cfg, "@alpha")
            discover.process_new(app.conn, app.cfg)
            worker.Worker(server.App(app.cfg, app.conn))._once()

            state = server.build_state(server.App(app.cfg, app.conn))
            body = json.dumps(state)

            self.assertTrue(state["channels"])
            self.assertTrue(state["recent"])
            self.assertGreaterEqual(state["counts"]["downloaded"], 1)
            self.assertGreater(state["library"]["bytes"], 0)
            self.assertIn("activity", state)
            self.assertTrue(body)


if __name__ == "__main__":
    unittest.main()
