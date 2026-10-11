"""Task 1.1 -- config defaults, overrides, and the exFAT database refusal."""

import json
import os
import shutil
import tempfile
import unittest

from tuner import config


class ConfigTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tuner-cfg-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, data):
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def test_defaults_fill_the_gaps(self):
        path = self.write({"port": 9999, "keep_n": 12})
        cfg = config.load(path)

        self.assertEqual(cfg["port"], 9999)
        self.assertEqual(cfg["keep_n"], 12)
        # Everything else falls back to the shipped defaults.
        self.assertEqual(cfg["title"], "Tuner")
        self.assertEqual(cfg["seed_n"], 3)
        self.assertEqual(cfg["poll_minutes"], 30)
        self.assertTrue(cfg["skip_shorts"])
        self.assertTrue(cfg["skip_live"])
        self.assertFalse(cfg["watched_delete"])

    def test_missing_file_is_not_an_error(self):
        cfg = config.load(os.path.join(self.tmp, "absent.json"))
        # Private keys (the remembered source path, added at 8.5) are
        # runtime-only and never part of the shipped defaults.
        public = dict((k, v) for k, v in cfg.items() if not k.startswith("_"))
        self.assertEqual(public, config.defaults())

    def test_unknown_keys_are_preserved(self):
        path = self.write({"port": 8770, "future_option": "kept"})
        cfg = config.load(path)
        self.assertEqual(cfg["future_option"], "kept")

        config.save(cfg, path)
        with open(path, "r", encoding="utf-8") as handle:
            written = json.load(handle)
        self.assertEqual(written["future_option"], "kept")

    def test_save_drops_private_keys_and_is_atomic(self):
        path = os.path.join(self.tmp, "out.json")
        cfg = config.defaults()
        cfg["_runtime_only"] = "should not persist"
        config.save(cfg, path)

        with open(path, "r", encoding="utf-8") as handle:
            written = json.load(handle)
        self.assertNotIn("_runtime_only", written)
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_save_keeps_the_file_owner_only(self):
        # It holds the Jellyfin API key; a save must not widen it to the
        # umask, whether the file was 0600 before, 0644, or a stale .tmp.
        path = self.write({"jellyfin_api_key": "k"})
        os.chmod(path, 0o644)
        with open(path + ".tmp", "w") as handle:
            handle.write("stale")
        os.chmod(path + ".tmp", 0o666)
        old_umask = os.umask(0o022)
        try:
            config.save(config.load(path), path)
        finally:
            os.umask(old_umask)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_database_under_library_root_is_refused(self):
        path = self.write({
            "library_root": "/srv/media/youtube",
            "db_path": "/srv/media/youtube/tuner.db",
        })
        with self.assertRaises(config.ConfigError) as caught:
            config.load(path)
        self.assertIn("exFAT", str(caught.exception))

    def test_database_beside_library_root_is_allowed(self):
        path = self.write({
            "library_root": "/srv/media/youtube",
            "db_path": "/var/lib/tuner/tuner.db",
        })
        cfg = config.load(path)
        self.assertEqual(cfg["db_path"], "/var/lib/tuner/tuner.db")

    def test_prefix_collision_is_not_treated_as_nesting(self):
        # /srv/media/youtube-db is a sibling, not a child, of the library.
        path = self.write({
            "library_root": "/srv/media/youtube",
            "db_path": "/srv/media/youtube-db/tuner.db",
        })
        cfg = config.load(path)
        self.assertEqual(cfg["db_path"], "/srv/media/youtube-db/tuner.db")

    def test_malformed_json_raises_config_error(self):
        path = os.path.join(self.tmp, "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(config.ConfigError):
            config.load(path)


if __name__ == "__main__":
    unittest.main()
