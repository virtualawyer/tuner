"""Task 7.2 -- reading played status back from Jellyfin."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import harness
from tuner import jellyfin

VIDEO_A = "dQw4w9WgXcQ"
VIDEO_B = "aBcDeFgHiJk"


def item(name, path, played_at="2026-08-11T12:00:00.000Z"):
    return {"Id": "jf-" + name, "Name": name, "Path": path,
            "UserData": {"Played": True, "LastPlayedDate": played_at}}


class StubItems(object):
    """Serves /Items (and optionally only the legacy per-user route)."""

    def __init__(self, pages, modern=True):
        self.pages = pages          # list of item lists, in page order
        self.modern = modern
        self.seen = []

    def __enter__(self):
        stub = self
        total = sum(len(page) for page in stub.pages)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                stub.seen.append({"path": parsed.path, "query": query})

                legacy = parsed.path.startswith("/Users/")
                if (not stub.modern and not legacy) or \
                        (parsed.path not in ("/Items",) and not legacy):
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                start = int((query.get("StartIndex") or ["0"])[0])
                offset, chosen = 0, []
                for page in stub.pages:
                    if offset == start:
                        chosen = page
                        break
                    offset += len(page)

                body = json.dumps({"Items": chosen,
                                   "TotalRecordCount": total}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def configured(stub_url, **extra):
    settings = {"jellyfin_url": stub_url, "jellyfin_api_key": "k",
                "jellyfin_user_id": "user-1"}
    settings.update(extra)
    return harness.temp_app(**settings)


class ExtractTest(unittest.TestCase):

    def test_the_id_is_read_from_a_container_path(self):
        # Jellyfin sees /media/... where Tuner wrote /srv/media/..., which is
        # exactly why matching is done on the id token and not the path.
        found = jellyfin.extract_video_id(
            {"Path": "/media/youtube/Alpha/2026-08-11 - A video [%s].mp4" % VIDEO_A,
             "Name": "2026-08-11 - A video"})
        self.assertEqual(found, VIDEO_A)

    def test_the_name_is_used_when_there_is_no_path(self):
        self.assertEqual(
            jellyfin.extract_video_id({"Name": "Something [%s]" % VIDEO_B}),
            VIDEO_B)

    def test_unrelated_media_yields_nothing(self):
        for candidate in ({"Path": "/media/movies/Arrival (2016).mkv",
                           "Name": "Arrival"},
                          {"Path": "/media/tv/Show/S01E01.mkv"},
                          {"Name": "[too-short]"},
                          {}):
            self.assertIsNone(jellyfin.extract_video_id(candidate))


class PlayedTest(unittest.TestCase):

    def test_played_ids_come_back_with_their_timestamps(self):
        page = [
            item("A", "/media/youtube/Alpha/2026-08-11 - A [%s].mp4" % VIDEO_A),
            item("B", "/media/youtube/Alpha/2026-08-12 - B [%s].mp4" % VIDEO_B,
                 played_at="2026-08-12T09:30:00.000Z"),
        ]
        with StubItems([page]) as stub:
            with configured(stub.url) as app:
                found = jellyfin.played_video_ids(app.cfg)

        self.assertEqual(set(found), {VIDEO_A, VIDEO_B})
        self.assertEqual(found[VIDEO_B], "2026-08-12T09:30:00.000Z")

    def test_the_query_asks_only_for_played_items(self):
        with StubItems([[]]) as stub:
            with configured(stub.url) as app:
                jellyfin.played_video_ids(app.cfg)

        query = stub.seen[0]["query"]
        self.assertEqual(query["IsPlayed"], ["true"])
        self.assertEqual(query["Recursive"], ["true"])
        self.assertEqual(query["Fields"], ["Path"])
        self.assertEqual(query["userId"], ["user-1"])

    def test_unrelated_library_items_are_ignored(self):
        page = [
            item("A", "/media/youtube/Alpha/2026-08-11 - A [%s].mp4" % VIDEO_A),
            item("Arrival", "/media/movies/Arrival (2016).mkv"),
            item("Episode", "/media/tv/Show/S01E01.mkv"),
        ]
        with StubItems([page]) as stub:
            with configured(stub.url) as app:
                found = jellyfin.played_video_ids(app.cfg)

        self.assertEqual(list(found), [VIDEO_A])

    def test_paging_assembles_every_page(self):
        first = [item("A%d" % n,
                      "/media/youtube/A/%d [%s].mp4" % (n, "vid%08d" % n))
                 for n in range(jellyfin.PAGE_SIZE)]
        second = [item("B", "/media/youtube/A/last [%s].mp4" % VIDEO_B)]

        with StubItems([first, second]) as stub:
            with configured(stub.url) as app:
                found = jellyfin.played_video_ids(app.cfg)

        self.assertEqual(len(found), jellyfin.PAGE_SIZE + 1)
        self.assertIn(VIDEO_B, found)
        self.assertEqual(len(stub.seen), 2)
        self.assertEqual(stub.seen[1]["query"]["StartIndex"],
                         [str(jellyfin.PAGE_SIZE)])

    def test_the_legacy_per_user_route_is_the_fallback(self):
        page = [item("A", "/media/youtube/A/x [%s].mp4" % VIDEO_A)]
        with StubItems([page], modern=False) as stub:
            with configured(stub.url) as app:
                found = jellyfin.played_video_ids(app.cfg)

        self.assertEqual(list(found), [VIDEO_A])
        self.assertEqual(stub.seen[0]["path"], "/Items")
        self.assertEqual(stub.seen[1]["path"], "/Users/user-1/Items")

    def test_nothing_played_is_an_empty_map(self):
        with StubItems([[]]) as stub:
            with configured(stub.url) as app:
                self.assertEqual(jellyfin.played_video_ids(app.cfg), {})

    def test_an_unreachable_server_yields_an_empty_map(self):
        with configured("http://127.0.0.1:1") as app:
            self.assertEqual(jellyfin.played_video_ids(app.cfg), {})

    def test_no_user_id_means_no_request(self):
        with StubItems([[]]) as stub:
            with configured(stub.url, jellyfin_user_id="") as app:
                self.assertEqual(jellyfin.played_video_ids(app.cfg), {})
        self.assertEqual(stub.seen, [])

    def test_unconfigured_means_no_request(self):
        with harness.temp_app() as app:
            self.assertEqual(jellyfin.played_video_ids(app.cfg), {})


if __name__ == "__main__":
    unittest.main()
