"""Task 7.1 -- the Jellyfin client and connection test.

Runs against a real http.server stub started inside the test, so the code
path exercised is the same urllib one that runs on the box.
"""

import contextlib
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import harness
from tuner import jellyfin, server


class StubJellyfin(object):
    """A tiny Jellyfin, scripted per path."""

    def __init__(self, routes, status=200):
        self.routes = routes
        self.status = status
        self.seen = []
        self.httpd = None

    def __enter__(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def respond(self):
                path = self.path.split("?", 1)[0]
                stub.seen.append({
                    "path": self.path,
                    "method": self.command,
                    "token": self.headers.get("X-Emby-Token"),
                })

                if stub.status != 200:
                    self.send_response(stub.status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if path not in stub.routes:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                body = json.dumps(stub.routes[path]).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = respond
            do_POST = respond

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


SYSTEM_INFO = {"ServerName": "basement", "Version": "10.10.3",
               "Id": "abc123"}
USERS = [{"Id": "user-1", "Name": "Alex"},
         {"Id": "user-2", "Name": "Guest"}]


class ConnectionTest(unittest.TestCase):

    def test_a_good_server_reports_its_name_and_version(self):
        with StubJellyfin({"/System/Info": SYSTEM_INFO}) as stub:
            with harness.temp_app(jellyfin_url=stub.url,
                                  jellyfin_api_key="secret-key-1234") as app:
                info = jellyfin.system_info(app.cfg)

        self.assertTrue(info["ok"])
        self.assertEqual(info["server_name"], "basement")
        self.assertEqual(info["version"], "10.10.3")
        self.assertIsNone(info["error"])

    def test_the_api_key_travels_in_the_header(self):
        with StubJellyfin({"/System/Info": SYSTEM_INFO}) as stub:
            with harness.temp_app(jellyfin_url=stub.url,
                                  jellyfin_api_key="secret-key-1234") as app:
                jellyfin.system_info(app.cfg)

        self.assertEqual(stub.seen[0]["token"], "secret-key-1234")

    def test_a_trailing_slash_in_the_url_is_tolerated(self):
        with StubJellyfin({"/System/Info": SYSTEM_INFO}) as stub:
            with harness.temp_app(jellyfin_url=stub.url + "/",
                                  jellyfin_api_key="k") as app:
                self.assertTrue(jellyfin.system_info(app.cfg)["ok"])
        self.assertEqual(stub.seen[0]["path"], "/System/Info")

    def test_a_rejected_key_reads_clearly(self):
        with StubJellyfin({}, status=401) as stub:
            with harness.temp_app(jellyfin_url=stub.url,
                                  jellyfin_api_key="wrong") as app:
                info = jellyfin.system_info(app.cfg)

        self.assertFalse(info["ok"])
        self.assertIn("rejected the API key", info["error"])

    def test_a_server_error_reads_clearly(self):
        with StubJellyfin({}, status=500) as stub:
            with harness.temp_app(jellyfin_url=stub.url,
                                  jellyfin_api_key="k") as app:
                info = jellyfin.system_info(app.cfg)

        self.assertFalse(info["ok"])
        self.assertIn("500", info["error"])

    def test_a_refused_connection_does_not_raise(self):
        # Port 1 is not listening. This is the everyday case of Jellyfin
        # being restarted while Tuner polls.
        with harness.temp_app(jellyfin_url="http://127.0.0.1:1",
                              jellyfin_api_key="k") as app:
            info = jellyfin.system_info(app.cfg)

        self.assertFalse(info["ok"])
        self.assertIn("cannot reach Jellyfin", info["error"])

    def test_an_unconfigured_client_says_so_without_a_request(self):
        with harness.temp_app() as app:
            info = jellyfin.system_info(app.cfg)
        self.assertFalse(info["ok"])
        self.assertIn("URL and API key", info["error"])

    def test_configured_needs_both_url_and_key(self):
        with harness.temp_app(jellyfin_url="http://x", jellyfin_api_key="") as app:
            self.assertFalse(jellyfin.configured(app.cfg))
        with harness.temp_app(jellyfin_url="", jellyfin_api_key="k") as app:
            self.assertFalse(jellyfin.configured(app.cfg))
        with harness.temp_app(jellyfin_url="http://x", jellyfin_api_key="k") as app:
            self.assertTrue(jellyfin.configured(app.cfg))


class UsersTest(unittest.TestCase):

    def test_users_are_listed_for_the_picker(self):
        with StubJellyfin({"/Users": USERS}) as stub:
            with harness.temp_app(jellyfin_url=stub.url,
                                  jellyfin_api_key="k") as app:
                found = jellyfin.users(app.cfg)

        self.assertEqual(found, [{"id": "user-1", "name": "Alex"},
                                 {"id": "user-2", "name": "Guest"}])

    def test_a_failure_yields_an_empty_list(self):
        with StubJellyfin({}, status=500) as stub:
            with harness.temp_app(jellyfin_url=stub.url,
                                  jellyfin_api_key="k") as app:
                self.assertEqual(jellyfin.users(app.cfg), [])

    def test_unconfigured_yields_an_empty_list_without_a_request(self):
        with harness.temp_app() as app:
            self.assertEqual(jellyfin.users(app.cfg), [])


class MaskTest(unittest.TestCase):

    def test_only_the_last_four_survive(self):
        self.assertEqual(jellyfin.mask_key("abcdef123456"), "********3456")
        self.assertEqual(jellyfin.mask_key("abcd"), "****")
        self.assertEqual(jellyfin.mask_key(""), "")
        self.assertEqual(jellyfin.mask_key(None), "")

    def test_the_state_payload_never_carries_the_real_key(self):
        secret = "supersecretkey9999"
        with harness.temp_app(jellyfin_url="http://x", jellyfin_api_key=secret) as app:
            state = server.build_state(server.App(app.cfg, app.conn))
            body = json.dumps(state)

        self.assertNotIn(secret, body)
        self.assertEqual(state["jellyfin"]["api_key"], "**************9999")
        self.assertTrue(state["jellyfin"]["configured"])


class TestEndpointTest(unittest.TestCase):

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
        import urllib.error
        import urllib.request
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path), data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_the_endpoint_reports_success_and_the_user_list(self):
        with StubJellyfin({"/System/Info": SYSTEM_INFO, "/Users": USERS}) as stub:
            with harness.temp_app(jellyfin_url=stub.url,
                                  jellyfin_api_key="k") as app:
                with self.running(app) as port:
                    status, body = self.post(port, "/api/jellyfin/test")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["server_name"], "basement")
        self.assertEqual(len(body["users"]), 2)

    def test_the_endpoint_reports_failure_without_users(self):
        with harness.temp_app(jellyfin_url="http://127.0.0.1:1",
                              jellyfin_api_key="k") as app:
            with self.running(app) as port:
                status, body = self.post(port, "/api/jellyfin/test")

        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertEqual(body["users"], [])
        self.assertIn("cannot reach", body["error"])


if __name__ == "__main__":
    unittest.main()
