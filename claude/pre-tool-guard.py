#!/usr/bin/env python3
"""Claude Code PreToolUse guard for Vertex WebSearch, tk CLI syntax, and live main edits.

1. Blocks Claude Code's built-in WebSearch (which fails with HTTP 400 under the
   Vertex AI organization policy) and directs the agent to delegate search to Pi.
2. Catches invalid `tk` CLI subcommands and missing required flags before execution
   so task logging/recording succeeds in one call.
3. Prompts for confirmation (`ask`) when editing application code directly on a
   primary `main` / `master` checkout while a local dev server for that repo is listening.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}
CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".css", ".scss", ".sql", ".vue", ".svelte", ".rs", ".go",
}
DEV_PORTS = {"3000", "3001", "5173", "5174", "8000", "8001", "8080"}


def decision_output(decision: str, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def find_tk_command_error(command: str) -> str | None:
    if not re.search(r"\btk\b", command):
        return None
    normalized = command.replace("\\\n", " ").strip()
    segments = [s.strip() for s in re.split(r"(?:&&|\|\||;|\n|\$\()", normalized) if s.strip()]
    for seg in segments:
        m = re.match(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*tk\s+(.+)$", seg)
        if not m:
            continue
        rest = m.group(1).strip()
        if not rest or re.search(r"(?:^|\s)(?:--help|-h)(?:\s|$)", rest):
            continue

        if re.match(r"^(?:task|tasks)\b", rest):
            return (
                "Invalid tk command (`tk task` does not exist). Use `tk list --all`, "
                "`tk show <id> --logs 8`, `tk add \"Title\" --description \"...\"`, or "
                "`tk status <id> <todo|in_progress|in_review|done>`."
            )
        if re.match(r"^records\b", rest):
            return (
                "Invalid tk command (`tk records` does not exist). Use "
                "`tk record list <task_id> [--kind X] [--all]` or "
                "`tk record add <task_id> --kind <kind> --log-id <log_id> --summary \"...\"`."
            )
        if re.match(r"^(?:finding|decision|blocker|question|hypothesis)\b", rest):
            return (
                "Invalid tk subcommand. Use `tk record add <task_id> "
                "--kind <decision|finding|blocker|question|hypothesis> --log-id <log_id> "
                "--summary \"...\" [--details \"...\"]`."
            )
        if re.match(r"^record\s+supersede\b", rest):
            return (
                "Invalid tk command (`tk record supersede` does not exist). Supersede by adding a "
                "new record with `--supersedes <old_record_id>`: `tk record add <task_id> "
                "--kind <kind> --log-id <log_id> --supersedes <old_record_id> --summary \"...\"`."
            )
        if re.match(r"^record\s+list\b", rest) and re.search(r"(?:^|\s)--limit\b", rest):
            return (
                "Invalid tk option (`tk record list` does not accept `--limit`). "
                "Use `tk record list <task_id> [--kind X] [--all]`."
            )
        if re.match(r"^record\s+add\b", rest):
            if re.search(r"(?:^|\s)--title\b", rest):
                return (
                    "Invalid tk option (`tk record add` uses `--summary`, not `--title`). "
                    "Syntax: `tk record add <task_id> --kind <decision|finding|blocker|question|hypothesis> "
                    "--log-id <log_id> --summary \"...\" [--details \"...\"]`."
                )
            missing = [
                opt
                for opt in ("--kind", "--log-id", "--summary")
                if not re.search(rf"(?:^|\s){re.escape(opt)}(?:\s|=)", rest)
            ]
            if missing:
                return (
                    f"Missing required option(s) {', '.join(missing)} for `tk record add`. "
                    "First create/get a log ID via `tk log <task_id> --summary \"...\"`, then run: "
                    "`tk record add <task_id> --kind <decision|finding|blocker|question|hypothesis> "
                    "--log-id <log_id> --summary \"...\" [--details \"...\"]`."
                )
        if re.match(r"^log\b(?!-|s\b)", rest) and not re.search(r"(?:^|\s)--summary(?:\s|=)", rest):
            return (
                "Missing required option `--summary` for `tk log`. "
                "Syntax: `tk log <task_id> --summary \"...\" [--details \"...\"] [--next-action \"...\"]`."
            )
        if re.match(r"^add\b", rest) and not re.search(r"(?:^|\s)--description(?:\s|=)", rest):
            return (
                "Missing required option `--description` for `tk add`. "
                "Syntax: `tk add \"Title\" --description \"目的：...\\n前提：...\\n完了条件：...\\nNon Goal：...\" [--parent <id>]`."
            )
    return None


def is_protected_code_path(abs_path: Path) -> bool:
    if abs_path.suffix.lower() not in CODE_EXTENSIONS:
        return False
    s = str(abs_path)
    excluded = ("/dotfiles/", "/memo/", "/docs/", "/.pi/", "/.claude/")
    if any(token in s or s.startswith(token.rstrip("/")) for token in excluded):
        return False
    return not re.search(r"^(?:/private)?/tmp/", s)


def detect_live_main_repo(abs_path: Path, cwd: str) -> tuple[str, str, list[str]] | None:
    if not is_protected_code_path(abs_path):
        return None
    target_dir = abs_path.parent if abs_path.parent.exists() else Path(cwd)
    try:
        git_out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD", "--git-dir", "--git-common-dir"],
            cwd=str(target_dir),
            text=True,
            capture_output=True,
            timeout=1.2,
            check=True,
        ).stdout.strip().splitlines()
        if len(git_out) < 4:
            return None
        repo_root, branch, git_dir, git_common_dir = git_out[:4]
        if branch not in {"main", "master"}:
            return None
        if (target_dir / git_dir).resolve() != (target_dir / git_common_dir).resolve():
            return None

        lsof_listen = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn"],
            text=True,
            capture_output=True,
            timeout=1.5,
            check=False,
        ).stdout
        pid_ports: dict[str, set[str]] = {}
        cur_pid = ""
        for line in lsof_listen.splitlines():
            if line.startswith("p"):
                cur_pid = line[1:]
            elif line.startswith("n") and cur_pid:
                m = re.search(r":(\d+)$", line)
                if m and m.group(1) in DEV_PORTS:
                    pid_ports.setdefault(cur_pid, set()).add(f":{m.group(1)}")
        if not pid_ports:
            return None

        cwd_out = subprocess.run(
            ["lsof", "-a", "-p", ",".join(pid_ports.keys()), "-d", "cwd", "-Fn"],
            text=True,
            capture_output=True,
            timeout=1.5,
            check=False,
        ).stdout
        repo_root_path = Path(repo_root).resolve()
        matched_ports: set[str] = set()
        cur_pid = ""
        for line in cwd_out.splitlines():
            if line.startswith("p"):
                cur_pid = line[1:]
            elif line.startswith("n") and cur_pid:
                proc_cwd = Path(line[1:]).resolve()
                if proc_cwd == repo_root_path or repo_root_path in proc_cwd.parents:
                    matched_ports.update(pid_ports.get(cur_pid, set()))
        if not matched_ports:
            return None
        return repo_root, branch, sorted(matched_ports)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def handle_pre_tool_use(payload: dict) -> dict:
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    cwd = str(payload.get("cwd") or os.getcwd())

    if tool_name == "WebSearch":
        return decision_output(
            "deny",
            (
                "Vertex AI の組織ポリシーにより Claude Code の組み込み WebSearch は HTTP 400 エラーになります。"
                "代わりに Bash ツールで次のコマンドを実行して検索・本文確認を行ってください：\n"
                'pi -p --no-session --model google-vertex/gemini-3.8-flash:low -t web_search,web_fetch "<検索と確認の依頼>" < /dev/null\n'
                "（学術論文や深い技術調査では --model google-vertex/gemini-4-hydrogen を使用してください）"
            ),
        )

    if tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        tk_err = find_tk_command_error(command)
        if tk_err:
            return decision_output("deny", tk_err)
        return {}

    if tool_name in WRITE_TOOLS and os.environ.get("ALLOW_MAIN_LIVE_EDIT") != "1":
        raw_path = str(tool_input.get("file_path") or tool_input.get("path") or "").strip()
        if not raw_path:
            return {}
        abs_path = Path(raw_path) if Path(raw_path).is_absolute() else (Path(cwd) / raw_path).resolve()
        live = detect_live_main_repo(abs_path, cwd)
        if live:
            repo_root, branch, ports = live
            return decision_output(
                "ask",
                (
                    f"ローカルサーバー ({', '.join(ports)}) が稼働中の {branch} ブランチ ({repo_root}) 上で"
                    "コードファイルを直接編集しようとしています。デモ環境を保護する場合は拒否して "
                    "Orca worktree（orca-cli）または作業ブランチで作業してください。"
                ),
            )
    return {}


def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print("{}")
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            print("{}")
            return 0
        if payload.get("hook_event_name") == "PreToolUse":
            out = handle_pre_tool_use(payload)
        else:
            out = {}
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError):
        print("{}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
