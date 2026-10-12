"""External skill installer checks use a stub npx and an isolated HOME."""

from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class SkillsInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dotfiles-skills-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.args_file = self.root / "npx-args"
        self.npx = self.bin / "npx"
        self.npx.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" > \"$NPX_ARGS_FILE\"\n"
            'exit "${NPX_EXIT_CODE:-0}"\n'
        )
        self.npx.chmod(0o755)

    def install(self, exit_code=0):
        return subprocess.run(
            ["/bin/sh", str(REPO / "skills/install.sh")],
            env={
                "HOME": str(self.home),
                "PATH": str(self.bin) + ":/usr/bin:/bin:/usr/sbin:/sbin",
                "DOTFILES_BACKUP_DIR": str(self.root / "backups"),
                "NPX_ARGS_FILE": str(self.args_file),
                "NPX_EXIT_CODE": str(exit_code),
            },
            text=True, capture_output=True,
        )

    def test_installs_tk_from_standalone_repository(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.args_file.read_text().splitlines(), [
            "skills", "add", "mixidota2/tasukura-skills",
            "--global", "--agent", "codex", "claude-code", "--copy", "--yes", "--skill", "tk",
        ])
        self.assertIn("tk skill installed for Codex and Claude Code.", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_failed_install_does_not_report_success(self):
        result = self.install(exit_code=17)
        self.assertEqual(result.returncode, 17)
        self.assertNotIn("tk skill installed", result.stdout)

    def test_existing_claude_skill_is_preserved_in_unique_backup(self):
        target = self.home / ".claude/skills/tk"
        target.mkdir(parents=True)
        (target / "custom.txt").write_text("local customization")
        backup = self.root / "backups/.claude/skills/tk"
        backup.mkdir(parents=True)
        (backup / "old.txt").write_text("previous backup")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((backup / "old.txt").read_text(), "previous backup")
        self.assertEqual((backup.with_name("tk.1") / "custom.txt").read_text(), "local customization")

    def test_relative_symlink_backup_retains_original_referent(self):
        target = self.home / ".claude/skills/tk"
        target.parent.mkdir(parents=True)
        source = self.home / ".claude/original-tk"
        source.mkdir()
        (source / "SKILL.md").write_text("original")
        target.symlink_to("../original-tk")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = self.root / "backups/.claude/skills/tk"
        self.assertTrue(backup.is_symlink())
        self.assertEqual((backup / "SKILL.md").read_text(), "original")
        self.assertEqual((source / "SKILL.md").read_text(), "original")

    def test_failed_install_restores_customizations(self):
        target = self.home / ".claude/skills/tk"
        target.mkdir(parents=True)
        (target / "custom.txt").write_text("preserved")
        result = self.install(exit_code=17)
        self.assertEqual(result.returncode, 17)
        self.assertIn("restored", result.stderr)
        self.assertEqual((target / "custom.txt").read_text(), "preserved")

    def test_failure_preserves_partial_replacement_and_occupied_backup(self):
        target = self.home / ".claude/skills/tk"
        target.mkdir(parents=True)
        (target / "custom.txt").write_text("original")
        failed = self.root / "backups/.claude/skills/tk.failed-install"
        failed.mkdir(parents=True)
        (failed / "old.txt").write_text("older failure")
        self.npx.write_text(
            '#!/bin/sh\nmkdir -p "$HOME/.claude/skills/tk"\n'
            'printf partial > "$HOME/.claude/skills/tk/partial.txt"\nexit 19\n'
        )
        result = self.install()
        self.assertEqual(result.returncode, 19)
        self.assertEqual((target / "custom.txt").read_text(), "original")
        self.assertEqual((failed / "old.txt").read_text(), "older failure")
        self.assertEqual((failed.with_name("tk.failed-install.1") / "partial.txt").read_text(), "partial")
        self.assertIn("restored", result.stderr)
        self.assertNotIn("tk skill installed", result.stdout)

    def test_whole_skills_symlink_stops_before_npx_or_source_changes(self):
        source = self.root / "shared skills"
        source.mkdir()
        (source / "SKILL.md").write_text("unchanged")
        skills = self.home / ".claude/skills"
        skills.parent.mkdir()
        skills.symlink_to(source)
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skills root is a symlink", result.stderr)
        self.assertFalse(self.args_file.exists())
        self.assertEqual((source / "SKILL.md").read_text(), "unchanged")
        self.assertTrue(skills.is_symlink())

    def test_missing_npx_fails_before_install(self):
        self.npx.unlink()
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("npx is required", result.stderr)
        self.assertFalse(self.args_file.exists())
        self.assertNotIn("tk skill installed", result.stdout)


if __name__ == "__main__":
    unittest.main()

