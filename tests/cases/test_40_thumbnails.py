"""Thumbnails as Jellyfin-readable sidecars.

The thumbnail was always embedded in the MP4, but Jellyfin does not
reliably read cover art out of a container. What it does read is a matching
image file beside the video, so one is written too.
"""

import os
import unittest

import harness
from tuner import config, db, paths, server, web, worker, ytdlp

VIDEO = "dQw4w9WgXcQ"


def runtime(app):
    return server.App(app.cfg, app.conn)


def queued_row(app, title="A video", published="2026-08-11T12:00:00+00:00"):
    chan = app.add_channel(channel_id="UC_a", title="Alpha Channel",
                           url="https://www.youtube.com/channel/UC_a",
                           folder="Alpha Channel")
    app.add_video(VIDEO, chan, title, published_at=published)
    row = db.get_video_by_ytid(app.conn, VIDEO)
    db.set_video_status(app.conn, row["id"], db.QUEUED)
    return db.get_video_by_ytid(app.conn, VIDEO)


class ArgumentTest(unittest.TestCase):

    def args(self, **overrides):
        with harness.temp_app(**overrides) as app:
            return ytdlp.download_args(app.cfg, VIDEO, "/tmp/tuner-x")

    def test_a_sidecar_is_requested_by_default(self):
        args = self.args()
        self.assertIn("--write-thumbnail", args)
        # YouTube serves webp, which Jellyfin handles poorly as a poster.
        self.assertEqual(args[args.index("--convert-thumbnails") + 1], "jpg")

    def test_it_is_still_embedded_as_well(self):
        self.assertIn("--embed-thumbnail", self.args())

    def test_the_setting_switches_the_sidecar_off(self):
        args = self.args(thumbnails=False)
        self.assertNotIn("--write-thumbnail", args)
        self.assertNotIn("--convert-thumbnails", args)
        # Embedding is unaffected: it costs nothing extra on disk.
        self.assertIn("--embed-thumbnail", args)

    def test_thumbnails_and_subtitles_are_independent(self):
        args = self.args(thumbnails=True, subtitles=False)
        self.assertIn("--write-thumbnail", args)
        self.assertNotIn("--write-subs", args)

        args = self.args(thumbnails=False, subtitles=True)
        self.assertNotIn("--write-thumbnail", args)
        self.assertIn("--write-subs", args)


class FinalizeTest(unittest.TestCase):

    def tearDown(self):
        worker.set_current(None)

    def test_the_image_lands_beside_the_video_with_a_matching_name(self):
        with harness.temp_app(min_free_gb=0) as app:
            row = queued_row(app)
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, VIDEO + ".mp4"), size=4096)
            app.make_file(os.path.join(tmp, VIDEO + ".jpg"), size=512)

            hand = worker.Worker(runtime(app))
            channel = db.get_channel(app.conn, row["channel_row_id"])
            final = hand.finalize(row, channel,
                                  os.path.join(tmp, VIDEO + ".mp4"), tmp)

            poster = final[:-len(".mp4")] + ".jpg"
            self.assertTrue(os.path.exists(poster))
            # Jellyfin pairs an image to a video by identical basename.
            self.assertEqual(os.path.splitext(os.path.basename(final))[0],
                             os.path.splitext(os.path.basename(poster))[0])

    def test_a_webp_thumbnail_is_still_carried_across(self):
        with harness.temp_app(min_free_gb=0) as app:
            row = queued_row(app)
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            app.make_file(os.path.join(tmp, VIDEO + ".mp4"), size=4096)
            app.make_file(os.path.join(tmp, VIDEO + ".webp"), size=512)

            hand = worker.Worker(runtime(app))
            channel = db.get_channel(app.conn, row["channel_row_id"])
            final = hand.finalize(row, channel,
                                  os.path.join(tmp, VIDEO + ".mp4"), tmp)

            self.assertTrue(os.path.exists(final[:-len(".mp4")] + ".webp"))

    def test_the_image_is_never_mistaken_for_the_video(self):
        with harness.temp_app(min_free_gb=0) as app:
            tmp = paths.tmp_dir(app.cfg, VIDEO)
            # A pathological case: the image is larger than the video.
            app.make_file(os.path.join(tmp, VIDEO + ".jpg"), size=99999)
            app.make_file(os.path.join(tmp, VIDEO + ".mp4"), size=100)

            self.assertTrue(worker.find_media(tmp).endswith(".mp4"))

    def test_everything_arrives_together(self):
        with harness.temp_app(min_free_gb=0) as app:
            queued_row(app)
            app.scenario([harness.rule_download(
                VIDEO, size=4096, match=["--merge-output-format"])])
            # Script the fake to produce what yt-dlp actually would.
            app.scenario([{
                "match": ["--merge-output-format"],
                "creates": ["%(id)s.mp4", "%(id)s.en.srt", "%(id)s.jpg"],
                "creates_size": 4096,
                "returncode": 0,
            }])

            worker.Worker(runtime(app))._once()

            row = db.get_video_by_ytid(app.conn, VIDEO)
            self.assertEqual(row["status"], db.DOWNLOADED)
            stem = row["file_path"][:-len(".mp4")]
            for suffix in (".mp4", ".en.srt", ".jpg"):
                self.assertTrue(os.path.exists(stem + suffix),
                                "missing %s" % suffix)


class SettingTest(unittest.TestCase):

    def test_the_default_is_on(self):
        self.assertTrue(config.defaults()["thumbnails"])

    def test_the_toggle_exists_in_the_page(self):
        with harness.temp_app() as app:
            markup = web.render(app.cfg)
        self.assertIn('id="set-thumbnails"', markup)

        # The script builds ids as 'set-' + key, so what has to be present
        # is the key in the flag list, not the literal element id.
        script = markup.split("<script>")[1]
        flags = script.split("var SETTINGS_FLAGS=")[1].split(";")[0]
        self.assertIn("'thumbnails'", flags)

    def test_it_is_editable_through_the_settings_endpoint(self):
        with harness.temp_app() as app:
            changed, problems = server.apply_settings(app.cfg,
                                                      {"thumbnails": False})
            self.assertEqual(problems, [])
            self.assertIn("thumbnails", changed)
            self.assertFalse(app.cfg["thumbnails"])

    def test_it_reaches_the_state_payload(self):
        with harness.temp_app() as app:
            state = server.build_state(server.App(app.cfg, app.conn))
        self.assertIn("thumbnails", state["settings"])


if __name__ == "__main__":
    unittest.main()
