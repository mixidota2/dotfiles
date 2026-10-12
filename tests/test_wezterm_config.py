"""Source-level WezTerm regressions; these do not execute WezTerm or Lua."""

from pathlib import Path
import re
import unittest


REPO = Path(__file__).resolve().parents[1]


class WezTermConfigTests(unittest.TestCase):
    def setUp(self):
        self.source = (REPO / "wezterm/.config/wezterm/wezterm.lua").read_text()

    def test_no_nightly_only_close_tab_option(self):
        # Stable 20240203-110809-5046fc22 rejects this field. Keep the
        # shared config usable without requiring the Brewfile's nightly.
        self.assertNotIn("show_close_tab_button_in_tabs", self.source)

    def test_command_shift_arrows_keep_app_tab_navigation(self):
        # These simple blocks intentionally have no nested Lua tables.
        bindings = re.findall(
            r"\{\s*key\s*=\s*'([^']+)'\s*,\s*"
            r"mods\s*=\s*'([^']+)'\s*,\s*"
            r"action\s*=\s*wezterm\.action\.ActivateTabRelative\((-?\d+)\)\s*,?\s*\}",
            self.source,
        )
        for key, direction in (("LeftArrow", "-1"), ("RightArrow", "1")):
            with self.subTest(key=key):
                self.assertEqual(bindings.count((key, "CMD|SHIFT", direction)), 1)

    def test_shift_enter_keeps_newline_binding(self):
        self.assertRegex(
            self.source,
            r"key\s*=\s*'Enter'\s*,\s*mods\s*=\s*'SHIFT'\s*,\s*"
            r"action\s*=\s*wezterm\.action\.SendString\('\\n'\)",
        )


if __name__ == "__main__":
    unittest.main()
