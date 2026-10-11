"""Browsing a channel's lineup and picking videos by hand.

A subscription answers "keep me current". This answers "I want those four,
and nothing else, and I am not following this channel".
"""

import contextlib
import json
import threading
import unittest
import urllib.error
import urllib.request

import harness
from tuner import db, discover, filters, retention, server, web, worker, ytdlp

CHANNEL = "UCabcdefghijklmnopqrstuv"


def lineup(count=5, channel_title="Alpha Channel", durations=None):
    entries = []
    for n in range(count):
        entry = {"id": "vid%08d" % (n + 1), "title": "Video %d" % (n + 1)}
        if durations:
            entry["duration"] = durations[n % len(durations)]
        entries.append(entry)
    # "--playlist-items 1-" and not just "0": the fake matches on argv
    # substrings, and "0" also appears inside "1-100".
    return {"match": ["--playlist-items 1-"], "returncode": 0,
            "stdout": json.dumps({"_type": "playlist", "id": CHANNEL,
                                  "channel": channel_title,
                                  "entries": entries})}


def resolve():
    return harness.rule_resolve(CHANNEL, "Alpha Channel",
                                match=["--playlist-items 0"])


class ListChannelTest(unittest.TestCase):

    def test_it_asks_for_the_requested_slice(self):
        with harness.temp_app() as app:
            app.scenario([lineup(3)])
            ytdlp.list_channel(app.cfg, "https://www.youtube.com/@alpha",
                               limit=200)

            call = app.last_call()
            self.assertEqual(call[call.index("--playlist-items") + 1], "1-200")

    def test_the_slice_is_bounded(self):
        with harness.temp_app() as app:
            app.scenario([lineup(3)])
            ytdlp.list_channel(app.cfg, "https://www.youtube.com/@alpha",
                               limit=99999)

            call = app.last_call()
            self.assertEqual(call[call.index("--playlist-items") + 1],
                             "1-%d" % ytdlp.BROWSE_MAX)

    def test_durations_come_through_when_offered(self):
        with harness.temp_app() as app:
            app.scenario([lineup(2, durations=[42, 900])])
            listing = ytdlp.list_channel(app.cfg,
                                         "https://www.youtube.com/@alpha")
        self.assertEqual(listing["entries"][0]["duration"], 42)


class BrowseTest(unittest.TestCase):

    def test_it_lists_the_channel(self):
        with harness.temp_app() as app:
            app.scenario([resolve(), lineup(5)])
            result = discover.browse(app.conn, app.cfg, "@alpha")

            self.assertTrue(result["ok"], result["message"])
            self.assertEqual(result["channel_id"], CHANNEL)
            self.assertEqual(result["channel_title"], "Alpha Channel")
            self.assertEqual(len(result["entries"]), 5)
            self.assertFalse(result["tracked"])

    def test_it_says_what_it_already_knows_about_each_video(self):
        with harness.temp_app() as app:
            chan = app.add_channel(channel_id=CHANNEL, title="Alpha Channel",
                                   url="https://www.youtube.com/channel/" + CHANNEL,
                                   folder="Alpha Channel")
            app.add_video("vid00000001", chan, "Video 1")
            row = db.get_video_by_ytid(app.conn, "vid00000001")
            db.set_video_status(app.conn, row["id"], db.DOWNLOADED, file_size=10)
            app.add_video("vid00000002", chan, "Video 2")
            row = db.get_video_by_ytid(app.conn, "vid00000002")
            db.set_video_status(app.conn, row["id"], db.SKIPPED,
                                skip_reason="short")

            app.scenario([resolve(), lineup(3)])
            result = discover.browse(app.conn, app.cfg, "@alpha")

            states = dict((e["video_id"], e["status"]) for e in result["entries"])
            self.assertEqual(states["vid00000001"], "downloaded")
            self.assertEqual(states["vid00000002"], "skipped")
            self.assertIsNone(states["vid00000003"])
            self.assertTrue(result["tracked"])
            self.assertTrue(result["subscribed"])

    def test_a_non_youtube_url_is_refused_without_a_subprocess(self):
        with harness.temp_app() as app:
            result = discover.browse(app.conn, app.cfg, "https://vimeo.com/1")
            self.assertFalse(result["ok"])
            self.assertEqual(app.calls(), [])

    def test_a_listing_failure_is_reported_not_raised(self):
        with harness.temp_app() as app:
            app.scenario([resolve(),
                          {"match": ["--flat-playlist", "--playlist-items"],
                           "returncode": 1, "stderr": "ERROR: no such channel"}])
            result = discover.browse(app.conn, app.cfg, "@alpha")
            self.assertFalse(result["ok"])
            self.assertIn("no such channel", result["message"])


class PickTest(unittest.TestCase):

    def test_picking_creates_the_channel_unsubscribed(self):
        with harness.temp_app() as app:
            app.scenario([resolve()])
            result = discover.pick(app.conn, app.cfg, "@alpha", [
                {"video_id": "vid00000002", "title": "Video 2"}])

            self.assertTrue(result["ok"], result["message"])
            channel = db.get_channel_by_ytid(app.conn, CHANNEL)
            # Holding a few chosen videos is not following the channel.
            self.assertEqual(channel["enabled"], 0)
            self.assertEqual(db.list_channels(app.conn, enabled_only=True), [])

    def test_the_chosen_videos_are_queued_and_marked(self):
        with harness.temp_app() as app:
            app.scenario([resolve()])
            discover.pick(app.conn, app.cfg, "@alpha", [
                {"video_id": "vid00000002", "title": "Video 2"},
                {"video_id": "vid00000005", "title": "Video 5"}])

            rows = db.list_videos(app.conn)
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(row["status"], db.NEW)
                self.assertEqual(row["picked"], 1)

    def test_an_existing_subscription_is_not_disturbed(self):
        with harness.temp_app() as app:
            app.add_channel(channel_id=CHANNEL, title="Alpha Channel",
                            url="https://www.youtube.com/channel/" + CHANNEL,
                            folder="Alpha Channel")
            app.scenario([resolve()])
            discover.pick(app.conn, app.cfg, "@alpha",
                          [{"video_id": "vid00000002"}])

            self.assertEqual(
                db.get_channel_by_ytid(app.conn, CHANNEL)["enabled"], 1)

    def test_a_skipped_video_can_be_picked_back(self):
        with harness.temp_app() as app:
            chan = app.add_channel(channel_id=CHANNEL, title="Alpha Channel",
                                   url="https://www.youtube.com/channel/" + CHANNEL,
                                   folder="Alpha Channel")
            app.add_video("vid00000002", chan, "Video 2")
            row = db.get_video_by_ytid(app.conn, "vid00000002")
            db.set_video_status(app.conn, row["id"], db.SKIPPED,
                                skip_reason="pre-existing")

            app.scenario([resolve()])
            discover.pick(app.conn, app.cfg, "@alpha",
                          [{"video_id": "vid00000002"}])

            after = db.get_video_by_ytid(app.conn, "vid00000002")
            self.assertEqual(after["status"], db.NEW)
            self.assertEqual(after["picked"], 1)
            self.assertIsNone(after["skip_reason"])

    def test_something_already_downloaded_is_left_alone(self):
        with harness.temp_app() as app:
            chan = app.add_channel(channel_id=CHANNEL, title="Alpha Channel",
                                   url="https://www.youtube.com/channel/" + CHANNEL,
                                   folder="Alpha Channel")
            app.add_video("vid00000002", chan, "Video 2")
            row = db.get_video_by_ytid(app.conn, "vid00000002")
            db.set_video_status(app.conn, row["id"], db.DOWNLOADED,
                                file_path="/x.mp4", file_size=10)

            app.scenario([resolve()])
            result = discover.pick(app.conn, app.cfg, "@alpha",
                                   [{"video_id": "vid00000002"}])

            self.assertEqual(result["queued"], 0)
            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000002")["status"],
                db.DOWNLOADED)

    def test_rubbish_ids_are_dropped(self):
        with harness.temp_app() as app:
            app.scenario([resolve()])
            result = discover.pick(app.conn, app.cfg, "@alpha",
                                   [{"video_id": "../../etc/passwd"},
                                    {"video_id": ""}, "not an id"])
            self.assertFalse(result["ok"])
            self.assertEqual(result["message"], "nothing selected")


class PickedBehaviourTest(unittest.TestCase):
    """A deliberate choice outranks the automatic rules."""

    def picked_row(self, app, video_id="vid00000001"):
        chan = app.add_channel(channel_id=CHANNEL, title="Alpha Channel",
                               url="https://www.youtube.com/channel/" + CHANNEL,
                               folder="Alpha Channel")
        app.add_video(video_id, chan, "A short", published_at="2026-08-01T00:00:00Z",
                      picked=True)
        return chan

    def test_a_picked_short_is_not_filtered_out(self):
        with harness.temp_app(skip_shorts=True) as app:
            self.picked_row(app)
            app.scenario([harness.rule_probe(video_id="vid00000001",
                                             duration=25, width=1080,
                                             height=1920, match=["-J"])])

            discover.process_new(app.conn, app.cfg)

            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000001")["status"],
                db.QUEUED)

    def test_a_picked_livestream_is_not_filtered_out(self):
        with harness.temp_app(skip_live=True) as app:
            self.picked_row(app)
            app.scenario([harness.rule_probe(video_id="vid00000001",
                                             live_status="was_live",
                                             duration=7200, match=["-J"])])

            discover.process_new(app.conn, app.cfg)

            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000001")["status"],
                db.QUEUED)

    def test_an_unpicked_short_is_still_filtered(self):
        with harness.temp_app(skip_shorts=True) as app:
            chan = app.add_channel(channel_id=CHANNEL, title="Alpha Channel",
                                   url="https://www.youtube.com/channel/" + CHANNEL,
                                   folder="Alpha Channel")
            app.add_video("vid00000002", chan, "A short")
            app.scenario([harness.rule_probe(video_id="vid00000002",
                                             duration=25, width=1080,
                                             height=1920, match=["-J"])])

            discover.process_new(app.conn, app.cfg)

            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vid00000002")["status"],
                db.SKIPPED)

    def test_keep_n_never_prunes_a_pick(self):
        with harness.temp_app(keep_n=1) as app:
            chan = app.add_channel(channel_id=CHANNEL, title="Alpha Channel",
                                   url="https://www.youtube.com/channel/" + CHANNEL,
                                   folder="Alpha Channel")
            for index in range(4):
                video_id = "vid%08d" % (index + 1)
                path = app.make_file("Alpha Channel/%s.mp4" % video_id, size=100)
                app.add_video(video_id, chan, "Video %d" % index,
                              published_at="2026-08-%02dT00:00:00Z" % (index + 1),
                              picked=(index < 2))
                row = db.get_video_by_ytid(app.conn, video_id)
                db.set_video_status(app.conn, row["id"], db.DOWNLOADED,
                                    file_path=path, file_size=100)

            channel = db.get_channel(app.conn, chan)
            result = retention.prune_channel(app.conn, app.cfg, channel)

            # Two picks are exempt; of the two ordinary ones, keep 1.
            self.assertEqual(result["deleted"], 1)
            survivors = db.list_videos(app.conn, status=db.DOWNLOADED)
            self.assertEqual(len(survivors), 3)
            self.assertEqual(
                sorted(r["video_id"] for r in survivors if r["picked"]),
                ["vid00000001", "vid00000002"])


class EndpointTest(unittest.TestCase):

    @contextlib.contextmanager
    def running(self, app):
        httpd = server.make_server(server.App(app.cfg, app.conn),
                                   host="127.0.0.1", port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            httpd.server_close()

    def post(self, port, path, payload):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_browse_then_pick(self):
        with harness.temp_app() as app:
            app.scenario([resolve(), lineup(4)])
            with self.running(app) as port:
                status, listing = self.post(port, "/api/browse",
                                            {"url": "@alpha", "limit": 50})
                self.assertEqual(status, 200)
                self.assertTrue(listing["ok"])
                self.assertEqual(len(listing["entries"]), 4)

                chosen = [{"video_id": e["video_id"], "title": e["title"]}
                          for e in listing["entries"][:2]]
                status, picked = self.post(port, "/api/pick",
                                           {"url": "@alpha", "videos": chosen})

            self.assertEqual(status, 200)
            self.assertEqual(picked["queued"], 2)
            self.assertEqual(len(db.list_videos(app.conn)), 2)

    def test_bad_requests(self):
        with harness.temp_app() as app:
            with self.running(app) as port:
                self.assertEqual(self.post(port, "/api/browse", {})[0], 400)
                self.assertEqual(
                    self.post(port, "/api/pick",
                              {"url": "@a", "videos": "nope"})[0], 400)


class UiTest(unittest.TestCase):

    def setUp(self):
        with harness.temp_app() as app:
            self.markup = web.render(app.cfg)

    def test_the_panel_exists_and_starts_collapsed(self):
        for element_id in ("browse-toggle", "browse-body", "browse-url",
                           "browse-limit", "browse-btn", "browse-list",
                           "browse-all", "browse-none", "browse-download"):
            self.assertIn('id="%s"' % element_id, self.markup)
        self.assertIn('id="browse-body" class="hidden"', self.markup)

    def test_it_explains_what_picking_means(self):
        self.assertIn("ignore the Shorts and livestream filters", self.markup)
        self.assertIn("keep-N never prunes them", self.markup)

    def test_already_downloaded_rows_cannot_be_selected(self):
        script = self.markup.split("<script>")[1]
        self.assertIn("box.disabled=have", script.replace(" ", ""))

    def test_a_pick_is_visible_in_the_recent_list(self):
        script = self.markup.split("<script>")[1]
        self.assertIn("video.picked", script)


if __name__ == "__main__":
    unittest.main()
