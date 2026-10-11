"""Task 5.2 -- the free-space guard."""

import collections
import os
import tempfile
import unittest

import harness
from tuner import disk

FakeStat = collections.namedtuple(
    "FakeStat", "f_blocks f_frsize f_bavail f_bfree")


def stat_for(total_gb, free_gb):
    """A statvfs result with a 4096 byte block size."""
    frsize = 4096
    blocks = int(total_gb * disk.GB) // frsize
    avail = int(free_gb * disk.GB) // frsize
    return FakeStat(f_blocks=blocks, f_frsize=frsize, f_bavail=avail,
                    f_bfree=avail)


class UsageTest(unittest.TestCase):

    def test_a_real_path_reports_sane_integers(self):
        report = disk.usage(tempfile.gettempdir())
        self.assertTrue(report["ok"])
        self.assertGreater(report["total"], 0)
        self.assertIsInstance(report["free"], int)
        self.assertGreaterEqual(report["percent"], 0)
        self.assertLessEqual(report["percent"], 100)

    def test_a_missing_path_reports_zeros(self):
        report = disk.usage("/definitely/not/mounted/anywhere")
        self.assertFalse(report["ok"])
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["free"], 0)
        self.assertEqual(report["percent"], 0)

    def test_an_empty_path_reports_zeros(self):
        self.assertFalse(disk.usage("")["ok"])

    def test_percent_is_computed_from_used_over_total(self):
        original = os.statvfs
        os.statvfs = lambda path: stat_for(total_gb=100, free_gb=25)
        try:
            report = disk.usage("/anywhere")
        finally:
            os.statvfs = original
        self.assertEqual(report["percent"], 75)


class HeadroomTest(unittest.TestCase):

    def run_with(self, total_gb, free_gb, min_free_gb=10):
        original = os.statvfs
        os.statvfs = lambda path: stat_for(total_gb, free_gb)
        try:
            with harness.temp_app(min_free_gb=min_free_gb) as app:
                return disk.has_headroom(app.cfg)
        finally:
            os.statvfs = original

    def test_ample_space_has_headroom(self):
        self.assertTrue(self.run_with(total_gb=2800, free_gb=1200))

    def test_tight_space_does_not(self):
        self.assertFalse(self.run_with(total_gb=2800, free_gb=4))

    def test_exactly_at_the_threshold_does_not_count_as_headroom(self):
        self.assertFalse(self.run_with(total_gb=100, free_gb=10, min_free_gb=10))

    def test_just_over_the_threshold_does(self):
        self.assertTrue(self.run_with(total_gb=100, free_gb=11, min_free_gb=10))

    def test_a_missing_library_path_never_has_headroom(self):
        # Deferring is right here: writing into a bare mount point would
        # fill the 13 GB root instead of the media drive.
        with harness.temp_app(library_root="/definitely/not/mounted") as app:
            self.assertFalse(disk.has_headroom(app.cfg))

    def test_min_free_gb_of_zero_only_needs_a_single_byte(self):
        self.assertTrue(self.run_with(total_gb=100, free_gb=1, min_free_gb=0))

    def test_a_nonsense_min_free_gb_falls_back_to_ten(self):
        with harness.temp_app(min_free_gb="lots") as app:
            self.assertEqual(disk.min_free_bytes(app.cfg), 10 * disk.GB)


class HumanTest(unittest.TestCase):

    def test_readable_sizes(self):
        self.assertEqual(disk.human(0), "0 B")
        self.assertEqual(disk.human(512), "512 B")
        self.assertEqual(disk.human(1536), "1.5 KB")
        self.assertEqual(disk.human(3 * disk.GB), "3.0 GB")
        self.assertEqual(disk.human(None), "0 B")


class ServerAliasTest(unittest.TestCase):

    def test_the_server_still_exposes_disk_usage(self):
        from tuner import server
        self.assertIs(server.disk_usage, disk.usage)


if __name__ == "__main__":
    unittest.main()
