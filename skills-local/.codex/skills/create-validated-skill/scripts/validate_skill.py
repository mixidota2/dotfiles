#!/usr/bin/env python3
"""Statically validate a skill package and any declared evaluation inputs.

This checks structure, not behavioral quality. Historical ledgers and results are
not evaluation definitions and are deliberately left uninterpreted.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import unquote, urlsplit

try:
    import yaml
except ImportError:  # Report a useful CLI error without a traceback.
    yaml = None


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PLACEHOLDER_RE = re.compile(r"^(?:\[TODO:|TODO(?:\s|[-:]|$))")
STRING_FRONTMATTER = {
    "name", "description", "license", "compatibility", "argument-hint",
    "model", "context", "agent",
}
BOOL_FRONTMATTER = {"disable-model-invocation", "user-invocable"}
ALLOWED_FRONTMATTER = STRING_FRONTMATTER | BOOL_FRONTMATTER | {"metadata", "allowed-tools", "hooks"}
THRESHOLDS = {
    "critical_positive_activation_rate", "positive_activation_rate",
    "negative_false_activation_rate", "near_miss_false_activation_rate",
}


if yaml is not None:
    class UniqueSafeLoader(yaml.SafeLoader):
        """Safe YAML with duplicate keys rejected instead of silently replaced."""

    def _unique_mapping(loader: Any, node: Any, deep: bool = False) -> dict:
        loader.flatten_mapping(node)
        mapping: dict = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    "unhashable mapping key", key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"duplicate mapping key: {key!r}", key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def parse_yaml_mapping(text: str, label: str) -> dict[str, Any]:
    if yaml is None:
        raise ValueError("PyYAML is required; install PyYAML or run with uv run --with pyyaml")
    try:
        value = yaml.load(text, Loader=UniqueSafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError(f"{label} contains invalid YAML: {exc}") from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a YAML mapping with string keys")
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter")
    for end, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return parse_yaml_mapping("".join(lines[1:end]), "SKILL.md frontmatter"), "".join(lines[end + 1:])
    raise ValueError("SKILL.md frontmatter is not closed")


def _string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mapping(value: Any) -> bool:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _positive_integer(value: Any) -> bool:
    return type(value) is int and value > 0


def _placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER_RE.match(value.strip()))


def _safe_relative(value: Any) -> bool:
    # Check both path dialects so a package is safe on Unix and Windows.
    return (
        _string(value) and not PurePosixPath(value).is_absolute()
        and not PureWindowsPath(value).drive and not PureWindowsPath(value).root
        and ".." not in PurePosixPath(value).parts
        and ".." not in PureWindowsPath(value).parts and "\x00" not in value
        and "\\" not in value
    )


def _contained_path(root: Path, value: Any, label: str, errors: list[str], *, directory: bool = False) -> Path | None:
    if not _safe_relative(value):
        errors.append(f"{label} must be a contained relative path")
        return None
    try:
        path = (root / value).resolve()
        path.relative_to(root.resolve())
        exists = path.is_dir() if directory else path.is_file()
    except (ValueError, OSError, RuntimeError):
        errors.append(f"{label} escapes its directory or cannot be resolved: {value}")
        return None
    if not exists:
        errors.append(f"{label} does not reference an existing {'directory' if directory else 'file'}: {value}")
        return None
    return path


def _read_mapping(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    try:
        return parse_yaml_mapping(path.read_text(encoding="utf-8"), label)
    except (ValueError, OSError, UnicodeError) as exc:
        errors.append(str(exc))
        return None


def _field_text(item: dict, key: str, label: str, errors: list[str]) -> None:
    if not _string(item.get(key)):
        errors.append(f"{label}.{key} must be a nonempty string")
    elif _placeholder(item[key]):
        errors.append(f"{label}.{key} contains an unfinished TODO placeholder")


def _record_id(item: dict, label: str, seen: set[str], errors: list[str]) -> None:
    _field_text(item, "id", label, errors)
    identifier = item.get("id")
    if not _string(identifier):
        return
    if identifier in seen:
        errors.append(f"{label} has duplicate id: {identifier}")
    seen.add(identifier)


def _validate_objective(check: Any, label: str, errors: list[str]) -> None:
    if not _mapping(check):
        errors.append(f"{label} must be a mapping")
        return
    kind = check.get("kind")
    if kind not in ("contains", "regex", "json_equals", "file_exists", "file_contains", "command"):
        errors.append(f"{label}.kind is not a supported objective check")
        return
    if kind in ("contains", "regex", "file_contains"):
        _field_text(check, "value", label, errors)
    if kind == "regex" and _string(check.get("value")):
        try:
            re.compile(check["value"])
        except re.error as exc:
            errors.append(f"{label}.value is an invalid regex: {exc}")
    if kind in ("file_exists", "file_contains") or "path" in check:
        if not _safe_relative(check.get("path")):
            errors.append(f"{label}.path must be a contained relative workspace path")
    if kind == "json_equals":
        if "value" not in check:
            errors.append(f"{label}.value is required")
        else:
            try:
                json.dumps(check["value"], allow_nan=False)
            except (TypeError, ValueError, RecursionError):
                errors.append(f"{label}.value must be JSON data")
        pointer = check.get("pointer", "")
        if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
            errors.append(f"{label}.pointer must be a JSON pointer string")
    if kind == "command":
        argv = check.get("argv")
        if not isinstance(argv, list) or not argv or not all(_string(arg) for arg in argv):
            errors.append(f"{label}.argv must be a nonempty list of strings")
    if "timeout_seconds" in check and not _positive_integer(check["timeout_seconds"]):
        errors.append(f"{label}.timeout_seconds must be a positive integer")


def _validate_task(task: dict, label: str, root: Path, seen: set[str], errors: list[str]) -> None:
    _record_id(task, label, seen, errors)
    for key in ("kind", "scenario"):
        _field_text(task, key, label, errors)
    if "fixture" in task:
        _contained_path(root, task["fixture"], f"{label}.fixture", errors, directory=True)
    if "workspace_write" in task and type(task["workspace_write"]) is not bool:
        errors.append(f"{label}.workspace_write must be a boolean")
    requirements = task.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append(f"{label}.requirements must be a nonempty list")
        return
    requirement_ids: set[str] = set()
    for index, requirement in enumerate(requirements):
        req_label = f"{label}.requirements[{index}]"
        if not _mapping(requirement):
            errors.append(f"{req_label} must be a mapping")
            continue
        _record_id(requirement, req_label, requirement_ids, errors)
        _field_text(requirement, "description", req_label, errors)
        if "critical" in requirement and type(requirement["critical"]) is not bool:
            errors.append(f"{req_label}.critical must be a boolean")
        if not any(key in requirement for key in ("text", "semantic", "objective")):
            errors.append(f"{req_label} must declare text, semantic, or objective grading")
        if "text" in requirement:
            config = requirement["text"]
            if not _mapping(config):
                errors.append(f"{req_label}.text must be a mapping")
            else:
                if "required" in config and type(config["required"]) is not bool:
                    errors.append(f"{req_label}.text.required must be a boolean")
                pattern_keys = [key for key in ("regex_any", "regex_all", "regex_none") if key in config]
                if not pattern_keys:
                    errors.append(f"{req_label}.text must declare regex_any, regex_all, or regex_none")
                for key in pattern_keys:
                    patterns = config[key]
                    if not isinstance(patterns, list) or not patterns or not all(_string(p) for p in patterns):
                        errors.append(f"{req_label}.text.{key} must be a nonempty list of strings")
                    else:
                        for pattern in patterns:
                            try:
                                re.compile(pattern)
                            except re.error as exc:
                                errors.append(f"{req_label}.text.{key} contains an invalid regex: {exc}")
        if "semantic" in requirement:
            if not _mapping(requirement["semantic"]):
                errors.append(f"{req_label}.semantic must be a mapping")
            else:
                _field_text(requirement["semantic"], "rubric", f"{req_label}.semantic", errors)
        if "objective" in requirement:
            checks = requirement["objective"]
            if not isinstance(checks, list) or not checks:
                errors.append(f"{req_label}.objective must be a nonempty list")
            else:
                for check_index, check in enumerate(checks):
                    _validate_objective(check, f"{req_label}.objective[{check_index}]", errors)


def _references(value: Any, root: Path, label: str, seen: set[Path], errors: list[str]) -> list[tuple[dict, str]]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list of relative file paths")
        return []
    documents = []
    for index, reference in enumerate(value):
        ref_label = f"{label}[{index}]"
        path = _contained_path(root, reference, ref_label, errors)
        if path is None:
            continue
        if path in seen:
            errors.append(f"{ref_label} repeats a referenced file: {reference}")
            continue
        seen.add(path)
        document = _read_mapping(path, str(reference), errors)
        if document is not None:
            documents.append((document, str(reference)))
    return documents


def _validate_manifest(path: Path, name: str, errors: list[str]) -> None:
    label = "evals/eval.yaml"
    manifest = _read_mapping(path, label, errors)
    if manifest is None:
        return
    if manifest.get("name") != name:
        errors.append(f"{label} name must match the skill name")
    if type(manifest.get("version")) is not int or manifest["version"] not in (2, 3):
        errors.append(f"{label} version must be integer 2 or 3")
    if "tier" in manifest and not _string(manifest["tier"]):
        errors.append(f"{label}.tier must be a nonempty string")
    if "trials_per_task" in manifest and not _positive_integer(manifest["trials_per_task"]):
        errors.append(f"{label}.trials_per_task must be a positive integer")
    if "completion" in manifest and not _mapping(manifest["completion"]):
        errors.append(f"{label}.completion must be a mapping")
    task_ids: set[str] = set()
    task_paths: set[Path] = set()
    for split in ("tasks", "validation", "holdout"):
        if split in manifest:
            for task, task_label in _references(manifest[split], path.parent, f"{label}.{split}", task_paths, errors):
                _validate_task(task, task_label, path.parent, task_ids, errors)
    if "routing" not in manifest:
        return
    routing = manifest["routing"]
    if not _mapping(routing):
        errors.append(f"{label}.routing must be a mapping")
        return
    if "profiles" in routing:
        profiles = routing["profiles"]
        if not isinstance(profiles, list) or not profiles or not all(_string(p) for p in profiles):
            errors.append(f"{label}.routing.profiles must be a nonempty list of strings")
        elif len(profiles) != len(set(profiles)):
            errors.append(f"{label}.routing.profiles contains duplicates")
    if "trials_per_case" in routing and not _positive_integer(routing["trials_per_case"]):
        errors.append(f"{label}.routing.trials_per_case must be a positive integer")
    if "thresholds" in routing:
        thresholds = routing["thresholds"]
        if not _mapping(thresholds):
            errors.append(f"{label}.routing.thresholds must be a mapping")
        else:
            for key, value in thresholds.items():
                if key not in THRESHOLDS:
                    errors.append(f"{label}.routing.thresholds has unsupported key: {key}")
                if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
                    errors.append(f"{label}.routing.thresholds.{key} must be a finite number from 0 to 1")
    case_ids: set[str] = set()
    for group, group_label in _references(routing.get("cases"), path.parent, f"{label}.routing.cases", set(), errors):
        kind = group.get("kind")
        if kind not in ("positive", "negative", "near-miss"):
            errors.append(f"{group_label}.kind must be positive, negative, or near-miss")
        if type(group.get("expected_activation")) is not bool:
            errors.append(f"{group_label}.expected_activation must be a boolean")
        elif kind in ("positive", "negative", "near-miss") and group["expected_activation"] != (kind == "positive"):
            errors.append(f"{group_label}.expected_activation conflicts with its kind")
        cases = group.get("cases")
        if not isinstance(cases, list) or not cases:
            errors.append(f"{group_label}.cases must be a nonempty list")
            continue
        for index, case in enumerate(cases):
            case_label = f"{group_label}.cases[{index}]"
            if not _mapping(case):
                errors.append(f"{case_label} must be a mapping")
                continue
            _record_id(case, case_label, case_ids, errors)
            _field_text(case, "prompt", case_label, errors)
            if "critical" in case and type(case["critical"]) is not bool:
                errors.append(f"{case_label}.critical must be a boolean")


def validate(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        return ["SKILL.md is missing"]
    try:
        frontmatter, body = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError) as exc:
        return [str(exc)]
    unknown = set(frontmatter) - ALLOWED_FRONTMATTER
    if unknown:
        errors.append(f"frontmatter has unsupported keys: {', '.join(sorted(unknown))}")
    for key in STRING_FRONTMATTER & frontmatter.keys():
        if not _string(frontmatter[key]):
            errors.append(f"frontmatter {key} must be a nonempty string")
    for key in BOOL_FRONTMATTER & frontmatter.keys():
        if type(frontmatter[key]) is not bool:
            errors.append(f"frontmatter {key} must be a boolean")
    for key in ("metadata", "hooks"):
        if key in frontmatter and not _mapping(frontmatter[key]):
            errors.append(f"frontmatter {key} must be a mapping with string keys")
    if _mapping(frontmatter.get("metadata")) and not all(isinstance(v, str) for v in frontmatter["metadata"].values()):
        errors.append("frontmatter metadata values must be strings")
    if "allowed-tools" in frontmatter:
        value = frontmatter["allowed-tools"]
        if not (_string(value) or (isinstance(value, list) and value and all(_string(v) for v in value))):
            errors.append("frontmatter allowed-tools must be a string or nonempty list of strings")
    name = frontmatter.get("name", "")
    description = frontmatter.get("description", "")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or len(name) > 64:
        errors.append("frontmatter name must be lowercase kebab-case and at most 64 characters")
    if name != skill_dir.name:
        errors.append(f"frontmatter name {name!r} does not match directory {skill_dir.name!r}")
    if not _string(description) or len(description.strip()) > 1024:
        errors.append("description must be 1-1024 characters")
    if _placeholder(description):
        errors.append("description contains an unfinished TODO placeholder")
    # Scaffold placeholders are errors; examples and prose discussing TODOs are not.
    fence = None
    for line in body.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
        elif fence is None and _placeholder(re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?", "", line)):
            errors.append("SKILL.md contains an unfinished TODO placeholder")
            break
    for link in LINK_RE.findall(body):
        target = link.strip().split(' "', 1)[0].strip("<>")
        try:
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            exists = (skill_dir / unquote(parsed.path)).exists()
        except (OSError, ValueError):
            exists = False
        if not exists:
            errors.append(f"linked resource does not exist: {link}")
    manifest = skill_dir / "evals/eval.yaml"
    if manifest.exists() or manifest.is_symlink():
        if manifest.is_file():
            _validate_manifest(manifest, name, errors)
        else:
            errors.append("evals/eval.yaml must be a file")
    for relative in ("assets/executor-output.schema.json", "assets/semantic-grade.schema.json"):
        path = skill_dir / relative
        if path.is_file():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError, UnicodeError) as exc:
                errors.append(f"{relative} is invalid JSON: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_directory", type=Path)
    args = parser.parse_args()
    skill_dir = args.skill_directory.expanduser().resolve()
    errors = validate(skill_dir)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"validated {skill_dir} (static checks only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
