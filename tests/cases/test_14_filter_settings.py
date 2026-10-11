"""Task 4.2 -- per-channel settings that inherit from the global config."""

import unittest

import harness
from tuner import db, filters


class SettingsForTest(unittest.TestCase):

    def channel(self, app, **overrides):
        row_id = app.add_channel(channel_id="UC_a", title="Alpha",
                                 url="https://www.youtube.com/channel/UC_a",
                                 folder="Alpha")
        if overrides:
            db.update_channel(app.conn, row_id, **overrides)
        return db.get_channel(app.conn, row_id)

    def test_null_columns_inherit_the_global_config(self):
        with harness.temp_app(skip_shorts=True, skip_live=True, keep_n=5) as app:
            row = self.channel(app)
            resolved = filters.settings_for(app.cfg, row)

            self.assertTrue(resolved["skip_shorts"])
            self.assertTrue(resolved["skip_live"])
            self.assertEqual(resolved["keep_n"], 5)

    def test_inheriting_a_global_off_switch(self):
        with harness.temp_app(skip_shorts=False, skip_live=False) as app:
            row = self.channel(app)
            resolved = filters.settings_for(app.cfg, row)

            self.assertFalse(resolved["skip_shorts"])
            self.assertFalse(resolved["skip_live"])

    def test_override_on_beats_a_global_off(self):
        with harness.temp_app(skip_shorts=False, skip_live=False) as app:
            row = self.channel(app, skip_shorts=1, skip_live=1)
            resolved = filters.settings_for(app.cfg, row)

            self.assertTrue(resolved["skip_shorts"])
            self.assertTrue(resolved["skip_live"])

    def test_override_off_beats_a_global_on(self):
        with harness.temp_app(skip_shorts=True, skip_live=True) as app:
            row = self.channel(app, skip_shorts=0, skip_live=0)
            resolved = filters.settings_for(app.cfg, row)

            self.assertFalse(resolved["skip_shorts"])
            self.assertFalse(resolved["skip_live"])

    def test_keep_n_override_including_zero(self):
        with harness.temp_app(keep_n=5) as app:
            self.assertEqual(
                filters.settings_for(app.cfg, self.channel(app, keep_n=2))["keep_n"], 2)

        # Zero is an override meaning unlimited, not an absent value.
        with harness.temp_app(keep_n=5) as app:
            self.assertEqual(
                filters.settings_for(app.cfg, self.channel(app, keep_n=0))["keep_n"], 0)

    def test_each_toggle_is_independent(self):
        with harness.temp_app(skip_shorts=True, skip_live=True, keep_n=5) as app:
            row = self.channel(app, skip_shorts=0)
            resolved = filters.settings_for(app.cfg, row)

            self.assertFalse(resolved["skip_shorts"])
            self.assertTrue(resolved["skip_live"])     # still inheriting
            self.assertEqual(resolved["keep_n"], 5)    # still inheriting

    def test_shorts_threshold_comes_from_the_global_config(self):
        with harness.temp_app(shorts_max_seconds=120) as app:
            self.assertEqual(
                filters.settings_for(app.cfg, self.channel(app))["shorts_max_seconds"],
                120)

    def test_no_channel_row_yields_the_global_defaults(self):
        with harness.temp_app(skip_shorts=True, skip_live=False, keep_n=7) as app:
            resolved = filters.settings_for(app.cfg, None)
            self.assertTrue(resolved["skip_shorts"])
            self.assertFalse(resolved["skip_live"])
            self.assertEqual(resolved["keep_n"], 7)

    def test_resolved_settings_drive_decide(self):
        short = {"duration": 30, "width": 1080, "height": 1920,
                 "live_status": "not_live"}

        with harness.temp_app(skip_shorts=True) as app:
            row = self.channel(app, skip_shorts=0)
            action, _ = filters.decide(short, filters.settings_for(app.cfg, row))
            self.assertEqual(action, filters.ACCEPT)

        with harness.temp_app(skip_shorts=False) as app:
            row = self.channel(app, skip_shorts=1)
            action, reason = filters.decide(short,
                                            filters.settings_for(app.cfg, row))
            self.assertEqual(action, filters.SKIP)
            self.assertEqual(reason, filters.SHORT)


if __name__ == "__main__":
    unittest.main()
