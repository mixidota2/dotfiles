#!/usr/bin/env python3
"""Run matched skill experiments; report evidence, never automatically adopt a skill."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from typing import Any

# Also works when imported by unittest from outside the script directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_adapters
import validate_skill
try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
EXECUTOR_SCHEMA = ROOT / "assets" / "executor-output.schema.json"
GRADER_SCHEMA = ROOT / "assets" / "semantic-grade.schema.json"
TREE_DIGEST_FORMAT = "sha256-tree-manifest-v2"

def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required; run with: uv run --with pyyaml python scripts/run_eval.py ...")
    value = validate_skill.parse_yaml_mapping(path.read_text(encoding="utf-8"), str(path))
    if not isinstance(value, dict):
        raise ValueError(f"expected a YAML mapping: {path}")
    return value


def write_yaml(path: Path, value: Any) -> None:
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def contained_path(path: Path, root: Path) -> str | None:
    """Return a portable path, or explicitly omit an out-of-scope reference."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def compact_run_record(run_record: dict[str, Any], skill_dir: Path) -> dict[str, Any]:
    results_root = Path(run_record["results_dir"])
    results_dir = contained_path(results_root, skill_dir)

    def relative_artifact(path: str) -> str | None:
        return contained_path(Path(path), results_root)

    routing_trials = [
        {
            "case": item["case"],
            "kind": item["kind"],
            "critical": item["critical"],
            "profile": item["profile"],
            "trial": item["trial"],
            "expected_activation": item["expected_activation"],
            "observed_activation": item["observed_activation"],
            "verdict": item["verdict"],
            "exit_code": item["exit_code"],
        }
        for item in run_record["routing_trials"]
    ]
    behavior_trials = []
    for item in run_record["behavior_trials"]:
        artifacts = {
            field: relative_artifact(item[field])
            for field in ("output_path", "events_path")
        }
        requirements = []
        for requirement in item.get("requirements", []):
            semantic = requirement.get("semantic", {})
            req = {
                "id": requirement["id"],
                "critical": requirement["critical"],
                "combined_verdict": requirement["combined_verdict"],
                "text_verdict": requirement.get("text", {}).get("verdict", requirement.get("text_verdict", "unclear")),
                "semantic_verdict": semantic.get("verdict", requirement.get("semantic_verdict", "unclear")),
                "objective": requirement.get("objective", []),
            }
            if req["combined_verdict"] != "pass":
                req["evidence"] = semantic.get("evidence", requirement.get("evidence", ""))
                req["general_fix_rule"] = semantic.get("general_fix_rule", requirement.get("general_fix_rule", ""))
            requirements.append(req)
        behavior_trials.append({
            "task": item["task"],
            "kind": item["kind"],
            "config": item.get("config", "with_skill"),
            "trial": item["trial"],
            "verdict": item["verdict"],
            "executor_exit_code": item["executor_exit_code"],
            "semantic_exit_code": item["semantic_exit_code"],
            **artifacts,
            "unresolved_artifacts": [field for field, path in artifacts.items() if path is None],
            "executor_duration_ms": item.get("executor_duration_ms", 0),
            "executor_tool_uses": item.get("executor_tool_uses", 0),
            "phase_status": {
                phase: (value.get("status", "unknown") if isinstance(value, dict) else value)
                for phase, value in (item.get("phase_trace") or item.get("phase_status", {})).items()
            },
            "unclear_points": [
                {
                    "issue": issue.get("issue", ""),
                    "general_fix_rule": issue.get("general_fix_rule", ""),
                }
                for issue in item.get("unclear_points", [])
            ],
            "retry_count": item.get("retries", {}).get("count", item.get("retry_count", 0)),
            "requirements": requirements,
            **{key: item[key] for key in ("scenario_sha256", "fixture_sha256", "runtime_skill_sha256", "target_skill_staged", "global_skill_availability", "skill_loaded_observed", "output_error") if key in item},
        })
    return {
        "id": run_record["id"],
        "mode": run_record["mode"],
        "model": run_record["model"],
        "results_dir": results_dir,
        "results_dir_scope": "skill" if results_dir is not None else "external",
        "routing_summary": run_record["routing_summary"],
        "routing_gates": run_record["routing_gates"],
        "routing_trials": routing_trials,
        "behavior_summary": run_record.get("behavior_summary", {}),
        "baseline_delta": run_record.get("baseline_delta", {}),
        "behavior_trials": behavior_trials,
        **{key: run_record[key] for key in ("schema_version", "engine", "cli_version", "split", "tree_digest_format", "candidate_sha256", "evaluation_sha256", "input_freeze", "decision", "decision_reasons", "case_comparisons", "limitations") if key in run_record},
    }


def update_failure_patterns(ledger: dict[str, Any], run_record: dict[str, Any]) -> None:
    patterns = ledger.setdefault("failure_patterns", [])
    by_rule = {item["general_fix_rule"]: item for item in patterns}
    occurrences = []
    for trial in run_record["behavior_trials"]:
        if trial.get("config", "with_skill") not in {"with_skill", "candidate"}:
            continue
        for issue in trial.get("unclear_points", []):
            occurrences.append((trial, "executor", issue))
        for requirement in trial.get("requirements", []):
            semantic = requirement.get("semantic", {})
            if semantic.get("verdict") in ("fail", "unclear") and semantic.get("general_fix_rule"):
                occurrences.append((trial, requirement["id"], semantic))
            elif requirement.get("combined_verdict") in ("fail", "unclear", "review_required") and requirement.get("general_fix_rule"):
                occurrences.append((trial, requirement["id"], requirement))
            # Objective failures alone establish an observation, not a general
            # remedy. Preserve them in behavior_trials without inventing a rule.
    for trial, source, issue in occurrences:
        rule = issue.get("general_fix_rule", "").strip()
        if not rule:
            continue
        pattern = by_rule.get(rule)
        if pattern is None:
            pattern = {
                "general_fix_rule": rule,
                "example_issue": issue.get("issue", ""),
                "seen_in": [],
            }
            patterns.append(pattern)
            by_rule[rule] = pattern
        pattern["seen_in"].append({
            "run": run_record["id"],
            "task": trial["task"],
            "trial": trial["trial"],
            "source": source,
        })


def append_ledger(skill_dir: Path, run_record: dict[str, Any]) -> None:
    ledger_path = skill_dir / "evals" / "ledger.yaml"
    ledger = load_yaml(ledger_path) if ledger_path.is_file() else {"skill": skill_dir.name}
    update_failure_patterns(ledger, run_record)
    ledger.setdefault("automated_runs", []).append(compact_run_record(run_record, skill_dir))
    write_yaml(ledger_path, ledger)



def digest(path):
    """Hash an unambiguous manifest of a staged tree, including empty directories."""
    entries = []
    items = [path]
    while items:
        item = items.pop()
        mode = item.lstat().st_mode  # Never dereference a workspace symlink.
        # Staging excludes these generated directories. Normal Python imports
        # may recreate them; they are not source inputs. A same-name symlink or
        # file is still hashed so replacing a directory with one is detectable.
        if item != path and item.name == "__pycache__" and stat.S_ISDIR(mode):
            continue
        entry = {"path": item.relative_to(path).as_posix()}
        if stat.S_ISLNK(mode):
            entry.update(type="symlink", target=os.readlink(item))
        elif stat.S_ISDIR(mode):
            entry.update(type="directory", executable_bits=mode & 0o111)
            items.extend(item.iterdir())
        elif stat.S_ISREG(mode):
            entry.update(type="file", executable_bits=mode & 0o111,
                         sha256=hashlib.sha256(item.read_bytes()).hexdigest())
        else:
            raise ValueError(f"unsupported snapshot entry type: {entry['path']}")
        entries.append(entry)
    entries.sort(key=lambda entry: entry["path"])
    manifest = {"format": TREE_DIGEST_FORMAT, "entries": entries}
    encoded = json.dumps(manifest, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def safe_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("expected nonempty relative path")
    result = (root / relative).resolve()
    if contained_path(result, root) is None:
        raise ValueError(f"path escapes its root: {relative}")
    return result


def stage_tree(source, destination, *, skill=False, evaluation=False):
    """Copy runtime inputs, not source links, graders, ledgers, or held-out data."""
    destination.mkdir(parents=True, exist_ok=False)
    allowed = {"SKILL.md", "references", "scripts", "assets", "agents"}
    for item in source.iterdir():
        if evaluation and item.is_dir() and contained_path(destination, item) is not None:
            continue
        if (skill and item.name not in allowed) or item.name in {".git", "__pycache__", "evals"} or (evaluation and item.name in {"results", "ledger.yaml", "evidence"}):
            continue
        if item.is_symlink() or any(p.is_symlink() for p in item.rglob("*")):
            raise ValueError(f"staged inputs must not contain symlinks: {item.name}")
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "evals", ".git"))
        elif item.is_file():
            shutil.copy2(item, target)


def freeze_inputs(skill, destination):
    """Record input identity without running an agent or overwriting evidence."""
    if contained_path(destination, skill) is not None:
        raise ValueError("freeze record must be outside the skill package")
    with tempfile.TemporaryDirectory(prefix="skill-freeze-") as raw:
        root = Path(raw)
        stage_tree(skill, root / "candidate", skill=True)
        stage_tree(skill / "evals", root / "evaluation", evaluation=True)
        record = {"version": 2, "tree_digest_format": TREE_DIGEST_FORMAT, "created_at": utc_stamp(),
                  "candidate_sha256": digest(root / "candidate"),
                  "evaluation_sha256": digest(root / "evaluation")}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    return record


def verify_frozen_inputs(path, candidate, evaluation):
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or type(record.get("version")) is not int:
        raise ValueError("invalid input freeze record")
    if record["version"] != 2 or record.get("tree_digest_format") != TREE_DIGEST_FORMAT:
        raise ValueError("unsupported input freeze format; preserve the old record and re-freeze with --freeze-inputs to a new path (version 2 required)")
    for key, directory in (("candidate_sha256", candidate), ("evaluation_sha256", evaluation)):
        if record.get(key) != digest(directory):
            raise ValueError(f"frozen input mismatch: {key}; edits require a new experiment")
    return record


def valid_output(path):
    """Validate the measured deliverable, independently of claimed phase success."""
    try:
        output = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"missing or malformed executor output: {exc.__class__.__name__}"
    if not isinstance(output, dict) or not isinstance(output.get("deliverable"), str) or not output["deliverable"].strip():
        return {}, "executor output must contain a nonempty string deliverable"
    return output, None


def objective_check(check, output, workspace, eval_root, evidence_dir, index):
    """Grade only the actual deliverable/files, never phase notes or self-scores."""
    kind = check.get("kind")
    text = output["deliverable"]
    detail = ""
    try:
        if kind == "contains":
            passed = check["value"] in text
        elif kind == "regex":
            passed = re.search(check["value"], text) is not None
        elif kind == "json_equals":
            value = json.loads(text)
            for segment in check.get("pointer", "").split("/")[1:]:
                segment = segment.replace("~1", "/").replace("~0", "~")
                value = value[int(segment)] if isinstance(value, list) else value[segment]
            passed = value == check["value"]
        elif kind in {"file_exists", "file_contains"}:
            path = safe_path(workspace, check["path"])
            passed = path.is_file()
            if passed and kind == "file_contains":
                passed = check["value"] in path.read_text(encoding="utf-8")
        elif kind == "command":
            # Local evaluation definitions are trusted code, like repository tests.
            # Never interpret model output as a command or use shell=True.
            argv = check["argv"]
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                raise ValueError("command check requires nonempty argv string list")
            argv = [x.replace("{workspace}", str(workspace)).replace("{eval_root}", str(eval_root)) for x in argv]
            result = subprocess.run(argv, cwd=workspace, capture_output=True, text=True,
                                    timeout=check.get("timeout_seconds", 30))
            evidence = evidence_dir / f"check-{index}.txt"
            evidence.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
            passed = result.returncode == 0
            detail = f"exit={result.returncode}; evidence={evidence.name}"
        else:
            raise ValueError(f"unsupported objective kind: {kind}")
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired) as exc:
        return {"kind": kind, "verdict": "error", "evidence": str(exc)}
    return {"kind": kind, "verdict": "pass" if passed else "fail", "evidence": detail or f"{kind} on measured deliverable"}


def grade_task(task, output, workspace, eval_root, evidence_dir):
    result = []
    for req in task["requirements"]:
        checks = req.get("objective", [])
        grades = [objective_check(c, output, workspace, eval_root, evidence_dir, f"{req['id']}-{i}") for i, c in enumerate(checks)]
        if not checks:
            verdict = "review_required"
        elif any(g["verdict"] == "error" for g in grades):
            verdict = "error"
        else:
            verdict = "pass" if all(g["verdict"] == "pass" for g in grades) else "fail"
        result.append({"id": req["id"], "critical": bool(req.get("critical")),
                       "combined_verdict": verdict, "objective": grades,
                       "semantic": {"verdict": "unrun", "evidence": "Legacy qualitative rubric requires independent review; no automated semantic adoption gate."}})
    return result


def detect_activation(trace, skill_file, engine="codex"):
    """Conservative observable read evidence; mentions and failed commands do not count."""
    import shlex
    expected = str(skill_file)
    evidence = []
    all_events = agent_adapters.events(trace)
    if engine == "codex":
        for event in all_events:
            item = agent_adapters.mapping(event.get("item"))
            if event.get("type") != "item.completed" or item.get("type") != "command_execution" or item.get("exit_code") != 0:
                continue
            try:
                argv = shlex.split(item.get("command", ""))
                if len(argv) >= 3 and Path(argv[0]).name in {"bash", "sh", "zsh"} and argv[1] in {"-c", "-lc"}:
                    argv = shlex.split(argv[2])
            except ValueError:
                continue
            # Do not infer reads from arbitrary shell programs, output, or chains.
            if len(argv) == 2 and Path(argv[0]).name == "cat" and argv[1] == expected:
                evidence.append({"type": "successful_read_command", "command": item["command"]})
    else:
        requested = {}
        for event in all_events:
            for item in agent_adapters.content(event):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use" and item.get("name") == "Read" and agent_adapters.mapping(item.get("input")).get("file_path") == expected:
                    requested[item.get("id")] = item
                if item.get("type") == "tool_result" and item.get("tool_use_id") in requested and not item.get("is_error", False):
                    evidence.append({"type": "successful_Read", "tool_use_id": item["tool_use_id"]})
    return bool(evidence), evidence


def selected_tasks(skill, manifest, args, eval_root=None):
    key = {"development": "tasks", "validation": "validation", "holdout": "holdout"}[args.split]
    paths = manifest.get(key, [])
    selected = []
    ids = set()
    for relative in paths:
        task = load_yaml(safe_path(eval_root or skill / "evals", relative))
        task_id = task.get("id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[\w .-]+", task_id) or task_id in ids or task_id in {".", ".."}:
            raise ValueError("task IDs must be unique safe path components")
        ids.add(task_id)
        requirements = task.get("requirements")
        if not isinstance(task.get("scenario"), str) or not task["scenario"].strip() or not isinstance(requirements, list) or not requirements:
            raise ValueError(f"{task_id}: scenario and nonempty requirements required")
        req_ids = [req.get("id") for req in requirements if isinstance(req, dict)]
        if len(req_ids) != len(requirements) or any(not isinstance(x, str) or x in {".", ".."} or not re.fullmatch(r"[\w.-]+", x) for x in req_ids) or len(set(req_ids)) != len(req_ids):
            raise ValueError(f"{task_id}: invalid or duplicate requirement IDs")
        if not any(req.get("critical") is True for req in requirements):
            raise ValueError(f"{task_id}: at least one explicitly critical requirement is required")
        if not args.case or task_id in args.case:
            selected.append(task)
    if not selected:
        raise ValueError("no evaluation cases selected; nothing was tested")
    return selected


def run_trial(skill, task, arm, frozen_skill, run_dir, args, trial, eval_root):
    trial_dir = run_dir / "behavior" / task["id"] / arm / f"trial-{trial}"
    trial_dir.mkdir(parents=True)
    workspace = trial_dir / "workspace"
    fixture = task.get("fixture")
    if fixture:
        stage_tree(safe_path(eval_root, fixture), workspace)
    else:
        workspace.mkdir()
    fixture_hash = digest(workspace)
    staged_skill = None
    if frozen_skill is not None:
        staged_skill = trial_dir / "runtime-skill" / skill.name
        stage_tree(frozen_skill, staged_skill, skill=True)
    runtime_before = digest(staged_skill) if staged_skill else None
    prompt = task["scenario"] + "\n\nWork in the assigned workspace. Return the actual deliverable and disclose blocked work."
    if staged_skill:
        prompt += f"\nUse the skill at {staged_skill / 'SKILL.md'} for this task."
    output_path = trial_dir / "executor-output.json"
    trace = trial_dir / "executor-events.jsonl"
    meta = agent_adapters.execute(engine=args.engine, binary=args.binary, prompt=prompt, workspace=workspace,
         schema=EXECUTOR_SCHEMA, output=output_path, trace=trace, stderr=trial_dir / "executor-stderr.txt",
         model=args.model, writable=bool(task.get("workspace_write", False)), timeout=args.timeout_seconds,
         runtime=trial_dir / "runtime-state", claude_accept_workspace_edits=args.claude_accept_workspace_edits)
    output, error = valid_output(output_path)
    if staged_skill and digest(staged_skill) != runtime_before:
        error = "staged runtime skill changed during execution; contaminated trial"
    grades = []
    if meta["exit_code"] == 0 and error is None:
        grades = grade_task(task, output, workspace, eval_root, trial_dir)
    if meta["exit_code"] or error or any(g["combined_verdict"] == "error" for g in grades):
        verdict = "error"
    elif any(g["combined_verdict"] == "fail" for g in grades):
        verdict = "fail"
    elif any(g["combined_verdict"] == "review_required" for g in grades):
        verdict = "review_required"
    else:
        verdict = "pass"
    activated, evidence = detect_activation(trace, staged_skill / "SKILL.md", args.engine) if staged_skill else (False, [])
    result = {"task": task["id"], "kind": task.get("kind", args.split), "config": arm,
        "trial": trial, "verdict": verdict,
        "critical_regression": any(g["critical"] and g["combined_verdict"] == "fail" for g in grades), "executor_exit_code": meta["exit_code"], "semantic_exit_code": None,
        "output_error": error, "output_path": str(output_path), "events_path": str(trace),
        "executor_duration_ms": meta["duration_ms"], "executor_tool_uses": meta["tool_uses"],
        "executor_usage": meta["usage"], "requirements": grades, "fixture_sha256": fixture_hash,
        "scenario_sha256": hashlib.sha256(task["scenario"].encode()).hexdigest(),
        "runtime_skill_sha256": runtime_before,
        "workspace_after_sha256": digest(workspace), "target_skill_staged": staged_skill is not None, "global_skill_availability": "unverified",
        "skill_loaded_observed": activated, "activation_evidence": evidence, "execution": meta,
        "settings_sha256": hashlib.sha256(json.dumps({"engine": args.engine, "model": args.model, "workspace_write": bool(task.get("workspace_write", False)), "timeout_seconds": args.timeout_seconds, "claude_accept_workspace_edits": args.claude_accept_workspace_edits}, sort_keys=True).encode()).hexdigest()}
    write_yaml(trial_dir / "result.yaml", result)
    return result


def summarize(results):
    summary = {}
    for arm in sorted({r["config"] for r in results}):
        group = [r for r in results if r["config"] == arm]
        summary[arm] = {"trials": len(group), "passed": sum(r["verdict"] == "pass" for r in group),
                        "failed": sum(r["verdict"] == "fail" for r in group),
                        "errors": sum(r["verdict"] == "error" for r in group),
                        "review_required": sum(r["verdict"] == "review_required" for r in group),
                        "duration_ms": sum(r["executor_duration_ms"] for r in group)}
    pairs = []
    for r in results:
        if r["config"] != "candidate":
            continue
        for other in ("no_skill", "previous"):
            match = next((x for x in results if x["task"] == r["task"] and x["trial"] == r["trial"] and x["config"] == other), None)
            if match:
                valid = r["verdict"] in {"pass", "fail"} and match["verdict"] in {"pass", "fail"}
                relation = "inconclusive" if not valid else "tie" if r["verdict"] == match["verdict"] else "win" if r["verdict"] == "pass" else "loss"
                before = {g["id"]: g for g in match.get("requirements", [])}
                regressions = [g["id"] for g in r.get("requirements", []) if g["combined_verdict"] == "fail" and before.get(g["id"], {}).get("combined_verdict") == "pass"]
                improvements = [g["id"] for g in r.get("requirements", []) if g["combined_verdict"] == "pass" and before.get(g["id"], {}).get("combined_verdict") == "fail"]
                pairs.append({"task": r["task"], "trial": r["trial"], "against": other, "result": relation,
                    "requirement_regressions": regressions, "requirement_improvements": improvements,
                    "critical_regressions": [g["id"] for g in r.get("requirements", []) if g["id"] in regressions and g["critical"]]})
    return summary, pairs


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_directory", nargs="?", type=Path)
    parser.add_argument("--engine", choices=("codex", "claude"), default="codex")
    parser.add_argument("--binary", help="existing official agent executable")
    parser.add_argument("--claude-accept-workspace-edits", action="store_true", help="explicitly approve Claude file edits in disposable trial workspaces; no shell tools")
    parser.add_argument("--model", help="pin model for matched comparisons")
    parser.add_argument("--previous-skill", type=Path, help="compare candidate with frozen previous version; does not add a no-skill arm")
    parser.add_argument("--baseline", action="store_true", help="explicitly add a matched no-target-skill arm, including alongside --previous-skill")
    parser.add_argument("--split", choices=("development", "validation", "holdout"), default="development")
    parser.add_argument("--candidate-frozen", action="store_true", help="deprecated declaration; holdout still requires --frozen-inputs")
    parser.add_argument("--freeze-inputs", type=Path, help="write a new candidate/evaluation hash record outside the skill, then exit without agent calls")
    parser.add_argument("--frozen-inputs", type=Path, help="verify against a prior freeze record; required for holdout")
    parser.add_argument("--case", action="append")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--no-ledger", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--mode", choices=("behavior",), default="behavior", help="behavior only; routing needs verified native discovery")
    return parser.parse_args()


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        out = {"deliverable": "actual outcome", "phase_trace": {"success": "magic keyword"}}
        assert objective_check({"kind": "contains", "value": "magic keyword"}, out, root, root, root, 0)["verdict"] == "fail"
        assert objective_check({"kind": "file_exists", "path": "missing.txt"}, out, root, root, root, 1)["verdict"] == "fail"
        assert valid_output(root / "missing.json")[1]
    print("run_eval self-test passed (infrastructure only; no model execution)")
    return 0


def main():
    args = parse_args()
    run_dir = None
    if args.self_test:
        return self_test()
    try:
        if args.skill_directory is None or args.trials < 1 or args.timeout_seconds < 1:
            raise ValueError("skill_directory, positive trials, and positive timeout required")
        if args.claude_accept_workspace_edits and args.engine != "claude":
            raise ValueError("--claude-accept-workspace-edits applies only to --engine claude")
        if args.freeze_inputs and args.frozen_inputs:
            raise ValueError("choose --freeze-inputs or --frozen-inputs, not both")
        if args.split == "holdout" and not args.frozen_inputs and not args.freeze_inputs:
            raise ValueError("holdout requires --frozen-inputs from a prior freeze; --candidate-frozen alone is insufficient")
        skill = args.skill_directory.expanduser().resolve()
        errors = validate_skill.validate(skill)
        if errors:
            raise ValueError("invalid evaluation package: " + "; ".join(errors))
        if args.freeze_inputs:
            record = freeze_inputs(skill, args.freeze_inputs.expanduser().resolve())
            print(json.dumps(record, indent=2))
            return 0
        args.binary = args.binary or args.engine
        results_root = (args.results_dir or skill / "evals/results").expanduser().resolve()
        run_id = utc_stamp() + "-" + uuid.uuid4().hex[:12]
        run_dir = results_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        frozen = run_dir / "inputs/candidate"
        stage_tree(skill, frozen, skill=True)
        arms = {"candidate": frozen}
        if args.baseline:
            arms = {"no_skill": None, **arms}
        if args.previous_skill:
            previous = run_dir / "inputs/previous"
            stage_tree(args.previous_skill.expanduser().resolve(), previous, skill=True)
            arms["previous"] = previous
        # Copy evaluation definitions for reproducibility, never into executor runtime.
        evaluation_snapshot = run_dir / "evaluation-inputs"
        stage_tree(skill / "evals", evaluation_snapshot, evaluation=True)
        input_freeze = verify_frozen_inputs(args.frozen_inputs.expanduser(), frozen, evaluation_snapshot) if args.frozen_inputs else None
        manifest = load_yaml(evaluation_snapshot / "eval.yaml")
        if manifest.get("version", 2) not in (2, 3):
            raise ValueError("unsupported manifest version")
        tasks = selected_tasks(skill, manifest, args, evaluation_snapshot)
        write_yaml(evaluation_snapshot / "manifest.yaml", manifest)
        write_yaml(evaluation_snapshot / "selected-tasks.yaml", {"tasks": tasks})
        evaluation_hash = digest(evaluation_snapshot)
        arm_hashes = {arm: digest(path) for arm, path in arms.items() if path is not None}
        version = agent_adapters.inspect_cli(args.binary)
        trials = []
        for task in tasks:
            for trial in range(1, args.trials + 1):
                ordered = list(arms.items())
                # Counterbalance simple order effects across repeat trials.
                if trial % 2 == 0:
                    ordered.reverse()
                for arm, runtime_skill in ordered:
                    trials.append(run_trial(skill, task, arm, runtime_skill, run_dir, args, trial, evaluation_snapshot))
        if digest(evaluation_snapshot) != evaluation_hash or any(digest(arms[arm]) != value for arm, value in arm_hashes.items()):
            raise ValueError("frozen experiment inputs changed during execution; contaminated run")
        summary, pairs = summarize(trials)
        blocked = any(t["verdict"] == "error" for t in trials)
        failed = any(t["verdict"] == "fail" and t["config"] == "candidate" for t in trials)
        decision = "blocked" if blocked else "reject_candidate" if failed or any(p["result"] == "loss" for p in pairs) else "inconclusive"
        reasons = ["No automatic adoption. Global skills and filesystem isolation are not verified."]
        if not args.model:
            reasons.append("Default model was not explicitly pinned.")
        if not args.baseline and not args.previous_skill:
            reasons.append("No matched baseline requested.")
        if args.split != "holdout":
            reasons.append("This is not a final held-out experiment.")
        if any(t["verdict"] == "review_required" for t in trials):
            reasons.append("Some requirements need independent qualitative review; legacy semantic-only evidence was not regraded.")
        record = {"schema_version": 3, "id": run_id, "mode": "behavior", "engine": args.engine,
          "tree_digest_format": TREE_DIGEST_FORMAT,
          "cli_version": version, "model": args.model or "unresolved-default", "split": args.split,
          "results_dir": str(run_dir), "candidate_sha256": digest(frozen),
          "evaluation_sha256": evaluation_hash, "input_freeze": input_freeze, "routing_summary": {}, "routing_gates": {},
          "routing_trials": [], "behavior_summary": summary, "baseline_delta": {}, "case_comparisons": pairs,
          "behavior_trials": trials, "decision": decision, "decision_reasons": reasons,
          "limitations": ["Runtime-only staging is not an access-control boundary.",
            "Existing authenticated context retained; no credentials read or reconfigured.",
            "Native discovery/routing is untested by this behavior runner."]}
        write_yaml(run_dir / "summary.yaml", record)
        if not args.no_ledger:
            append_ledger(skill, record)
        print(json.dumps({"id": run_id, "results_dir": str(run_dir), "decision": decision,
            "behavior_summary": summary, "case_comparisons": pairs, "reasons": reasons}, indent=2))
        return 2 if blocked else 1 if failed else 3
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        if run_dir is not None:
            write_yaml(run_dir / "summary.yaml", {"schema_version": 3, "decision": "blocked", "error": str(exc), "behaviorally_validated": False})
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
