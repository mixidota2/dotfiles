#!/usr/bin/env python3
"""Claude Code PostToolUse / Stop verification gate for Backend, Frontend, and Visual HTML.

Tracks code and visual-HTML mutations per Claude session and blocks premature Stop
when the modified stack (backend Python, frontend TS/TSX/CSS, or visual HTML diagram)
has not been verified after its latest edit.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}

BACKEND_VERIFY_PATTERNS = [
    re.compile(r"(?:^|[;&|]\s*)(?:uv\s+run\s+(?:--\S+(?:\s+\S+)?\s+)*|uvx\s+)?(?:python(?:3)?\s+(?:-[A-Za-z]+\s+)*-m\s+)?(?:pytest|unittest)\b", re.IGNORECASE),
    re.compile(r"(?:^|[;&|]\s*)(?:uv\s+run\s+(?:--\S+(?:\s+\S+)?\s+)*|uvx\s+|npx\s+)?(?:ruff\s+check|ty\s+check|mypy|pyright)\b", re.IGNORECASE),
]
FRONTEND_VERIFY_PATTERNS = [
    re.compile(r"(?:^|[;&|]\s*)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|check|lint|typecheck|build)\b", re.IGNORECASE),
    re.compile(r"(?:^|[;&|]\s*)(?:npx|pnpm\s+exec|bunx)\s+(?:vitest|jest|tsc|eslint|(?:@biomejs/)?biome|next\s+build|vite\s+build)\b", re.IGNORECASE),
    re.compile(r"(?:^|[;&|]\s*)(?:vitest|jest|eslint|tsc\b|biome\s+(?:check|ci|lint))\b", re.IGNORECASE),
]
VISUAL_HTML_VERIFY_PATTERNS = [
    re.compile(r"--headless\b[^\n]*--screenshot\b", re.IGNORECASE),
    re.compile(r"\b(?:check_all\.py|check_deck\.py|check_layout\.mjs|snapshot\.py)\b", re.IGNORECASE),
    re.compile(r"\b(?:playwright|puppeteer)\b[^\n]*(?:screenshot|pdf|test)", re.IGNORECASE),
]


def state_file_for_session(session_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "default")
    return Path(tempfile.gettempdir()) / f"claude-code-verify-gate-{safe_id}.json"


def load_state(session_id: str) -> dict:
    path = state_file_for_session(session_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {
        "seq": 0,
        "dirty": {"backend": 0, "frontend": 0, "visual_html": 0},
        "verified": {"backend": 0, "frontend": 0, "visual_html": 0},
        "files": {"backend": [], "frontend": [], "visual_html": []},
        "reminded_seq": 0,
    }


def save_state(session_id: str, state: dict) -> None:
    if not session_id:
        return
    try:
        state_file_for_session(session_id).write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        return


def classify_path(abs_path: str) -> str | None:
    p = Path(abs_path)
    ext = p.suffix.lower()
    norm = abs_path.replace("\\", "/")
    if any(seg in norm for seg in ("/node_modules/", "/.git/")) or re.search(r"/(?:private/)?tmp/", norm):
        return None
    if ext == ".html":
        if p.name.lower() in {"template.html", "index.html"} or "/memo/retail-news/" in norm:
            return None
        if "/memo/" in norm or "/docs/" in norm:
            return "visual_html"
        return None
    if ext == ".py" or p.name == "pyproject.toml":
        return "backend"
    if ext in {".ts", ".tsx", ".js", ".jsx", ".css", ".scss", ".vue", ".svelte"}:
        if "/.pi/agent/extensions/" in norm:
            return None
        return "frontend"
    return None


def classify_bash_verification(command: str) -> list[str]:
    norm = re.sub(r"\s+", " ", command.replace("\\\n", " ")).strip()
    if not norm:
        return []
    matched: list[str] = []
    if any(p.search(norm) for p in BACKEND_VERIFY_PATTERNS):
        matched.append("backend")
    if any(p.search(norm) for p in FRONTEND_VERIFY_PATTERNS):
        matched.append("frontend")
    if any(p.search(norm) for p in VISUAL_HTML_VERIFY_PATTERNS):
        matched.append("visual_html")
    return matched


def bash_response_succeeded(tool_response: object) -> bool:
    if isinstance(tool_response, dict):
        for key in ("exit_code", "exitCode", "returncode"):
            val = tool_response.get(key)
            if isinstance(val, int) and val != 0:
                return False
        if tool_response.get("interrupted") is True:
            return False
    return True


def as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def handle_post_tool_use(payload: dict) -> dict:
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return {}
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    cwd = str(payload.get("cwd") or os.getcwd())

    if tool_name in WRITE_TOOLS:
        raw_path = str(tool_input.get("file_path") or tool_input.get("path") or "").strip()
        if not raw_path:
            return {}
        abs_path = str(Path(raw_path) if Path(raw_path).is_absolute() else (Path(cwd) / raw_path).resolve())
        domain = classify_path(abs_path)
        if not domain:
            return {}
        state = load_state(session_id)
        seq = as_int(state.get("seq")) + 1
        state["seq"] = seq
        state.setdefault("dirty", {})[domain] = seq
        files = state.setdefault("files", {}).setdefault(domain, [])
        if abs_path not in files:
            files.append(abs_path)
        save_state(session_id, state)
        return {}

    if tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        domains = classify_bash_verification(command)
        if not domains or not bash_response_succeeded(payload.get("tool_response")):
            return {}
        state = load_state(session_id)
        seq = as_int(state.get("seq"))
        for d in domains:
            state.setdefault("verified", {})[d] = seq
        save_state(session_id, state)
    return {}


def handle_stop(payload: dict) -> dict:
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return {}
    state = load_state(session_id)
    dirty = state.get("dirty") or {}
    verified = state.get("verified") or {}
    files = state.get("files") or {}

    unverified: list[str] = [
        d for d in ("backend", "frontend", "visual_html")
        if as_int(dirty.get(d)) > as_int(verified.get(d))
    ]
    if not unverified:
        return {}

    max_dirty = max(as_int(dirty.get(d)) for d in unverified)
    if payload.get("stop_hook_active") and as_int(state.get("reminded_seq")) >= max_dirty:
        return {}

    state["reminded_seq"] = max_dirty
    save_state(session_id, state)

    bullets: list[str] = []
    if "backend" in unverified:
        sample = ", ".join(Path(p).name for p in (files.get("backend") or [])[-4:])
        bullets.append(
            f"- Backend Python ({sample}): 最後の編集以降にテスト・静的解析が未実行です（`uv run pytest`、`ruff check`、`ty check` 等を実行してください）。"
        )
    if "frontend" in unverified:
        sample = ", ".join(Path(p).name for p in (files.get("frontend") or [])[-4:])
        bullets.append(
            f"- Frontend ({sample}): 最後の編集以降にフロントエンド検証が未実行です（`vitest` / `npm test`、`tsc` / `biome check`、または `npm run build` を実行してください）。"
        )
    if "visual_html" in unverified:
        sample = ", ".join(Path(p).name for p in (files.get("visual_html") or [])[-4:])
        bullets.append(
            f"- Visual HTML ({sample}): 最後の編集の後に再描画確認が行われていません。修正後の崩れ・重なりを残さないよう、必ず最後の編集の後にヘッドレスブラウザのスクリーンショット撮影や `check_all.py` で描画を確認してから完了報告してください。"
        )

    return {
        "decision": "block",
        "reason": "[CODE & RENDER VERIFICATION GATE]\n未検証の変更が残っています。完了報告の前に以下を実行して通過を確認してください：\n" + "\n".join(bullets),
    }


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
        event = str(payload.get("hook_event_name") or "")
        if event == "PostToolUse":
            out = handle_post_tool_use(payload)
        elif event == "Stop":
            out = handle_stop(payload)
        else:
            out = {}
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError):
        print("{}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
