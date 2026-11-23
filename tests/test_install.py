"""Installer regressions. Every invocation uses an isolated temporary HOME."""

import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
ORCA = Path(".orca/keybindings.json")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dotfiles-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.backups = self.root / "backups with spaces"
        self.target = self.home / ORCA
        self.target.parent.mkdir()
        self.backup = self.backups / ORCA

    def install(self, *args):
        env = {
            "HOME": str(self.home),
            "DOTFILES_BACKUP_DIR": str(self.backups),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }
        result = subprocess.run(
            ["/bin/sh", str(REPO / "install.sh"), *args],
            env=env, text=True, capture_output=True, check=True,
        )
        return result.stdout

    def assert_orca_linked(self):
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(os.readlink(self.target), str(REPO / "orca" / ORCA))

    def test_herdr_install_uses_official_formula(self):
        brewfile = (REPO / "Brewfile").read_text()
        formulas = re.findall(r'^\s*brew\s+["\']([^"\']+)["\']', brewfile, re.MULTILINE)
        casks = re.findall(r'^\s*cask\s+["\']([^"\']+)["\']', brewfile, re.MULTILINE)
        self.assertEqual(formulas.count("herdr"), 1)
        self.assertNotIn("herdr", casks)

    def test_install_links_herdr_and_preserves_runtime_state(self):
        config = Path(".config/herdr/config.toml")
        target = self.home / config
        target.parent.mkdir(parents=True)
        target.write_text("# previous local configuration\n")
        sentinels = [target.parent / "sessions/state.json",
                     target.parent / "logs/herdr.log",
                     self.home / ".local/share/herdr/session.db",
                     self.home / ".local/state/herdr/private-state"]
        for path in sentinels:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("local runtime sentinel\n")
        self.install()
        self.assertTrue(target.is_symlink())
        self.assertEqual(os.readlink(target), str(REPO / "herdr" / config))
        self.assertEqual((self.backups / config).read_text(), "# previous local configuration\n")
        for path in sentinels:
            self.assertEqual(path.read_text(), "local runtime sentinel\n")
        repeated = self.install()
        self.assertNotIn("backup  ", repeated)
        self.assertNotIn("link    ", repeated)

    def test_orca_install_uses_official_qualified_cask(self):
        brewfile = (REPO / "Brewfile").read_text()
        taps = re.findall(r'^\s*tap\s+[\"\']([^\"\']+)[\"\']', brewfile, re.MULTILINE)
        casks = re.findall(r'^\s*cask\s+[\"\']([^\"\']+)[\"\']', brewfile, re.MULTILINE)
        self.assertEqual(taps.count("stablyai/orca"), 1)
        self.assertEqual(casks.count("stablyai/orca/orca"), 1)
        # The bare core cask is a different, disabled Plotly application.
        self.assertNotIn("orca", casks)

    def test_pi_and_gcloud_are_in_brewfile(self):
        brewfile = (REPO / "Brewfile").read_text()
        formulas = re.findall(r'^\s*brew\s+["\']([^"\']+)["\']', brewfile, re.MULTILINE)
        casks = re.findall(r'^\s*cask\s+["\']([^"\']+)["\']', brewfile, re.MULTILINE)
        self.assertEqual(formulas.count("pi-coding-agent"), 1)
        self.assertEqual(casks.count("gcloud-cli"), 1)

    def test_merges_orca_settings_into_existing_profile_db(self):
        db_path = self.home / "Library/Application Support/orca/profiles/local-default/profile-state.db"
        db_path.parent.mkdir(parents=True)
        initial = {"workspaceDir": "/local/only/path", "theme": "light", "voice": {"enabled": False}}
        raw = json.dumps(initial)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE profile_state_documents "
                "(domain TEXT PRIMARY KEY, payload TEXT, domain_version INT, revision INT, updated_at INT, content_hash TEXT)"
            )
            conn.execute(
                "INSERT INTO profile_state_documents VALUES ('settings', ?, 1, 5, 1000, ?)",
                (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()),
            )
        self.install()
        with sqlite3.connect(db_path) as conn:
            payload, revision, content_hash = conn.execute(
                "SELECT payload, revision, content_hash FROM profile_state_documents WHERE domain = 'settings'"
            ).fetchone()
        merged = json.loads(payload)
        self.assertEqual(merged["workspaceDir"], "/local/only/path")
        self.assertEqual(merged["theme"], "dark")
        self.assertEqual(merged["terminalFontFamily"], "SF Mono")
        self.assertEqual(merged["voice"]["sttModel"], "parakeet-tdt-ctc-0.6b-ja-int8")
        self.assertEqual(revision, 6)
        self.assertEqual(content_hash, hashlib.sha256(payload.encode("utf-8")).hexdigest())
        self.assertIn("is up to date", self.install())

    def test_install_links_orca_and_preserves_other_orca_files(self):
        settings = self.target.parent / "settings.json"
        settings.write_text('{"local":true}\n')
        self.install()
        self.assert_orca_linked()
        self.assertEqual(settings.read_text(), '{"local":true}\n')
        self.assertFalse(self.backups.exists())

    def test_existing_file_is_backed_up_and_reinstall_is_idempotent(self):
        self.target.write_text('{"local-shortcut":"keep me"}\n')
        self.install()
        self.assert_orca_linked()
        self.assertEqual(self.backup.read_text(), '{"local-shortcut":"keep me"}\n')
        output = self.install()
        self.assertNotIn("backup  ", output)
        self.assertNotIn("link    ", output)
        self.assertEqual(list(self.backup.parent.iterdir()), [self.backup])

    def test_existing_symlinks_are_backed_up_without_touching_referent(self):
        for broken in (False, True):
            with self.subTest(broken=broken):
                if self.target.is_symlink():
                    self.target.unlink()
                other = self.root / ("missing.json" if broken else "other.json")
                if not broken:
                    other.write_text("original referent\n")
                self.target.symlink_to(other)
                self.install()
                self.assert_orca_linked()
                saved = self.backup.with_name(self.backup.name + ".1") if broken else self.backup
                self.assertTrue(saved.is_symlink())
                self.assertEqual(os.readlink(saved), str(other))
                if not broken:
                    self.assertEqual(other.read_text(), "original referent\n")
                else:
                    self.assertFalse(other.exists())

    def test_occupied_backup_names_are_never_replaced(self):
        self.backup.parent.mkdir(parents=True)
        self.backup.write_text("old backup\n")
        first = self.backup.with_name(self.backup.name + ".1")
        first.symlink_to(self.root / "missing-backup-target")
        second = self.backup.with_name(self.backup.name + ".2")
        second.mkdir()
        (second / "sentinel").write_text("old directory\n")
        referent = self.root / "backup-referent.json"
        referent.write_text("old symlink referent\n")
        third = self.backup.with_name(self.backup.name + ".3")
        third.symlink_to(referent)
        self.target.write_text("current settings\n")
        self.install()
        self.assert_orca_linked()
        self.assertEqual(self.backup.read_text(), "old backup\n")
        self.assertEqual(os.readlink(first), str(self.root / "missing-backup-target"))
        self.assertEqual((second / "sentinel").read_text(), "old directory\n")
        self.assertEqual(os.readlink(third), str(referent))
        self.assertEqual(referent.read_text(), "old symlink referent\n")
        self.assertEqual(self.backup.with_name(self.backup.name + ".4").read_text(), "current settings\n")

    def test_existing_directory_is_backed_up_intact(self):
        self.target.mkdir()
        (self.target / "sentinel").write_text("keep directory contents\n")
        self.install()
        self.assert_orca_linked()
        self.assertEqual((self.backup / "sentinel").read_text(), "keep directory contents\n")

    def test_dry_run_does_not_change_existing_file_or_backup(self):
        self.target.write_text("current settings\n")
        self.backup.parent.mkdir(parents=True)
        self.backup.write_text("old backup\n")
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        output = self.install("--dry-run")
        after = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        self.assertEqual(before, after)
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(self.target.read_text(), "current settings\n")
        self.assertEqual(self.backup.read_text(), "old backup\n")
        self.assertIn(str(self.backup) + ".1", output)
        self.assertIn(str(REPO / "orca" / ORCA), output)

    def test_dry_run_destinations_are_unique(self):
        output = self.install("--dry-run")
        destinations = [
            line.removeprefix("link    ").split(" -> ", 1)[0]
            for line in output.splitlines() if line.startswith("link    ")
        ]
        self.assertEqual(len(destinations), len(set(destinations)))
        self.assertIn(str(self.home / ORCA), destinations)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.backups.exists())


if __name__ == "__main__":
    unittest.main()
