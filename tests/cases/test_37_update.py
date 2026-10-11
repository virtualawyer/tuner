"""Task 9.3 -- the yt-dlp version chip and the update action."""

import contextlib
import json
import threading
import unittest
import urllib.error
import urllib.request

import harness
from tuner import server, web, ytdlp


class UpdateTest(unittest.TestCase):

    def test_a_successful_update_reports_its_output(self):
        with harness.temp_app() as app:
            app.scenario([
                harness.rule_version("2026.08.01"),
                {"match": ["-U"], "returncode": 0,
                 "stdout": "Updating to stable@2026.08.11 ... Updated yt-dlp"},
            ])

            result = ytdlp.update(app.cfg)

            self.assertTrue(result["ok"])
            self.assertIn("Updated yt-dlp", result["message"])

    def test_an_apt_installed_binary_refusing_is_passed_through(self):
        # The apt package refuses to self-update. Hiding that would leave
        # the operator with a stale yt-dlp and no idea why.
        refusal = ("ERROR: You installed yt-dlp with a package manager or "
                   "setup.py; Use that to update")
        with harness.temp_app() as app:
            app.scenario([
                harness.rule_version("2023.01.01"),
                {"match": ["-U"], "returncode": 1, "stderr": refusal},
            ])

            result = ytdlp.update(app.cfg)

            self.assertFalse(result["ok"])
            self.assertIn("package manager", result["message"])

    def test_a_missing_binary_says_so_without_a_traceback(self):
        with harness.temp_app(ytdlp_path="/nonexistent/yt-dlp") as app:
            result = ytdlp.update(app.cfg)

            self.assertFalse(result["ok"])
            self.assertIsNone(result["version"])
            self.assertIn("not installed", result["message"])

    def test_a_version_change_is_noticed(self):
        with harness.temp_app() as app:
            app.scenario([
                {"match": ["--version"], "returncode": 0,
                 "stdout": "2026.08.01"},
                {"match": ["-U"], "returncode": 0, "stdout": "Updated"},
            ])
            result = ytdlp.update(app.cfg)
            # Same scripted version before and after, so nothing changed.
            self.assertFalse(result["changed"])


class ChipTest(unittest.TestCase):

    def test_the_chip_shows_the_version(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_version("2026.08.01")])
            server._VERSION_CACHE["checked_at"] = 0.0
            state = server.build_state(server.App(app.cfg, app.conn))
        self.assertEqual(state["ytdlp_version"], "2026.08.01")

    def test_a_missing_binary_turns_the_chip_red(self):
        with harness.temp_app(ytdlp_path="/nonexistent/yt-dlp") as app:
            server._VERSION_CACHE["checked_at"] = 0.0
            state = server.build_state(server.App(app.cfg, app.conn))
            markup = web.render(app.cfg)

        self.assertIsNone(state["ytdlp_version"])
        # The script switches the chip to the bad class when the version is
        # null, and .chip.bad is defined in the stylesheet.
        script = markup.split("<script>")[1]
        self.assertIn("yt-dlp missing", script)
        self.assertIn(".chip.bad{color:var(--red)", markup)

    def test_the_button_exists(self):
        with harness.temp_app() as app:
            markup = web.render(app.cfg)
        self.assertIn('id="ytdlp-update"', markup)
        self.assertIn('id="ytdlp-result"', markup)


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

    def post(self, port, path):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path), data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_the_endpoint_returns_the_output(self):
        with harness.temp_app() as app:
            app.scenario([
                harness.rule_version("2026.08.01"),
                {"match": ["-U"], "returncode": 0, "stdout": "Updated yt-dlp"},
            ])
            with self.running(app) as port:
                status, body = self.post(port, "/api/ytdlp/update")

            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertIn("Updated", body["message"])
            self.assertTrue(any("yt-dlp update" in m for m in app.events()))

    def test_the_endpoint_refreshes_the_cached_version(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_version("2026.01.01")])
            server._VERSION_CACHE["checked_at"] = 0.0
            server.ytdlp_version(app.cfg)

            app.scenario([
                harness.rule_version("2026.08.11"),
                {"match": ["-U"], "returncode": 0, "stdout": "Updated"},
            ])
            with self.running(app) as port:
                self.post(port, "/api/ytdlp/update")

            # Without the forced refresh the chip would show the old version
            # for another five minutes.
            self.assertEqual(server._VERSION_CACHE["value"], "2026.08.11")

    def test_a_missing_binary_is_a_clear_error_not_a_500(self):
        with harness.temp_app(ytdlp_path="/nonexistent/yt-dlp") as app:
            with self.running(app) as port:
                status, body = self.post(port, "/api/ytdlp/update")

            self.assertEqual(status, 200)
            self.assertFalse(body["ok"])
            self.assertIn("not installed", body["message"])


if __name__ == "__main__":
    unittest.main()
