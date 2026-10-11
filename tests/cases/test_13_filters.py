"""Task 4.1 -- the filter engine."""

import unittest

from tuner import filters

ON = {"skip_shorts": True, "skip_live": True, "shorts_max_seconds": 60}


def meta(**overrides):
    base = {"duration": 600, "width": 1920, "height": 1080,
            "live_status": "not_live", "was_live": False}
    base.update(overrides)
    return base


class DecideTest(unittest.TestCase):

    def assertAccepted(self, item, settings=None):
        action, reason = filters.decide(item, settings or ON)
        self.assertEqual(action, filters.ACCEPT,
                         "expected accept, got skip/%s" % reason)
        self.assertIsNone(reason)

    def assertSkipped(self, item, expected, settings=None):
        action, reason = filters.decide(item, settings or ON)
        self.assertEqual(action, filters.SKIP, "expected skip/%s" % expected)
        self.assertEqual(reason, expected)

    def test_the_nine_cases(self):
        # A normal video.
        self.assertAccepted(meta(duration=1284))
        # A 45 second vertical clip: a Short.
        self.assertSkipped(meta(duration=45, width=1080, height=1920),
                           filters.SHORT)
        # A 45 second landscape clip: an ordinary short video, not a Short.
        self.assertAccepted(meta(duration=45, width=1920, height=1080))
        # 40 seconds with unknown dimensions: duration alone decides.
        self.assertSkipped(meta(duration=40, width=None, height=None),
                           filters.SHORT)
        # Live right now.
        self.assertSkipped(meta(live_status="is_live", duration=None),
                           filters.LIVE)
        # A premiere that has not started.
        self.assertSkipped(meta(live_status="is_upcoming", duration=None),
                           filters.UPCOMING)
        # A finished stream archive.
        self.assertSkipped(meta(live_status="was_live", duration=7200),
                           filters.WAS_LIVE)
        # A 20 minute video with no dimensions.
        self.assertAccepted(meta(duration=1200, width=None, height=None))
        # Null duration and not live: nothing to judge it on, so let it pass
        # and let the download decide.
        self.assertAccepted(meta(duration=None))

    def test_square_video_at_the_threshold_counts_as_a_short(self):
        self.assertSkipped(meta(duration=60, width=1080, height=1080),
                           filters.SHORT)

    def test_one_second_over_the_threshold_is_kept(self):
        self.assertAccepted(meta(duration=61, width=1080, height=1920))

    def test_the_threshold_is_configurable(self):
        settings = dict(ON, shorts_max_seconds=120)
        self.assertSkipped(meta(duration=90, width=1080, height=1920),
                           filters.SHORT, settings)
        self.assertAccepted(meta(duration=90, width=1080, height=1920), ON)

    def test_was_live_boolean_alone_is_enough(self):
        self.assertSkipped(meta(live_status="not_live", was_live=True),
                           filters.WAS_LIVE)

    def test_post_live_is_treated_as_live(self):
        self.assertSkipped(meta(live_status="post_live"), filters.LIVE)

    def test_live_is_judged_before_short(self):
        # A 30 second vertical premiere reports as upcoming, not as a Short:
        # the more useful reason wins.
        self.assertSkipped(meta(duration=30, width=1080, height=1920,
                                live_status="is_upcoming"), filters.UPCOMING)


class SwitchedOffTest(unittest.TestCase):

    def test_shorts_filter_off_keeps_shorts(self):
        settings = dict(ON, skip_shorts=False)
        action, reason = filters.decide(
            meta(duration=30, width=1080, height=1920), settings)
        self.assertEqual(action, filters.ACCEPT)
        self.assertIsNone(reason)

    def test_live_filter_off_keeps_streams(self):
        settings = dict(ON, skip_live=False)
        action, _ = filters.decide(meta(live_status="was_live"), settings)
        self.assertEqual(action, filters.ACCEPT)

    def test_both_off_accepts_everything(self):
        settings = {"skip_shorts": False, "skip_live": False}
        for item in (meta(duration=5, width=1080, height=1920),
                     meta(live_status="is_live"),
                     meta(live_status="is_upcoming")):
            self.assertEqual(filters.decide(item, settings)[0], filters.ACCEPT)

    def test_empty_inputs_do_not_raise(self):
        self.assertEqual(filters.decide(None, None)[0], filters.ACCEPT)
        self.assertEqual(filters.decide({}, ON)[0], filters.ACCEPT)


if __name__ == "__main__":
    unittest.main()
