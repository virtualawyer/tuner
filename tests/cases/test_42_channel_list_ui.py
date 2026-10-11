"""The channel list: compact rows, sorting, filtering.

One card per channel is fine at four channels and unusable at forty, so the
channels render as rows in a single panel that open in place. These tests
pin the structure and the two layout bugs found while building it.
"""

import re
import unittest

import harness
from tuner import web


def page():
    with harness.temp_app() as app:
        return web.render(app.cfg)


class ToolbarTest(unittest.TestCase):

    def setUp(self):
        self.markup = page()

    def test_filter_sort_and_direction_all_exist(self):
        for element_id in ("channel-filter", "channel-sort",
                           "channel-sortdir", "channels-toolbar"):
            self.assertIn('id="%s"' % element_id, self.markup)

    def test_every_sort_key_is_offered_and_implemented(self):
        script = self.markup.split("<script>")[1]
        offered = set(re.findall(r'<option value="([a-z]+)">', self.markup))
        for key in ("name", "checked", "videos", "size", "attention"):
            self.assertIn(key, offered)
            self.assertIn(key + ":function", script.replace(" ", ""))

    def test_each_sort_key_has_a_default_direction_and_wording(self):
        script = self.markup.split("<script>")[1]
        directions = script.split("SORT_DEFAULT_DIR=")[1].split("};")[0]
        words = script.split("SORT_WORDS=")[1].split("};")[0]
        for key in ("name", "checked", "videos", "size", "attention"):
            self.assertIn(key, directions)
            self.assertIn(key + ":", words)

    def test_the_direction_button_says_what_it_did(self):
        # An arrow on its own is ambiguous, so the button carries wording.
        script = self.markup.split("<script>")[1]
        self.assertIn("Largest first", script)
        self.assertIn("A to Z", script)
        self.assertIn("aria-label", script)

    def test_the_choice_is_remembered(self):
        script = self.markup.split("<script>")[1]
        self.assertIn("localStorage", script)
        self.assertIn("tuner.channels", script)
        # And a browser that refuses localStorage must not break the page.
        self.assertIn("catch(e){}", script.replace(" ", ""))


class ListStructureTest(unittest.TestCase):

    def setUp(self):
        self.markup = page()
        self.style = self.markup.split("<style>")[1].split("</style>")[0]

    def test_the_channels_render_into_one_panel(self):
        self.assertIn('class="list hidden" id="channels"', self.markup)
        self.assertIn(".list{", self.style)
        self.assertIn(".lrow{", self.style)
        self.assertIn(".lhead{", self.style)

    def test_a_row_is_a_real_button_for_keyboard_and_screen_readers(self):
        script = self.markup.split("<script>")[1]
        self.assertIn("createElement('button')", script)
        self.assertIn("aria-expanded", script)
        self.assertIn("aria-label", script)

    def test_there_is_an_empty_state_for_a_filter_that_matches_nothing(self):
        self.assertIn('id="channels-none"', self.markup)
        self.assertIn('id="channels-empty"', self.markup)

    def test_the_toolbar_hides_itself_when_there_is_nothing_to_sort(self):
        script = self.markup.split("<script>")[1]
        self.assertIn("show(el('channels-toolbar'),all.length>1)",
                      script.replace(" ", ""))


class LayoutBugTest(unittest.TestCase):
    """Two bugs found by looking at the rendered page, not by reading it."""

    def setUp(self):
        self.markup = page()
        self.style = self.markup.split("<style>")[1].split("</style>")[0]

    def test_the_flex_modifier_does_not_collide_with_the_page_container(self):
        # `.row.wrap` also matches `.wrap`, the page container, so every
        # wrapped row inherited its 60px bottom padding.
        self.assertNotIn(".row.wrap{", self.style)
        self.assertIn(".row.wrapped{", self.style)
        self.assertNotIn('class="row wrap"', self.markup)

        script = self.markup.split("<script>")[1]
        self.assertNotIn("'row wrap'", script)

    def test_the_channel_name_cannot_shove_the_caret_onto_its_own_line(self):
        # flex-basis:auto lets the name claim max-content width when the
        # browser decides where to wrap, which breaks long channel names.
        rule = re.search(r"\.lname\{[^}]*\}", self.style).group(0)
        self.assertIn("flex:1 1 0", rule)
        self.assertIn("min-width:0", rule)


class RedrawGuardTest(unittest.TestCase):

    def setUp(self):
        self.script = page().split("<script>")[1]

    def test_only_form_controls_block_a_redraw(self):
        # Treating a just-clicked button as "being edited" made the panel
        # it sits in refuse to re-render, so rows would not open.
        guard = self.script.split("function editing(host){")[1].split("\n}")[0]
        for tag in ("INPUT", "SELECT", "TEXTAREA"):
            self.assertIn(tag, guard)
        self.assertNotIn("BUTTON", guard)

    def test_every_polled_panel_uses_the_same_guard(self):
        # channels, recent, settings
        self.assertEqual(self.script.count("if(editing("), 3)

    def test_the_toggled_row_keeps_keyboard_focus(self):
        self.assertIn("data-ch", self.script)
        self.assertIn(".focus()", self.script)


if __name__ == "__main__":
    unittest.main()
