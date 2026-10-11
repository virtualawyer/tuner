"""Docker packaging.

Building the image needs Docker, which the build machine does not have, so
that goes on the deploy checklist. What is provable here is that the
entrypoint parses, that its seeded config matches the app's real defaults,
and that the compose file does not put the database on the media drive.
"""

import json
import os
import re
import subprocess
import unittest

from tuner import config

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
ENTRYPOINT = os.path.join(REPO, "docker", "entrypoint.sh")
DOCKERFILE = os.path.join(REPO, "Dockerfile")
COMPOSE = os.path.join(REPO, "docker-compose.yml")
IGNORE = os.path.join(REPO, ".dockerignore")


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def seeded_config():
    """The JSON the entrypoint writes on first run, with vars filled in."""
    body = read(ENTRYPOINT)
    block = body.split("cat > \"$CONFIG\" <<EOF", 1)[1].split("\nEOF", 1)[0]
    filled = (block
              .replace("${PORT}", "8770")
              .replace("${LIBRARY}", "/media/youtube")
              .replace("${CONFIG_DIR}", "/config"))
    return json.loads(filled)


class EntrypointTest(unittest.TestCase):

    def setUp(self):
        self.body = read(ENTRYPOINT)

    def test_it_parses(self):
        result = subprocess.run(["bash", "-n", ENTRYPOINT],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_strict_mode(self):
        self.assertIn("set -euo pipefail", self.body)

    def test_it_refuses_a_config_inside_the_library(self):
        # The one Docker-specific way to lose data: mapping /config onto the
        # exFAT drive, where SQLite has no locking.
        self.assertIn("exit 2", self.body)
        self.assertIn("no file locking", self.body)

    def test_it_only_seeds_when_the_config_is_absent(self):
        self.assertIn('if [[ ! -f "$CONFIG" ]]', self.body)

    def test_it_hands_over_to_the_real_entrypoint(self):
        self.assertIn('exec python3 -m tuner --config "$CONFIG" "$@"',
                      self.body)


class SeededConfigTest(unittest.TestCase):

    def setUp(self):
        self.seeded = seeded_config()

    def test_it_is_valid_json_with_no_unknown_keys(self):
        unknown = set(self.seeded) - set(config.DEFAULTS)
        self.assertEqual(unknown, set(),
                         "seeded config has keys the app does not know")

    def test_it_covers_every_key_the_app_has(self):
        missing = set(config.DEFAULTS) - set(self.seeded)
        self.assertEqual(missing, set(),
                         "seeded config is missing keys from DEFAULTS")

    def test_the_values_match_the_apps_defaults_except_the_paths(self):
        # Only the three container-specific values may differ, so a default
        # changed in config.py cannot silently drift from the image.
        expected_differences = {"library_root", "db_path", "host"}
        differing = set(key for key, value in self.seeded.items()
                        if config.DEFAULTS.get(key) != value)
        self.assertEqual(differing - expected_differences, set())

    def test_it_passes_the_apps_own_validation(self):
        # In particular the database must not resolve under the library.
        config.validate(dict(self.seeded))

    def test_the_database_sits_beside_the_config_not_in_the_library(self):
        self.assertTrue(self.seeded["db_path"].startswith("/config/"))
        self.assertEqual(self.seeded["library_root"], "/media/youtube")

    def test_it_listens_on_all_interfaces(self):
        self.assertEqual(self.seeded["host"], "0.0.0.0")


class DockerfileTest(unittest.TestCase):

    def setUp(self):
        self.body = read(DOCKERFILE)

    def test_ffmpeg_is_installed(self):
        self.assertIn("ffmpeg", self.body)

    def test_ytdlp_comes_from_the_standalone_build_not_apt(self):
        # The Debian package is months old and refuses to self-update.
        self.assertIn("releases/latest/download/yt-dlp", self.body)
        self.assertNotIn("apt-get install -y yt-dlp", self.body)
        self.assertNotIn("pip install yt-dlp", self.body)

    def test_the_build_fails_if_ytdlp_is_broken(self):
        self.assertIn("yt-dlp --version", self.body)

    def test_both_volumes_and_the_port_are_declared(self):
        self.assertIn("/config", self.body)
        self.assertIn("/media/youtube", self.body)
        self.assertIn("EXPOSE 8770", self.body)

    def test_there_is_a_healthcheck_against_the_api(self):
        self.assertIn("HEALTHCHECK", self.body)
        self.assertIn("/healthz", self.body)

    def test_only_the_package_is_copied_in(self):
        self.assertIn("COPY tuner/ /app/tuner/", self.body)


class ComposeTest(unittest.TestCase):

    def setUp(self):
        self.body = read(COMPOSE)

    def test_the_config_volume_is_not_on_the_media_drive(self):
        mounts = re.findall(r"^\s*-\s+(/\S+):(/\S+)\s*$", self.body, re.M)
        config_hosts = [host for host, container in mounts
                        if container == "/config"]
        self.assertEqual(config_hosts, ["/opt/tuner"])
        for host in config_hosts:
            self.assertFalse(host.startswith("/srv/media"),
                             "the database would land on exFAT")

    def test_the_library_is_mounted_from_the_media_drive(self):
        # The host side must match the folder that exists on the box, but
        # the container side is fixed: config.json refers to it.
        mounts = re.findall(r"^\s*-\s+(/\S+):(/media/youtube)\s*$",
                            self.body, re.M)
        self.assertEqual(len(mounts), 1)
        host, _ = mounts[0]
        self.assertEqual(host.lower(), "/srv/media/youtube")

    def test_the_port_and_restart_policy(self):
        self.assertIn('"8770:8770"', self.body)
        self.assertIn("restart: unless-stopped", self.body)

    def test_it_leaves_headroom_for_jellyfin(self):
        self.assertIn("cpus:", self.body)
        self.assertIn("mem_limit:", self.body)


class DockerignoreTest(unittest.TestCase):

    def test_the_build_context_excludes_what_the_image_does_not_need(self):
        body = read(IGNORE)
        for excluded in ("tests/", "dist/", "docs/", "__pycache__", ".git"):
            self.assertIn(excluded, body)


if __name__ == "__main__":
    unittest.main()
