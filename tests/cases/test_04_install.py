"""Task 1.5 -- the systemd unit and the installer's shape.

Running the installer needs the Debian box, so that goes on the deploy
checklist. What can be proven here is that the script parses and that the
unit carries the guards this box needs.
"""

import os
import re
import subprocess
import unittest

# tests/cases/ -> tests/ -> repo root
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UNIT = os.path.join(REPO, "tuner.service")
INSTALLER = os.path.join(REPO, "install.sh")


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class UnitTest(unittest.TestCase):

    def setUp(self):
        self.unit = read(UNIT)

    def test_waits_for_the_media_drive(self):
        # Without this, systemd starts Tuner before the exFAT drive mounts
        # and the library tree gets created on the bare mount point.
        self.assertIn("RequiresMountsFor=/srv/media", self.unit)

    def test_restarts_always(self):
        self.assertIn("Restart=always", self.unit)
        self.assertIn("RestartSec=5", self.unit)

    def test_runs_the_package_entrypoint(self):
        self.assertIn("ExecStart=/usr/bin/python3 -m tuner", self.unit)
        self.assertIn("WorkingDirectory=/opt/tuner", self.unit)

    def test_enabled_for_multi_user(self):
        self.assertIn("WantedBy=multi-user.target", self.unit)


class InstallerTest(unittest.TestCase):

    def setUp(self):
        self.script = read(INSTALLER)

    def test_script_parses(self):
        result = subprocess.run(["bash", "-n", INSTALLER],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uses_strict_mode(self):
        self.assertIn("set -euo pipefail", self.script)

    def test_preflights_the_hard_dependencies(self):
        for needle in ("python3", "yt-dlp", "ffmpeg"):
            self.assertIn(needle, self.script)
        # Each missing dependency has to name the command that fixes it.
        self.assertIn("apt install ffmpeg", self.script)
        self.assertIn("releases/latest/download/yt-dlp", self.script)

    def test_refuses_a_database_on_the_media_drive(self):
        self.assertIn("no file locking", self.script)

    def test_preflight_runs_before_any_copy(self):
        preflight = self.script.index("==> Preflight")
        install = self.script.index("==> Installing to")
        self.assertLess(preflight, install)
        # No cp/mkdir may appear in the preflight section.
        head = self.script[preflight:install]
        self.assertNotIn("\ncp ", head)
        self.assertNotIn("\nmkdir ", head)

    def test_does_not_clobber_an_edited_config(self):
        self.assertIn('if [[ ! -f "$DEST/config.json" ]]', self.script)

    def test_mentions_the_firewall_and_jellyfin_setup(self):
        self.assertIn("ufw allow", self.script)
        self.assertIn("metadata", self.script)

    def test_documents_bash_invocation(self):
        # The exec bit does not survive a trip through exFAT.
        self.assertIn("sudo bash install.sh", self.script)

    def test_port_default_matches_the_shipped_config(self):
        shipped = read(os.path.join(REPO, "config.json"))
        self.assertIn('"port": 8770', shipped)
        self.assertTrue(re.search(r'PORT:-8770', self.script))


if __name__ == "__main__":
    unittest.main()
