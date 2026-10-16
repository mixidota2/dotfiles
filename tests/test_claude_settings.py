"""Merging managed keys into Claude Code's user settings never touches the real HOME."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "claude/merge_settings.py"
ADDITIONS = REPO / "claude/settings-additions.json"


class MergeClaudeSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="claude-settings-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.target = self.root / "settings.json"
        self.additions = self.root / "additions.json"
        self.additions.write_text(json.dumps(
            {"pluginConfigs": {"a@builtin": {"options": {"mode": "new"}}}}
        ))

    def merge(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.additions), str(self.target), *extra],
            text=True, capture_output=True,
        )

    def write_target(self, value):
        self.target.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")

    def test_sets_managed_keys_and_keeps_everything_else(self):
        self.write_target({
            "hooks": {"Stop": [{"command": "orca hook"}]},
            "language": "日本語",
            "pluginConfigs": {
                "other@builtin": {"options": {"x": 1}},
                "a@builtin": {"options": {"mode": "old", "keep": True}},
            },
        })
        self.assertEqual(self.merge().returncode, 0)
        self.assertEqual(json.loads(self.target.read_text()), {
            "hooks": {"Stop": [{"command": "orca hook"}]},
            "language": "日本語",
            "pluginConfigs": {
                "other@builtin": {"options": {"x": 1}},
                "a@builtin": {"options": {"mode": "new", "keep": True}},
            },
        })

    def test_up_to_date_file_is_not_rewritten(self):
        self.write_target({"pluginConfigs": {"a@builtin": {"options": {"mode": "new"}}}})
        before = self.target.stat().st_mtime_ns
        result = self.merge()
        self.assertEqual(result.returncode, 0)
        self.assertIn("up to date", result.stdout)
        self.assertEqual(self.target.stat().st_mtime_ns, before)

    def test_unrelated_lines_keep_their_format(self):
        self.write_target({"language": "日本語", "theme": "dark"})
        self.merge()
        text = self.target.read_text()
        self.assertIn('"language": "日本語",\n  "theme": "dark"', text)
        self.assertTrue(text.endswith("}\n"))

    def test_missing_file_is_created(self):
        self.assertEqual(self.merge().returncode, 0)
        self.assertEqual(json.loads(self.target.read_text())["pluginConfigs"]["a@builtin"],
                         {"options": {"mode": "new"}})

    def test_check_reports_without_writing(self):
        result = self.merge("--check")
        self.assertEqual(result.returncode, 0)
        self.assertIn("would be updated", result.stdout)
        self.assertFalse(self.target.exists())

    def test_unreadable_file_is_left_as_is(self):
        self.target.write_text("not json\n")
        result = self.merge()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.target.read_text(), "not json\n")

    def test_symlinked_settings_file_stays_a_link(self):
        real = self.root / "real-settings.json"
        real.write_text("{}\n")
        self.target.symlink_to(real)
        self.assertEqual(self.merge().returncode, 0)
        self.assertTrue(self.target.is_symlink())
        self.assertIn("a@builtin", real.read_text())

    def test_repository_additions_read_agents_md_with_claude_md(self):
        additions = json.loads(ADDITIONS.read_text())
        options = additions["pluginConfigs"]["cc-plugin-agents-md@builtin"]["options"]
        self.assertEqual(options, {"instructionFiles": "claude-md-and-agents-md"})


if __name__ == "__main__":
    unittest.main()
