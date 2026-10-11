"""Task 4.3 -- probe every new video and route it to queued or skipped."""

import unittest

import harness
from tuner import db, discover


class PipelineTest(unittest.TestCase):

    def seed(self, app, videos):
        chan = app.add_channel(channel_id="UC_a", title="Alpha",
                               url="https://www.youtube.com/channel/UC_a",
                               folder="Alpha")
        for index, video_id in enumerate(videos):
            app.add_video(video_id, chan, "Placeholder %d" % index,
                          published_at="2026-08-%02dT00:00:00Z" % (index + 1))
        return chan

    def test_the_four_outcomes(self):
        with harness.temp_app(max_attempts=3) as app:
            self.seed(app, ["vidaccept1", "vidshort01", "vidprivat1",
                            "vidflaky01"])
            app.scenario([
                harness.rule_probe(video_id="vidaccept1", title="A good one",
                                   duration=1284, match=["-J", "vidaccept1"]),
                harness.rule_probe(video_id="vidshort01", title="A short",
                                   duration=30, width=1080, height=1920,
                                   match=["-J", "vidshort01"]),
                {"match": ["-J", "vidprivat1"], "returncode": 1,
                 "stderr": "ERROR: Private video"},
                {"match": ["-J", "vidflaky01"], "returncode": 1,
                 "stderr": "ERROR: HTTP Error 503"},
            ])

            counts = discover.process_new(app.conn, app.cfg)

            self.assertEqual(counts["queued"], 1)
            self.assertEqual(counts["skipped"], 2)
            self.assertEqual(counts["retry"], 1)

            self.assertEqual(db.get_video_by_ytid(app.conn, "vidaccept1")["status"],
                             db.QUEUED)

            short = db.get_video_by_ytid(app.conn, "vidshort01")
            self.assertEqual(short["status"], db.SKIPPED)
            self.assertEqual(short["skip_reason"], "short")

            private = db.get_video_by_ytid(app.conn, "vidprivat1")
            self.assertEqual(private["status"], db.SKIPPED)
            self.assertEqual(private["skip_reason"], "private")
            self.assertEqual(private["attempts"], 0)   # no attempts burned

            flaky = db.get_video_by_ytid(app.conn, "vidflaky01")
            self.assertEqual(flaky["status"], db.NEW)  # back in line
            self.assertEqual(flaky["attempts"], 1)

    def test_the_probe_supplies_the_real_title_and_duration(self):
        with harness.temp_app() as app:
            self.seed(app, ["vidaccept1"])
            app.scenario([harness.rule_probe(
                video_id="vidaccept1", title="The real title", duration=742,
                match=["-J"])])

            discover.process_new(app.conn, app.cfg)

            row = db.get_video_by_ytid(app.conn, "vidaccept1")
            self.assertEqual(row["title"], "The real title")
            self.assertEqual(row["duration_s"], 742)

    def test_rows_are_processed_oldest_first(self):
        with harness.temp_app() as app:
            self.seed(app, ["vidoldest1", "vidmiddle1", "vidnewest1"])
            app.scenario([harness.rule_probe(match=["-J"], duration=600)])

            discover.process_new(app.conn, app.cfg)

            probed = [" ".join(call) for call in app.calls()]
            self.assertIn("vidoldest1", probed[0])
            self.assertIn("vidnewest1", probed[-1])

    def test_limit_stops_early(self):
        with harness.temp_app() as app:
            self.seed(app, ["vidone00001", "vidtwo00001", "vidthree001"])
            app.scenario([harness.rule_probe(match=["-J"], duration=600)])

            discover.process_new(app.conn, app.cfg, limit=2)
            self.assertEqual(len(db.list_videos(app.conn, status=db.NEW)), 1)

    def test_repeated_transient_failures_end_in_failed(self):
        with harness.temp_app(max_attempts=3) as app:
            self.seed(app, ["vidflaky01"])
            app.scenario([{"match": ["-J"], "returncode": 1,
                           "stderr": "ERROR: HTTP Error 503"}])

            for expected_status, expected_attempts in ((db.NEW, 1), (db.NEW, 2),
                                                       (db.FAILED, 3)):
                discover.process_new(app.conn, app.cfg)
                row = db.get_video_by_ytid(app.conn, "vidflaky01")
                self.assertEqual(row["status"], expected_status)
                self.assertEqual(row["attempts"], expected_attempts)

            # Once failed it is out of the NEW pool and stops being retried.
            discover.process_new(app.conn, app.cfg)
            self.assertEqual(
                db.get_video_by_ytid(app.conn, "vidflaky01")["attempts"], 3)

    def test_a_rate_limit_does_not_skip_the_video_permanently(self):
        with harness.temp_app(max_attempts=3) as app:
            self.seed(app, ["vidflaky01"])
            app.scenario([{"match": ["-J"], "returncode": 1,
                           "stderr": "ERROR: Sign in to confirm you're not a bot"}])

            discover.process_new(app.conn, app.cfg)
            row = db.get_video_by_ytid(app.conn, "vidflaky01")
            self.assertEqual(row["status"], db.NEW)
            self.assertNotEqual(row["status"], db.SKIPPED)

    def test_per_channel_override_changes_the_outcome(self):
        with harness.temp_app(skip_shorts=True) as app:
            chan = self.seed(app, ["vidshort01"])
            db.update_channel(app.conn, chan, skip_shorts=0)
            app.scenario([harness.rule_probe(video_id="vidshort01", duration=30,
                                             width=1080, height=1920,
                                             match=["-J"])])

            discover.process_new(app.conn, app.cfg)
            self.assertEqual(db.get_video_by_ytid(app.conn, "vidshort01")["status"],
                             db.QUEUED)

    def test_every_outcome_writes_exactly_one_event(self):
        with harness.temp_app() as app:
            self.seed(app, ["vidaccept1", "vidshort01"])
            app.scenario([
                harness.rule_probe(video_id="vidaccept1", duration=600,
                                   match=["-J", "vidaccept1"]),
                harness.rule_probe(video_id="vidshort01", duration=20,
                                   width=1080, height=1920,
                                   match=["-J", "vidshort01"]),
            ])

            discover.process_new(app.conn, app.cfg)
            messages = app.events()
            self.assertEqual(len([m for m in messages if m.startswith("queued")]), 1)
            self.assertEqual(len([m for m in messages if m.startswith("skipped")]), 1)

    def test_no_new_rows_is_a_no_op(self):
        with harness.temp_app() as app:
            counts = discover.process_new(app.conn, app.cfg)
            self.assertEqual(counts, {"queued": 0, "skipped": 0, "failed": 0,
                                      "retry": 0})
            self.assertEqual(app.calls(), [])

    def test_no_row_is_left_stuck_in_probing(self):
        with harness.temp_app() as app:
            self.seed(app, ["vidaccept1", "vidprivat1", "vidflaky01"])
            app.scenario([
                harness.rule_probe(video_id="vidaccept1", duration=600,
                                   match=["-J", "vidaccept1"]),
                {"match": ["-J", "vidprivat1"], "returncode": 1,
                 "stderr": "ERROR: Private video"},
                {"match": ["-J", "vidflaky01"], "returncode": 1,
                 "stderr": "ERROR: HTTP Error 503"},
            ])

            discover.process_new(app.conn, app.cfg)
            self.assertEqual(db.list_videos(app.conn, status=db.PROBING), [])


if __name__ == "__main__":
    unittest.main()
