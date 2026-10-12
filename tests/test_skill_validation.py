"""Skill-package validation regressions: temporary files, no agent invocations."""

import contextlib
import copy
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml


REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "skills-local/.codex/skills"
SCRIPTS = SKILLS / "create-validated-skill/scripts"
SPEC = importlib.util.spec_from_file_location("skill_validator", SCRIPTS / "validate_skill.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class SkillValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skill-validation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skill = self.root / "example-skill"
        self.skill.mkdir()
        self.frontmatter = {"name": "example-skill", "description": "Validate observable package behavior."}
        self.write_skill()
        self.task = {
            "id": "custom scenario 日本語", "kind": "typical", "scenario": "Produce a usable result.",
            "requirements": [{
                "id": "result", "critical": True, "description": "Produces a usable result.",
                "text": {"required": False, "regex_any": ["usable"]},
                "semantic": {"rubric": "The result is usable."},
            }],
        }
        self.group = {
            "kind": "positive", "expected_activation": True,
            "cases": [{"id": "route", "critical": False, "prompt": "Please produce a result."}],
        }
        self.manifest = {
            "name": self.skill.name, "version": 2, "trials_per_task": 1,
            "tasks": ["scenarios/custom.yaml"],
            "routing": {
                "profiles": ["isolated"], "trials_per_case": 1,
                "cases": ["routing/custom.yaml"],
                "thresholds": {"positive_activation_rate": 0.9},
            },
        }

    def write_skill(self, body="Use available evidence to verify the requested result.\n"):
        (self.skill / "SKILL.md").write_text(
            "---\n" + yaml.safe_dump(self.frontmatter, sort_keys=False) + "---\n\n" + body,
            encoding="utf-8",
        )

    def write_yaml(self, relative, value):
        path = self.skill / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return path

    def write_evals(self):
        self.write_yaml("evals/eval.yaml", self.manifest)
        self.write_yaml("evals/scenarios/custom.yaml", self.task)
        self.write_yaml("evals/routing/custom.yaml", self.group)

    def assertValid(self):
        self.assertEqual(validator.validate(self.skill), [])

    def assertInvalid(self, text):
        errors = validator.validate(self.skill)
        self.assertTrue(any(text in error for error in errors), errors)

    def test_all_six_installed_skills_pass_without_mutating_historical_files(self):
        installed = sorted(path for path in SKILLS.iterdir() if (path / "SKILL.md").is_file())
        self.assertEqual(len(installed), 6)
        before = {path: path.read_bytes() for skill in installed for path in (skill / "evals").rglob("*.yaml")}
        for skill in installed:
            with self.subTest(skill=skill.name):
                self.assertEqual(validator.validate(skill), [])
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_minimal_skill_does_not_require_evaluation_scaffold(self):
        self.assertValid()
        self.assertFalse((self.skill / "evals").exists())

    def test_supported_codex_and_claude_frontmatter(self):
        self.frontmatter.update({
            "license": "MIT", "compatibility": "Codex and Claude Code", "metadata": {"short-description": "Useful result"},
            "allowed-tools": "Read, Grep, Bash(python:*)", "argument-hint": "[path]",
            "disable-model-invocation": False, "user-invocable": True, "model": "inherit",
            "context": "fork", "agent": "Explore", "hooks": {"PreToolUse": []},
        })
        self.write_skill()
        self.assertValid()
        self.frontmatter["allowed-tools"] = ["Read", "Bash(python:*)"]
        self.write_skill()
        self.assertValid()

    def test_frontmatter_folded_description_quotes_comments_and_crlf(self):
        (self.skill / "SKILL.md").write_bytes(
            b'---\r\nname: "example-skill" # comment\r\ndescription: >-\r\n  Validate a result\r\n  with evidence.\r\nmetadata:\r\n  author: "123"\r\n---\r\nDo the work.\r\n'
        )
        self.assertValid()

    def test_frontmatter_type_mutations_fail_cleanly(self):
        for key, value in (
            ("name", ["example-skill"]), ("description", 12), ("description", " "),
            ("metadata", "not a mapping"), ("metadata", {"author": 12}),
            ("allowed-tools", ["Read", 3]), ("allowed-tools", {"Read": True}),
            ("hooks", []), ("model", {}), ("disable-model-invocation", "false"),
            ("user-invocable", 1), ("unexpected", "value"),
        ):
            with self.subTest(key=key, value=value):
                original = copy.deepcopy(self.frontmatter)
                self.frontmatter[key] = value
                self.write_skill()
                self.assertTrue(validator.validate(self.skill))
                self.frontmatter = original

    def test_yaml_requires_mapping_and_rejects_duplicate_keys_and_unsafe_tags(self):
        for source, message in (
            ("[]", "mapping"),
            ("name: example-skill\nname: other\ndescription: Valid", "duplicate mapping key"),
            ("name: example-skill\ndescription: [broken", "invalid YAML"),
            ("name: example-skill\ndescription: !!python/object/apply:os.system ['false']", "invalid YAML"),
            ("name: example-skill\ndescription: Valid\nmetadata: {author: A, author: B}", "duplicate mapping key"),
            ("1: invalid", "string keys"),
        ):
            with self.subTest(source=source):
                (self.skill / "SKILL.md").write_text(f"---\n{source}\n---\nBody.\n")
                self.assertInvalid(message)

    def test_missing_frontmatter_and_unclosed_frontmatter(self):
        for source, message in (("Body", "must start"), ("---\nname: example-skill\n", "not closed")):
            (self.skill / "SKILL.md").write_text(source)
            self.assertInvalid(message)

    def test_missing_yaml_dependency_is_a_clear_validation_error(self):
        with mock.patch.object(validator, "yaml", None):
            self.assertInvalid("PyYAML is required")

    def test_placeholder_detection_is_not_a_blanket_todo_scan(self):
        self.write_skill("Describe why TODO markers are useful.\n```text\nTODO example output\n```\n")
        self.assertValid()
        for body in ("TODO Fill the workflow.\n", "1. TODO Define a step.\n", "[TODO: Add instructions]\n"):
            self.write_skill(body)
            self.assertInvalid("unfinished TODO placeholder")

    def test_declared_files_need_no_conventional_names_or_ledger(self):
        self.write_evals()
        self.assertValid()
        self.assertFalse((self.skill / "evals/ledger.yaml").exists())
        self.assertFalse((self.skill / "evals/tasks/typical.yaml").exists())

    def test_unreferenced_historical_ledgers_results_and_definitions_are_not_scanned(self):
        self.write_evals()
        for name in ("ledger.yaml", "results/past/output.yaml", "tasks/unreferenced.yaml"):
            path = self.skill / "evals" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("TODO historical evidence\n!!invalid: [\n")
        self.assertValid()

    def test_version_three_objective_tasks_and_optional_routing(self):
        self.manifest = {"name": self.skill.name, "version": 3, "validation": ["scenarios/custom.yaml"]}
        self.task["requirements"][0].pop("text")
        self.task["requirements"][0].pop("semantic")
        self.task["workspace_write"] = True
        self.task["fixture"] = "fixtures/repo"
        (self.skill / "evals/fixtures/repo").mkdir(parents=True)
        self.task["requirements"][0]["objective"] = [
            {"kind": "contains", "value": "usable"}, {"kind": "regex", "value": r"\busable\b"},
            {"kind": "json_equals", "pointer": "/result", "value": {"ok": True}},
            {"kind": "json_equals", "value": {"ok": True}},
            {"kind": "file_exists", "path": "build/result.txt"},
            {"kind": "file_contains", "path": "build/result.txt", "value": "usable"},
            {"kind": "command", "argv": ["python3", "-c", "print('ok')"], "timeout_seconds": 30},
        ]
        self.write_evals()
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("validation must not execute checks")):
            self.assertValid()

    def test_routing_only_manifest_is_valid(self):
        del self.manifest["tasks"]
        self.write_evals()
        self.assertValid()

    def test_manifest_yaml_is_parsed_not_substring_searched(self):
        self.write_evals()
        (self.skill / "evals/eval.yaml").write_text(
            'name: example-skill\nversion: "2"\nnotes: |\n  version: 2\n  routing:\n  profiles:\n  thresholds:\n'
        )
        self.assertInvalid("version must be integer")
        (self.skill / "evals/eval.yaml").write_text("name: example-skill\nversion: 2\nversion: 3\n")
        self.assertInvalid("duplicate mapping key")

    def test_manifest_type_mutations(self):
        for key, value, message in (
            ("name", "wrong", "name must match"), ("version", True, "version must be integer"),
            ("version", 4, "version must be integer"), ("tasks", "scenarios/custom.yaml", "must be a list"),
            ("validation", {}, "must be a list"), ("holdout", False, "must be a list"),
            ("routing", [], "routing must be a mapping"), ("completion", [], "completion must be a mapping"),
            ("trials_per_task", 0, "positive integer"), ("trials_per_task", True, "positive integer"),
        ):
            with self.subTest(key=key, value=value):
                original = copy.deepcopy(self.manifest)
                self.manifest[key] = value
                self.write_evals()
                self.assertInvalid(message)
                self.manifest = original

    def test_task_and_routing_paths_must_exist_and_stay_inside_evals(self):
        for reference in ("missing.yaml", "../SKILL.md", str(self.skill / "SKILL.md"), "C:/private/file.yaml", "..\\SKILL.md", None):
            for key in ("tasks", "routing"):
                with self.subTest(reference=reference, key=key):
                    original = copy.deepcopy(self.manifest)
                    if key == "tasks":
                        self.manifest[key] = [reference]
                    else:
                        self.manifest[key]["cases"] = [reference]
                    self.write_evals()
                    self.assertTrue(validator.validate(self.skill))
                    self.manifest = original

    def test_symlink_escape_for_referenced_task_and_fixture_is_rejected(self):
        self.write_evals()
        outside = self.root / "external.yaml"
        outside.write_text(yaml.safe_dump(self.task))
        link = self.skill / "evals/scenarios/external.yaml"
        link.symlink_to(outside)
        self.manifest["tasks"] = ["scenarios/external.yaml"]
        self.write_yaml("evals/eval.yaml", self.manifest)
        self.assertInvalid("escapes its directory")
        self.manifest["tasks"] = ["scenarios/custom.yaml"]
        (self.skill / "evals/fixtures").symlink_to(self.root, target_is_directory=True)
        self.task["fixture"] = "fixtures"
        self.write_evals()
        self.assertInvalid("fixture escapes its directory")

    def test_duplicate_references_and_ids_are_rejected_across_splits(self):
        self.manifest["holdout"] = ["scenarios/custom.yaml"]
        self.write_evals()
        self.assertInvalid("repeats a referenced file")
        self.manifest["holdout"] = ["scenarios/heldout.yaml"]
        self.write_yaml("evals/scenarios/heldout.yaml", self.task)
        self.write_evals()
        self.assertInvalid("duplicate id")
        del self.manifest["holdout"]
        self.task["requirements"].append(copy.deepcopy(self.task["requirements"][0]))
        self.write_evals()
        self.assertInvalid("duplicate id")

    def test_duplicate_routing_ids_are_rejected_across_groups(self):
        self.manifest["routing"]["cases"].append("routing/other.yaml")
        other = copy.deepcopy(self.group)
        other.update(kind="negative", expected_activation=False)
        self.write_yaml("evals/routing/other.yaml", other)
        self.write_evals()
        self.assertInvalid("duplicate id")

    def test_task_field_mutations_are_rejected(self):
        for key, value, message in (
            ("id", [], "id must be a nonempty string"), ("scenario", {}, "scenario must be"),
            ("kind", False, "kind must be"), ("requirements", {}, "requirements must be a nonempty list"),
            ("requirements", [], "requirements must be a nonempty list"),
            ("requirements", [None], "must be a mapping"), ("workspace_write", "true", "workspace_write must be a boolean"),
            ("fixture", "missing", "existing directory"), ("fixture", "../outside", "contained relative path"),
        ):
            with self.subTest(key=key, value=value):
                original = copy.deepcopy(self.task)
                self.task[key] = value
                self.write_evals()
                self.assertInvalid(message)
                self.task = original

    def test_requirement_and_regex_mutations_are_rejected(self):
        for key, value, message in (
            ("id", None, "id must be"), ("critical", "true", "critical must be a boolean"),
            ("description", [], "description must be"), ("semantic", [], "semantic must be a mapping"),
            ("semantic", {"rubric": False}, "rubric must be"), ("text", [], "text must be a mapping"),
            ("text", {"regex_any": "result"}, "nonempty list of strings"),
            ("text", {"regex_any": ["["]}, "invalid regex"),
            ("text", {"required": "false", "regex_any": ["ok"]}, "required must be a boolean"),
            ("objective", {}, "objective must be a nonempty list"),
        ):
            with self.subTest(key=key, value=value):
                original = copy.deepcopy(self.task)
                self.task["requirements"][0][key] = value
                self.write_evals()
                self.assertInvalid(message)
                self.task = original

    def test_semantic_only_and_regex_none_requirements_are_valid(self):
        del self.task["requirements"][0]["text"]
        self.write_evals()
        self.assertValid()
        self.task["requirements"][0]["text"] = {"regex_none": ["unwanted"], "required": False}
        self.write_evals()
        self.assertValid()

    def test_objective_field_mutations_are_rejected(self):
        checks = [
            None, {"kind": "unknown"}, {"kind": "contains", "value": []},
            {"kind": "regex", "value": "["}, {"kind": "json_equals", "value": 1, "pointer": "key"},
            {"kind": "json_equals", "pointer": "/key"},
            {"kind": "file_exists", "path": "../outside"}, {"kind": "file_exists", "path": "/outside"},
            {"kind": "file_contains", "path": "file", "value": False},
            {"kind": "command", "argv": "echo result"}, {"kind": "command", "argv": ["echo", 2]},
            {"kind": "command", "argv": ["echo"], "timeout_seconds": True},
        ]
        for check in checks:
            with self.subTest(check=check):
                self.task["requirements"][0]["objective"] = [check]
                self.write_evals()
                self.assertTrue(validator.validate(self.skill))

    def test_routing_metadata_and_case_mutations_are_rejected(self):
        for key, value, message in (
            ("profiles", "isolated", "profiles must be"), ("profiles", ["isolated", "isolated"], "duplicates"),
            ("trials_per_case", -1, "positive integer"), ("thresholds", [], "thresholds must be a mapping"),
            ("thresholds", {"positive_activation_rate": True}, "finite number"),
            ("thresholds", {"positive_activation_rate": float("nan")}, "finite number"),
            ("thresholds", {"positive_activation_rate": 1.5}, "finite number"),
            ("thresholds", {"positive_activation_rate": 10 ** 1000}, "finite number"),
        ):
            with self.subTest(key=key, value=value):
                original = copy.deepcopy(self.manifest)
                self.manifest["routing"][key] = value
                self.write_evals()
                self.assertInvalid(message)
                self.manifest = original
        for key, value, message in (
            ("kind", "other", "kind must be"), ("expected_activation", "true", "must be a boolean"),
            ("expected_activation", False, "conflicts with its kind"), ("cases", [], "nonempty list"),
            ("cases", [{"id": "route", "prompt": 3}], "prompt must be"),
        ):
            with self.subTest(key=key, value=value):
                original = copy.deepcopy(self.group)
                self.group[key] = value
                self.write_evals()
                self.assertInvalid(message)
                self.group = original

    def test_links_and_optional_json_assets_are_checked(self):
        self.write_skill("Read [the reference](references/guide.md).\n")
        self.assertInvalid("linked resource does not exist")
        path = self.skill / "references/guide.md"
        path.parent.mkdir()
        path.write_text("Useful guide")
        self.assertValid()
        self.write_skill("Use [email](mailto:example@example.com) or [section](#section).\n")
        self.assertValid()
        asset = self.skill / "assets/executor-output.schema.json"
        asset.parent.mkdir()
        asset.write_text("{")
        self.assertInvalid("invalid JSON")

    def test_broken_manifest_symlink_and_malformed_link_fail_cleanly(self):
        (self.skill / "evals").mkdir()
        (self.skill / "evals/eval.yaml").symlink_to("missing.yaml")
        self.assertInvalid("evals/eval.yaml must be a file")
        self.write_skill("Read [a malformed link](https://[).\n")
        self.assertInvalid("linked resource does not exist")

    def test_cli_failure_does_not_emit_traceback(self):
        self.frontmatter["name"] = []
        self.write_skill()
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", ["validate_skill.py", str(self.skill)]), contextlib.redirect_stderr(stderr):
            self.assertEqual(validator.main(), 1)
        self.assertNotIn("Traceback", stderr.getvalue())


class SkillInitializerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skill-init-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def initialize(self, name="new-skill", *options):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "init_skill.py"), name, "--path", str(self.root), *options],
            capture_output=True, text=True, check=False,
        )

    def test_default_initializes_only_minimal_package(self):
        result = self.initialize()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.root / "new-skill"
        self.assertCountEqual([path.name for path in skill.iterdir()], ["SKILL.md", ".candidate"])
        self.assertNotIn("evals/eval.yaml", (skill / "SKILL.md").read_text())
        self.assertTrue(validator.validate(skill))  # Unfinished instructions are not a completed skill.

    def test_optional_evals_create_version_three_scaffold(self):
        result = self.initialize("new-skill", "--evals")
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = self.root / "new-skill"
        manifest = yaml.safe_load((skill / "evals/eval.yaml").read_text())
        self.assertEqual(manifest["version"], 3)
        self.assertEqual(manifest["name"], "new-skill")
        self.assertTrue((skill / "evals/ledger.yaml").is_file())
        self.assertIn("evals/eval.yaml", (skill / "SKILL.md").read_text())

    def test_candidates_refuse_discovery_roots_including_symlink_aliases(self):
        for root in (".codex/skills", ".claude/skills", ".agents/skills", ".pi/agent/skills"):
            destination = self.root / root
            result = self.initialize("new-skill", "--path", str(destination))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(destination.exists())
        destination = self.root / ".agents/skills"
        destination.mkdir(parents=True)
        alias = self.root / "alias"
        alias.symlink_to(destination, target_is_directory=True)
        result = self.initialize("new-skill", "--path", str(alias))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(destination.iterdir()), [])
        shared = self.root / "shared"
        shared.mkdir()
        destination.rmdir()
        destination.symlink_to(shared, target_is_directory=True)
        result = self.initialize("new-skill", "--path", str(destination))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(shared.iterdir()), [])

    def test_broken_existing_symlink_is_not_overwritten(self):
        target = self.root / "new-skill"
        target.symlink_to(self.root / "missing")
        result = self.initialize()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(target.is_symlink())

    def test_existing_skill_and_invalid_name_are_not_overwritten(self):
        self.assertEqual(self.initialize().returncode, 0)
        path = self.root / "new-skill/SKILL.md"
        path.write_text("Keep existing content")
        self.assertEqual(self.initialize().returncode, 2)
        self.assertEqual(path.read_text(), "Keep existing content")
        self.assertEqual(self.initialize("../escape").returncode, 2)
        self.assertFalse((self.root.parent / "escape").exists())


if __name__ == "__main__":
    unittest.main()
