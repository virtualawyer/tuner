"""Task 8.5 -- the settings panel and the endpoint behind it."""

import contextlib
import json
import os
import threading
import unittest
import urllib.error
import urllib.request

import harness
from tuner import config, jellyfin, server, web


class MarkupTest(unittest.TestCase):

    def setUp(self):
        with harness.temp_app() as app:
            self.markup = web.render(app.cfg)

    def test_the_panel_starts_collapsed(self):
        self.assertIn('id="settings-body" class="hidden"', self.markup)
        self.assertIn('id="settings-toggle"', self.markup)

    def test_every_editable_field_has_an_input(self):
        for key in ("poll_minutes", "seed_n", "keep_n", "min_free_gb",
                    "shorts_max_seconds", "skip_shorts", "skip_live",
                    "subtitles", "jellyfin_url", "jellyfin_api_key",
                    "jellyfin_user_id", "watched_delete",
                    "watched_grace_hours"):
            self.assertIn('id="set-%s"' % key, self.markup)

    def test_the_connection_test_and_save_exist(self):
        self.assertIn('id="jellyfin-test"', self.markup)
        self.assertIn('id="settings-save"', self.markup)
        self.assertIn('id="jellyfin-result"', self.markup)


class ApplyTest(unittest.TestCase):

    def test_numbers_booleans_and_text_are_applied(self):
        with harness.temp_app() as app:
            changed, problems = server.apply_settings(app.cfg, {
                "poll_minutes": "45", "keep_n": 8, "skip_shorts": False,
                "jellyfin_url": "  http://127.0.0.1:8096  ",
            })

            self.assertEqual(problems, [])
            self.assertEqual(app.cfg["poll_minutes"], 45)
            self.assertEqual(app.cfg["keep_n"], 8)
            self.assertFalse(app.cfg["skip_shorts"])
            self.assertEqual(app.cfg["jellyfin_url"], "http://127.0.0.1:8096")
            self.assertEqual(sorted(changed),
                             ["jellyfin_url", "keep_n", "poll_minutes",
                              "skip_shorts"])

    def test_out_of_range_values_are_refused_and_nothing_is_applied(self):
        with harness.temp_app(poll_minutes=30) as app:
            changed, problems = server.apply_settings(app.cfg, {
                "poll_minutes": 1,      # below the 5 minute floor
                "keep_n": 5,            # fine, but must not be applied
            })

            self.assertTrue(problems)
            self.assertEqual(changed, {})
            self.assertEqual(app.cfg["poll_minutes"], 30)

    def test_a_non_numeric_value_is_refused(self):
        with harness.temp_app() as app:
            _, problems = server.apply_settings(app.cfg, {"seed_n": "lots"})
            self.assertIn("seed_n must be a whole number", problems)

    def test_unknown_keys_are_ignored_not_rejected(self):
        with harness.temp_app() as app:
            changed, problems = server.apply_settings(
                app.cfg, {"db_path": "/somewhere/else", "keep_n": 4})
            self.assertEqual(problems, [])
            self.assertEqual(list(changed), ["keep_n"])
            self.assertNotEqual(app.cfg["db_path"], "/somewhere/else")

    def test_the_masked_api_key_coming_back_does_not_overwrite(self):
        secret = "supersecretkey9999"
        with harness.temp_app(jellyfin_api_key=secret) as app:
            masked = jellyfin.mask_key(secret)
            changed, _ = server.apply_settings(
                app.cfg, {"jellyfin_api_key": masked})

            self.assertNotIn("jellyfin_api_key", changed)
            self.assertEqual(app.cfg["jellyfin_api_key"], secret)

    def test_an_empty_api_key_does_not_wipe_the_stored_one(self):
        with harness.temp_app(jellyfin_api_key="secret") as app:
            server.apply_settings(app.cfg, {"jellyfin_api_key": ""})
            self.assertEqual(app.cfg["jellyfin_api_key"], "secret")

    def test_a_new_api_key_does_replace_it(self):
        with harness.temp_app(jellyfin_api_key="old-key-1111") as app:
            server.apply_settings(app.cfg, {"jellyfin_api_key": "new-key-2222"})
            self.assertEqual(app.cfg["jellyfin_api_key"], "new-key-2222")


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

    def post(self, port, payload):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/settings" % port,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_a_save_rewrites_the_config_file(self):
        with harness.temp_app() as app:
            path = os.path.join(app.tmp, "config.json")
            app.cfg["_config_path"] = path

            with self.running(app) as port:
                status, body = self.post(port, {"keep_n": 9,
                                                "poll_minutes": 45})

            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["changed"], ["keep_n", "poll_minutes"])

            with open(path, "r", encoding="utf-8") as handle:
                written = json.load(handle)
            self.assertEqual(written["keep_n"], 9)
            self.assertEqual(written["poll_minutes"], 45)

    def test_the_running_config_reflects_the_change_at_once(self):
        with harness.temp_app() as app:
            app.cfg["_config_path"] = os.path.join(app.tmp, "config.json")
            with self.running(app) as port:
                self.post(port, {"keep_n": 12})
            # Same dict every thread reads, so no restart is needed.
            self.assertEqual(app.cfg["keep_n"], 12)

    def test_private_keys_never_reach_the_file(self):
        with harness.temp_app() as app:
            path = os.path.join(app.tmp, "config.json")
            app.cfg["_config_path"] = path
            with self.running(app) as port:
                self.post(port, {"keep_n": 3})

            with open(path, "r", encoding="utf-8") as handle:
                written = json.load(handle)
            self.assertNotIn("_config_path", written)

    def test_an_out_of_range_value_is_a_400_with_a_message(self):
        with harness.temp_app() as app:
            app.cfg["_config_path"] = os.path.join(app.tmp, "config.json")
            with self.running(app) as port:
                status, body = self.post(port, {"min_free_gb": -5})
            self.assertEqual(status, 400)
            self.assertIn("between", body["error"])

    def test_saving_the_masked_key_keeps_the_real_one(self):
        secret = "supersecretkey9999"
        with harness.temp_app(jellyfin_api_key=secret) as app:
            path = os.path.join(app.tmp, "config.json")
            app.cfg["_config_path"] = path

            with self.running(app) as port:
                self.post(port, {"jellyfin_api_key": jellyfin.mask_key(secret),
                                 "keep_n": 4})

            self.assertEqual(app.cfg["jellyfin_api_key"], secret)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["jellyfin_api_key"], secret)

    def test_nothing_to_change_is_still_a_success(self):
        with harness.temp_app() as app:
            app.cfg["_config_path"] = os.path.join(app.tmp, "config.json")
            with self.running(app) as port:
                status, body = self.post(port, {"unknown_thing": 1})
            self.assertEqual(status, 200)
            self.assertEqual(body["changed"], [])

    def test_the_state_exposes_the_editable_settings_without_the_key(self):
        with harness.temp_app(jellyfin_api_key="supersecretkey9999") as app:
            state = server.build_state(server.App(app.cfg, app.conn))

            self.assertIn("settings", state)
            self.assertEqual(state["settings"]["keep_n"], app.cfg["keep_n"])
            self.assertNotIn("jellyfin_api_key", state["settings"])
            self.assertNotIn("supersecretkey9999", json.dumps(state))


class ConfigPathTest(unittest.TestCase):

    def test_load_remembers_where_it_came_from(self):
        with harness.temp_app() as app:
            path = os.path.join(app.tmp, "written.json")
            config.save(config.defaults(), path)
            loaded = config.load(path)
            self.assertEqual(loaded["_config_path"], path)

    def test_save_defaults_to_that_path(self):
        with harness.temp_app() as app:
            path = os.path.join(app.tmp, "written.json")
            config.save(config.defaults(), path)
            loaded = config.load(path)
            loaded["keep_n"] = 42
            config.save(loaded)

            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["keep_n"], 42)


if __name__ == "__main__":
    unittest.main()
