"""0.6.0 -- who may drive Tuner: other websites never, and with a password set,
only someone who knows it."""

import base64
import contextlib
import json
import os
import threading
import unittest
import urllib.error
import urllib.request

import harness
from tuner import VERSION, config, server, web


@contextlib.contextmanager
def running(app, password=""):
    served = server.App(app.cfg, app.conn)
    served.password = password
    httpd = server.make_server(served, host="127.0.0.1", port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(port, path, method="GET", body=None, content_type=None, auth=None):
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    if auth is not None:
        token = base64.b64encode(auth.encode("utf-8")).decode("ascii")
        headers["Authorization"] = "Basic " + token
    request = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=None if body is None else body.encode("utf-8"),
        headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


class CrossSiteTest(unittest.TestCase):
    """A page on another site can POST text/plain at a LAN address without
    the browser asking first. That must change nothing."""

    def test_a_text_plain_settings_post_is_refused_and_changes_nothing(self):
        with harness.temp_app(jellyfin_api_key="real-key-0123456789") as app:
            app.cfg["_config_path"] = os.path.join(app.tmp, "config.json")
            body = json.dumps({"jellyfin_url": "http://attacker.example",
                               "watched_delete": True, "keep_n": 1})
            with running(app) as port:
                status, _, text = call(port, "/api/settings", "POST", body,
                                       "text/plain;charset=UTF-8")
            self.assertEqual(status, 415)
            self.assertIn("application/json", json.loads(text)["error"])
            self.assertEqual(app.cfg["jellyfin_url"], "")
            self.assertFalse(app.cfg["watched_delete"])
            self.assertEqual(app.cfg["keep_n"], config.DEFAULTS["keep_n"])
            self.assertFalse(os.path.exists(app.cfg["_config_path"]))

    def test_a_form_encoded_post_is_refused(self):
        with harness.temp_app() as app:
            with running(app) as port:
                status, _, _ = call(port, "/api/channels", "POST",
                                    "urls=%40someone",
                                    "application/x-www-form-urlencoded")
            self.assertEqual(status, 415)
            self.assertEqual(app.events(), [])

    def test_a_post_with_no_content_type_is_refused(self):
        with harness.temp_app() as app:
            with running(app) as port:
                status, _, _ = call(port, "/api/ytdlp/update", "POST", "{}")
            self.assertEqual(status, 415)

    def test_a_text_plain_patch_is_refused(self):
        with harness.temp_app() as app:
            row_id = app.add_channel()
            with running(app) as port:
                status, _, _ = call(port, "/api/channels/%d" % row_id, "PATCH",
                                    json.dumps({"enabled": False}), "text/plain")
            self.assertEqual(status, 415)

    def test_json_with_a_charset_is_accepted(self):
        with harness.temp_app() as app:
            app.cfg["_config_path"] = os.path.join(app.tmp, "config.json")
            with running(app) as port:
                status, _, _ = call(port, "/api/settings", "POST",
                                    json.dumps({"keep_n": 7}),
                                    "application/json; charset=utf-8")
            self.assertEqual(status, 200)
            self.assertEqual(app.cfg["keep_n"], 7)

    def test_the_page_sends_json_on_every_post_and_patch(self):
        with harness.temp_app() as app:
            markup = web.render(app.cfg)
        for method in ("method:'POST'", "method:'PATCH'"):
            start = markup.index(method)
            self.assertIn("'Content-Type':'application/json'",
                          markup[start:start + 120])

    def test_a_refusal_closes_the_connection(self):
        # The body was never read, so the connection cannot be reused.
        with harness.temp_app() as app:
            with running(app) as port:
                _, headers, _ = call(port, "/api/settings", "POST", "{}",
                                     "text/plain")
            self.assertEqual(headers.get("Connection"), "close")


class PasswordTest(unittest.TestCase):

    def test_without_a_password_everything_is_open(self):
        with harness.temp_app() as app:
            with running(app) as port:
                self.assertEqual(call(port, "/")[0], 200)
                self.assertEqual(call(port, "/api/state")[0], 200)

    def test_with_a_password_the_page_asks_for_it(self):
        with harness.temp_app() as app:
            with running(app, "hunter2") as port:
                status, headers, _ = call(port, "/")
            self.assertEqual(status, 401)
            self.assertIn("Basic", headers.get("WWW-Authenticate", ""))

    def test_every_route_is_behind_it(self):
        with harness.temp_app() as app:
            row_id = app.add_channel()
            with running(app, "hunter2") as port:
                for method, path in (("GET", "/api/state"),
                                     ("POST", "/api/settings"),
                                     ("POST", "/api/channels"),
                                     ("PATCH", "/api/channels/%d" % row_id),
                                     ("DELETE", "/api/channels/%d" % row_id)):
                    status, _, _ = call(port, path, method,
                                        None if method == "DELETE" else "{}",
                                        "application/json")
                    self.assertEqual(status, 401, "%s %s" % (method, path))
            self.assertIsNotNone(harness.db.get_channel(app.conn, row_id))

    def test_a_wrong_password_is_refused(self):
        with harness.temp_app() as app:
            with running(app, "hunter2") as port:
                self.assertEqual(call(port, "/", auth="me:hunter3")[0], 401)
                self.assertEqual(call(port, "/", auth="hunter2")[0], 401)
                self.assertEqual(call(port, "/", auth="me:")[0], 401)

    def test_the_right_password_with_any_user_name_gets_in(self):
        with harness.temp_app() as app:
            app.cfg["_config_path"] = os.path.join(app.tmp, "config.json")
            with running(app, "hunter2") as port:
                self.assertEqual(call(port, "/", auth="me:hunter2")[0], 200)
                self.assertEqual(call(port, "/api/state", auth=":hunter2")[0], 200)
                status, _, _ = call(port, "/api/settings", "POST",
                                    json.dumps({"keep_n": 4}),
                                    "application/json", auth="x:hunter2")
            self.assertEqual(status, 200)
            self.assertEqual(app.cfg["keep_n"], 4)

    def test_a_password_with_a_colon_and_accents_works(self):
        with harness.temp_app() as app:
            with running(app, "pass:wörd") as port:
                self.assertEqual(call(port, "/", auth="me:pass:wörd")[0], 200)

    def test_a_garbled_header_is_refused_not_crashed_on(self):
        with harness.temp_app() as app:
            with running(app, "hunter2") as port:
                request = urllib.request.Request(
                    "http://127.0.0.1:%d/" % port,
                    headers={"Authorization": "Basic %%%not-base64"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, 401)

    def test_it_comes_from_the_environment_only(self):
        previous = os.environ.get("TUNER_PASSWORD")
        os.environ["TUNER_PASSWORD"] = "from-env"
        try:
            with harness.temp_app() as app:
                self.assertEqual(server.App(app.cfg, app.conn).password, "from-env")
                self.assertNotIn("password", app.cfg)
        finally:
            if previous is None:
                os.environ.pop("TUNER_PASSWORD", None)
            else:
                os.environ["TUNER_PASSWORD"] = previous


class HealthTest(unittest.TestCase):

    def test_healthz_answers_without_a_password(self):
        with harness.temp_app() as app:
            with running(app, "hunter2") as port:
                status, _, text = call(port, "/healthz")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(text), {"ok": True, "version": VERSION})

    def test_healthz_says_nothing_about_the_library(self):
        with harness.temp_app() as app:
            app.add_channel(title="Private Channel Name")
            with running(app) as port:
                text = call(port, "/healthz")[2]
            self.assertNotIn("Private Channel Name", text)
            self.assertNotIn(app.cfg["library_root"], text)


if __name__ == "__main__":
    unittest.main()
