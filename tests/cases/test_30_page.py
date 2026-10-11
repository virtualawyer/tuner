"""Task 8.1 -- the page shell, header, and disk bar."""

import re
import unittest

import harness
from tuner import server, web


class TokenTest(unittest.TestCase):

    def setUp(self):
        with harness.temp_app() as app:
            self.markup = web.render(app.cfg)
        self.style = self.markup.split("<style>")[1].split("</style>")[0]

    def test_every_design_token_is_defined(self):
        for token in ("--bg", "--card", "--border", "--text", "--muted",
                      "--accent", "--track", "--green", "--greenbg", "--red",
                      "--redbg", "--amber", "--amberbg", "--shadow"):
            self.assertIn(token + ":", self.style)

    def test_light_and_dark_are_both_defined(self):
        self.assertIn("prefers-color-scheme:dark", self.style)
        self.assertEqual(self.style.count(":root{"), 2)

    def test_no_hex_color_outside_the_root_blocks(self):
        without_root = re.sub(r":root\{[^}]*\}", "", self.style)
        leftover = re.findall(r"#[0-9a-fA-F]{3,8}\b", without_root)
        self.assertEqual(leftover, [])

    def test_the_page_loads_nothing_from_outside(self):
        # Example URLs in placeholder text are fine; what must not exist is
        # anything that actually fetches from another host.
        for construct in ("<link", "<script src", "@import", "url(http",
                          'src="http', "src='http", 'href="http',
                          "href='http", "jsdelivr", "unpkg", "//cdn"):
            self.assertNotIn(construct, self.markup)

        script = self.markup.split("<script>")[1]
        for absolute in ("fetch('http", 'fetch("http'):
            self.assertNotIn(absolute, script)

    def test_no_placeholder_survives(self):
        for placeholder in ("__BRAND__", "__STYLE__", "__SCRIPT__",
                            "__LIBRARY__", "__SECTIONS__", "{{"):
            self.assertNotIn(placeholder, self.markup)

    def test_the_brand_and_library_are_injected(self):
        with harness.temp_app(title="Den Tuner") as app:
            markup = web.render(app.cfg)
            self.assertIn("Den Tuner", markup)
            self.assertIn(app.cfg["library_root"], markup)

    def test_the_viewport_is_mobile_ready(self):
        self.assertIn('name="viewport"', self.markup)
        self.assertIn("width=device-width", self.markup)
        self.assertIn("max-width:560px", self.style)


class HeaderTest(unittest.TestCase):

    def setUp(self):
        with harness.temp_app() as app:
            self.markup = web.render(app.cfg)

    def test_every_chip_the_script_fills_exists_in_the_markup(self):
        for element_id in ("c-channels", "c-downloaded", "c-queued",
                           "c-ytdlp", "c-free", "dot", "diskbar", "disksub",
                           "libroot", "version"):
            self.assertIn('id="%s"' % element_id, self.markup)

    def test_the_disk_bar_uses_the_track_and_accent_tokens(self):
        style = self.markup.split("<style>")[1].split("</style>")[0]
        self.assertIn("background:var(--track)", style)
        self.assertIn("background:var(--accent)", style)
        self.assertIn(".bar.low span{background:var(--red)}", style)


class StatePayloadTest(unittest.TestCase):
    """Whatever the header renders has to exist in the payload."""

    def test_state_carries_the_ytdlp_version(self):
        with harness.temp_app() as app:
            app.scenario([harness.rule_version("2026.08.01")])
            server._VERSION_CACHE["checked_at"] = 0.0
            state = server.build_state(server.App(app.cfg, app.conn))
            self.assertEqual(state["ytdlp_version"], "2026.08.01")

    def test_a_missing_binary_reports_none(self):
        with harness.temp_app(ytdlp_path="/nonexistent/yt-dlp") as app:
            server._VERSION_CACHE["checked_at"] = 0.0
            state = server.build_state(server.App(app.cfg, app.conn))
            self.assertIsNone(state["ytdlp_version"])

    def test_the_version_lookup_is_cached(self):
        # Polling every 2 seconds must not spawn a process every 2 seconds.
        with harness.temp_app() as app:
            app.scenario([harness.rule_version("2026.08.01")])
            server._VERSION_CACHE["checked_at"] = 0.0
            server.ytdlp_version(app.cfg)
            first = len(app.calls())
            for _ in range(5):
                server.ytdlp_version(app.cfg)
            self.assertEqual(len(app.calls()), first)

    def test_the_header_fields_are_all_present(self):
        with harness.temp_app() as app:
            state = server.build_state(server.App(app.cfg, app.conn))
            for key in ("version", "title", "ytdlp_version", "disk", "library",
                        "counts"):
                self.assertIn(key, state)
            for key in ("ok", "low", "percent", "free", "used", "total",
                        "min_free_gb"):
                self.assertIn(key, state["disk"])
            for key in ("root", "bytes", "videos", "per_channel"):
                self.assertIn(key, state["library"])


if __name__ == "__main__":
    unittest.main()
