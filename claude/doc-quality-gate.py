#!/usr/bin/env python3
"""Claude Code PostToolUse / Stop quality gate for Markdown and Design Docs.

Prevents repeated user correction turns by deterministically checking written or
edited Markdown files (especially Design Docs and READMEs) for:
1. Prohibited/unnatural phrasing ("失うもの", "使う人", "解決の道筋", vague headings
   like "作るもの" / "設計の柱").
2. Subjectless Goal bullets in Design Docs (e.g., "〜で、流れが一通りローカルで動く").
3. Code design sections missing concrete change-scenario impact checks.
4. Prompting a full top-to-bottom readback and terminology/metric consistency
   self-audit before reporting completion to the user.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile

WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}

PROHIBITED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"失うもの"),
        "「失うもの」という不自然な定型表現は使わず、「トレードオフ」または自然な文（「その代わり〜」）に直してください。",
    ),
    (
        re.compile(r"使う人"),
        "「使う人」という相対呼称は使わず、具体的な役割名（売場構成企画部の担当者、事業部の担当者、需要予測Gなど）を主語にしてください。",
    ),
    (
        re.compile(r"解決の道筋"),
        "「解決の道筋」という回りくどい表現は「解決策」に直してください。",
    ),
    (
        re.compile(r"^#{1,4}\s*(?:作るもの|設計の[0-9０-９一二三四五六七八九十]*つの柱|設計の柱)\s*$"),
        "「作るもの」「設計の柱」のような曖昧な見出しは避け、具体的な設計対象または標準見出しにしてください。",
    ),
]

SUBJECTLESS_GOAL_PATTERN = re.compile(
    r"(?:流れが一通り|一通りローカルで動く|ローカルで動くこと|動くようにする)\s*[。．]?$"
)
CHANGE_SCENARIO_HINT_PATTERN = re.compile(
    r"(?:変更シナリオ|変えるとき|変えたとき|増やしたとき|追加したとき|直す場所|変更箇所|波及)"
)
CODE_DESIGN_BAD_FILENAME_PATTERN = re.compile(
    r"(?:^|[\s/│├└─`])(model\.py|table\.py|apply\.py|recommend\.py|operations\.py|utils\.py|common\.py|helpers\.py)(?=$|[\s`#):])"
)


def is_target_markdown(file_path: str) -> bool:
    if not file_path:
        return False
    p = Path(file_path)
    if p.suffix.lower() != ".md":
        return False
    # Do not flag skill definition or agent instruction files that mention prohibited words as negative examples.
    parts = set(p.parts)
    if p.name in {"SKILL.md", "AGENTS.md", "CLAUDE.md", "APPEND_SYSTEM.md"}:
        return False
    if "skills" in parts or " skills-local" in file_path:
        return False
    return True


def is_design_doc(file_path: str, text: str) -> bool:
    name = Path(file_path).name.lower()
    if "design-doc" in name or "design_doc" in name:
        return True
    return "## Context and Scope" in text and "## Goals and Non-goals" in text


def strip_code_fences(lines: list[str]) -> list[tuple[int, str]]:
    in_fence = False
    visible: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            visible.append((idx, line))
    return visible


def extract_section(visible_lines: list[tuple[int, str]], heading_regex: str) -> list[tuple[int, str]]:
    pattern = re.compile(heading_regex, re.IGNORECASE)
    collecting = False
    level = 0
    collected: list[tuple[int, str]] = []
    for idx, line in visible_lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if m:
            cur_level = len(m.group(1))
            title = m.group(2).strip()
            if pattern.search(title):
                collecting = True
                level = cur_level
                continue
            if collecting and cur_level <= level:
                break
        elif collecting:
            collected.append((idx, line))
    return collected


def inspect_markdown(file_path: str, text: str) -> tuple[list[str], list[str]]:
    """Return (hard_violations, advisory_warnings)."""
    hard: list[str] = []
    advisory: list[str] = []
    lines = text.splitlines()
    visible = strip_code_fences(lines)

    for pattern, message in PROHIBITED_PATTERNS:
        matched_lines = [idx for idx, line in visible if pattern.search(line)]
        if matched_lines:
            line_refs = ", ".join(f"L{n}" for n in matched_lines[:5])
            hard.append(f"- {file_path} ({line_refs}): {message}")

    if is_design_doc(file_path, text):
        goals_lines = extract_section(visible, r"^Goals\b")
        for idx, line in goals_lines:
            s = line.strip()
            if not re.match(r"^(?:[-*]|\d+\.)\s+", s):
                continue
            if SUBJECTLESS_GOAL_PATTERN.search(s):
                hard.append(
                    f"- {file_path} (L{idx}): Goal に主語と達成状態がありません（「誰が・何を見て・何を判断/操作でき・どういう状態になるか」の文に直してください）。"
                )

        code_design_all_lines = extract_section(
            [(idx, line) for idx, line in enumerate(lines, 1)], r"^Code design\b"
        )
        if code_design_all_lines:
            bad_lines = [
                idx
                for idx, line in code_design_all_lines
                if CODE_DESIGN_BAD_FILENAME_PATTERN.search(line)
            ]
            if bad_lines:
                line_refs = ", ".join(f"L{n}" for n in bad_lines[:5])
                hard.append(
                    f"- {file_path} ({line_refs}): Code design に多義語・曖昧語・動詞1つの細切れファイル名（model.py, table.py, apply.py, recommend.py, operations.py, utils.py 等）が含まれています。共有データモデルは models/<概念名>.py に集約し、専門モジュールと use_cases/ を分け、リーダブルコードの命名原則に沿った具体的な名前へ直してください。"
                )
            code_design_text = "\n".join(line for _, line in code_design_all_lines)
            if not CHANGE_SCENARIO_HINT_PATTERN.search(code_design_text):
                advisory.append(
                    f"- {file_path} (Code design): 代表的な変更シナリオ（モデル追加、画面変更、予測対象追加、最適化手法追加、データソース変更、保存項目追加、ログ追加など）ごとに「どのモジュールだけを変更すれば済むか」の検証が見当たりません。密結合を防ぐため変更局所性を確認してください。"
                )

    return hard, advisory


def state_file_for_session(session_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "default")
    return Path(tempfile.gettempdir()) / f"claude-doc-quality-gate-{safe_id}.json"


def load_session_state(session_id: str) -> dict:
    path = state_file_for_session(session_id)
    if not path.exists():
        return {"touched_files": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"touched_files": []}


def save_session_state(session_id: str, state: dict) -> None:
    path = state_file_for_session(session_id)
    try:
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def handle_post_tool_use(payload: dict) -> dict:
    tool_name = str(payload.get("tool_name") or "")
    if tool_name not in WRITE_TOOLS:
        return {}
    tool_input = payload.get("tool_input") or {}
    raw_path = str(tool_input.get("file_path") or tool_input.get("path") or "").strip()
    if not raw_path:
        return {}
    cwd = str(payload.get("cwd") or os.getcwd())
    abs_path = str(Path(raw_path) if Path(raw_path).is_absolute() else (Path(cwd) / raw_path).resolve())
    if not is_target_markdown(abs_path) or not Path(abs_path).is_file():
        return {}

    session_id = str(payload.get("session_id") or "")
    if session_id:
        state = load_session_state(session_id)
        touched = state.get("touched_files") or []
        if abs_path not in touched:
            touched.append(abs_path)
        state["touched_files"] = touched
        save_session_state(session_id, state)

    try:
        text = Path(abs_path).read_text(encoding="utf-8")
    except Exception:
        return {}

    hard, advisory = inspect_markdown(abs_path, text)
    checklist = [
        "[DOCUMENT SELF-REVIEW GATE]",
        f"Markdown 文書 ({Path(abs_path).name}) を更新しました。ユーザーへ完了報告する前に必ず以下を実施してください：",
        "1. 書き出したファイル全文を頭から読み直し、すべての文（特に Goals や箇条書き）に「誰が・何に対して・何をする（どうなる）か」の主語と述語があるか確かめる。",
        "2. 元の分析の指標（売上・粗利・客数の区別）や用語（DPT と部門、最適化と計算など）に揺れや節間の前後矛盾がないか確かめる。",
        "3. 頼まれていない将来の拡張案（エンハンス）や根拠のない社内手続き確認が紛れ込んでいないか確かめる。",
    ]
    if hard or advisory:
        checklist.append("\n検出された要修正箇所：")
        checklist.extend(hard)
        checklist.extend(advisory)

    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(checklist),
        }
    }


def handle_stop(payload: dict) -> dict:
    if payload.get("stop_hook_active"):
        return {}
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return {}
    state = load_session_state(session_id)
    touched = state.get("touched_files") or []
    if not touched:
        return {}

    all_hard: list[str] = []
    for file_path in touched:
        if not is_target_markdown(file_path) or not Path(file_path).is_file():
            continue
        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except Exception:
            continue
        hard, _ = inspect_markdown(file_path, text)
        all_hard.extend(hard)

    if not all_hard:
        return {}

    reason = (
        "[DOCUMENT QUALITY GATE]\n"
        "作成・編集した Markdown 文書に、手戻りの原因になる禁止表現または主語欠落が残っています。ユーザーに完了報告する前に修正してください：\n"
        + "\n".join(all_hard)
    )
    return {
        "decision": "block",
        "reason": reason,
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
    except Exception:
        print("{}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
