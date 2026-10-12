"""Claude setup tests never install tools, authenticate, or use the real HOME."""

import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

import yaml

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / "skills-local/.codex/skills"
NAMES = {
    "create-validated-skill", "design-python-code", "eli5", "herdr",
    "pi-extension-development", "sanitize-artifacts",
}


class ClaudeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="claude-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.backups = self.root / "backups"
        self.skills = self.home / ".claude/skills"

    def install(self, *args):
        return subprocess.run(
            ["/bin/sh", str(REPO / "install.sh"), *args],
            env={"HOME": str(self.home), "DOTFILES_BACKUP_DIR": str(self.backups),
                 "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            text=True, capture_output=True, check=True,
        ).stdout

    def test_all_six_skills_have_claude_compatible_metadata(self):
        actual = {p.parent.name for p in LOCAL.glob("*/SKILL.md")}
        self.assertEqual(actual, NAMES)
        for name in NAMES:
            text = (LOCAL / name / "SKILL.md").read_text()
            frontmatter = yaml.safe_load(text.split("---", 2)[1])
            self.assertEqual(frontmatter["name"], name)
            self.assertIsInstance(frontmatter["description"], str)
            self.assertTrue(frontmatter["description"])
            self.assertLessEqual(len(frontmatter["description"]), 1024)

    def test_links_all_skills_and_preserves_settings_and_user_additions(self):
        self.skills.mkdir(parents=True)
        sentinels = [self.skills / "custom/SKILL.md", self.skills / "synced/state.json",
                     self.home / ".claude/settings.json", self.home / ".claude/.credentials.json",
                     self.home / ".claude/CLAUDE.md"]
        for path in sentinels:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("local sentinel\n")
        self.install()
        for name in NAMES:
            target = self.skills / name
            self.assertTrue(target.is_symlink())
            self.assertEqual(os.readlink(target), str(LOCAL / name))
            self.assertEqual((target / "SKILL.md").read_bytes(), (LOCAL / name / "SKILL.md").read_bytes())
        for path in sentinels:
            self.assertEqual(path.read_text(), "local sentinel\n")
        self.assertFalse(self.skills.is_symlink())
        self.assertFalse(self.backups.exists())
        repeated = self.install()
        self.assertNotIn("backup  ", repeated)
        self.assertNotIn("link    ", repeated)

    def test_collision_is_backed_up_with_custom_contents(self):
        target = self.skills / "eli5"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("custom skill\n")
        (target / "private.txt").write_text("custom addition\n")
        self.install()
        saved = self.backups / ".claude/skills/eli5"
        self.assertEqual((saved / "private.txt").read_text(), "custom addition\n")
        self.assertEqual((saved / "SKILL.md").read_text(), "custom skill\n")
        self.assertTrue(target.is_symlink())

    def test_broken_collision_is_preserved_and_dry_run_is_read_only(self):
        self.skills.mkdir(parents=True)
        target = self.skills / "eli5"
        target.symlink_to(self.root / "missing")
        before = sorted(str(p) for p in self.home.rglob("*"))
        output = self.install("--dry-run")
        self.assertIn(str(self.skills / "eli5"), output)
        self.assertEqual(before, sorted(str(p) for p in self.home.rglob("*")))
        self.assertFalse(self.backups.exists())
        self.install()
        self.assertEqual(os.readlink(self.backups / ".claude/skills/eli5"), str(self.root / "missing"))

    def test_relative_symlink_backup_remains_readable(self):
        self.skills.mkdir(parents=True)
        original = self.home / ".claude/custom-eli5"
        original.mkdir()
        (original / "SKILL.md").write_text("custom skill")
        (self.skills / "eli5").symlink_to("../custom-eli5")
        self.install()
        saved = self.backups / ".claude/skills/eli5"
        self.assertEqual((saved / "SKILL.md").read_text(), "custom skill")
        self.assertEqual((original / "SKILL.md").read_text(), "custom skill")

    def test_whole_skills_symlink_fails_before_mutating_checkout_or_home(self):
        fixture = self.root / "checkout"
        shutil.copytree(REPO, fixture, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        source = fixture / "skills-local/.codex/skills"
        self.skills.parent.mkdir()
        self.skills.symlink_to(source)
        before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        for args in ([], ["--dry-run"]):
            result = subprocess.run(
                ["/bin/sh", str(fixture / "install.sh"), *args],
                env={"HOME": str(self.home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("skills root is a symlink", result.stderr)
            after = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertFalse(any(p.is_symlink() for p in source.rglob("*")))
            self.assertEqual(list(self.home.iterdir()), [self.skills.parent])

    def test_parent_link_to_same_source_is_never_replaced(self):
        fixture = self.root / "checkout"
        shutil.copytree(REPO, fixture, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        source = fixture / "skills-local/.codex/skills"
        codex = self.home / ".codex"
        codex.mkdir()
        (codex / "skills").symlink_to(source)
        before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        result = subprocess.run(
            ["/bin/sh", str(fixture / "install.sh")],
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(any(p.is_symlink() for p in source.rglob("*")))

    def test_brewfile_provides_node_for_external_skill_installer(self):
        lines = (REPO / "Brewfile").read_text().splitlines()
        self.assertEqual(lines.count('brew "node"'), 1)
        self.assertIn("npx skills add", (REPO / "skills/install.sh").read_text())

    def test_brewfile_contains_official_claude_code_cask_once(self):
        lines = (REPO / "Brewfile").read_text().splitlines()
        self.assertEqual(lines.count('cask "claude-code"'), 1)


if __name__ == "__main__":
    unittest.main()
