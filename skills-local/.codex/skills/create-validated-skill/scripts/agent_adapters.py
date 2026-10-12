"""CLI-specific transport. Never creates, reads, copies, or links credentials."""
from __future__ import annotations

import json
import os
import signal
from pathlib import Path
import subprocess
import time


def events(path):
    if not path.is_file():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def mapping(value):
    return value if isinstance(value, dict) else {}


def content(event):
    value = mapping(event.get("message")).get("content")
    return value if isinstance(value, list) else []


def inspect_cli(binary):
    """Help/version only; never attempts login or installs a missing runtime."""
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f"{binary} --version failed: {result.stderr.strip()}")
    return result.stdout.strip()


def command(engine, binary, workspace, output, schema, model, writable, runtime, claude_accept_workspace_edits=False):
    if engine == "codex":
        cmd = [binary, "exec", "--ephemeral", "--json", "--color", "never",
               "--skip-git-repo-check", "--ignore-user-config", "--sandbox",
               "workspace-write" if writable else "read-only", "--output-schema", str(schema),
               "--output-last-message", str(output), "--cd", str(workspace),
               "-c", "project_doc_max_bytes=0", "-c", f'sqlite_home={json.dumps(str(runtime / "sqlite"))}',
               "-c", f'log_dir={json.dumps(str(runtime / "logs"))}']
        if model:
            cmd += ["--model", model]
        return cmd + ["-"]
    if engine == "claude":
        # Official headless JSON protocol. --tools restricts capabilities; it is
        # deliberately not --allowedTools or a permission-bypass mode.
        cmd = [binary, "-p", "--output-format", "stream-json", "--verbose",
               "--no-session-persistence", "--setting-sources", "",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--disallowedTools", "mcp__*",
               "--permission-mode", "acceptEdits" if writable and claude_accept_workspace_edits else "default", "--tools",
               "Read,Glob,Grep,Write,Edit" if writable and claude_accept_workspace_edits else "Read,Glob,Grep",
               "--json-schema", schema.read_text(encoding="utf-8")]
        if model:
            cmd += ["--model", model]
        return cmd
    raise ValueError(f"unsupported engine: {engine}")


def run_process(cmd, *, prompt, workspace, stdout, timeout):
    """Terminate only the process group created for this invocation on timeout."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=stdout,
        stderr=subprocess.PIPE, text=True, cwd=workspace, env=os.environ.copy(),
        start_new_session=(os.name == "posix"))
    try:
        _, error = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            proc.terminate()
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                # An escaped descendant may retain a pipe. Do not wait forever
                # or signal a process group this invocation does not own.
                if proc.stderr:
                    proc.stderr.close()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stderr=error)


def execute(*, engine, binary, prompt, workspace, schema, output, trace, stderr,
            model, writable, timeout, runtime, claude_accept_workspace_edits=False):
    runtime.mkdir(parents=True, exist_ok=True)
    if engine == "claude" and writable and not claude_accept_workspace_edits:
        trace.write_text("", encoding="utf-8")
        stderr.write_text("Claude writable trial blocked: explicitly approve edits in the disposable workspace with --claude-accept-workspace-edits. No permissions were changed.", encoding="utf-8")
        return {"exit_code": 78, "command": [], "duration_ms": 0, "usage": {}, "tool_uses": 0,
                "resolved_model": None, "isolation": "capability blocked before execution"}
    cmd = command(engine, binary, workspace, output, schema, model, writable, runtime, claude_accept_workspace_edits)
    started = time.perf_counter()
    code = 127
    error = ""
    # Keep the current authenticated context. No CODEX_HOME/HOME reconfiguration.
    with trace.open("w", encoding="utf-8") as stream:
        try:
            proc = run_process(cmd, prompt=prompt, workspace=workspace, stdout=stream, timeout=timeout)
            code, error = proc.returncode, proc.stderr
        except subprocess.TimeoutExpired:
            code, error = 124, f"executor timed out after {timeout}s"
        except OSError as exc:
            error = str(exc)
    stderr.write_text(error, encoding="utf-8")
    parsed = events(trace)
    if engine == "claude":
        terminal = [event for event in parsed if event.get("type") == "result"]
        if terminal:
            result = terminal[-1]
            if result.get("is_error"):
                code = code or 1
            value = result.get("structured_output")
            if isinstance(value, dict):
                output.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    usage = {}
    tool_count = 0
    resolved_model = None
    for event in parsed:
        if engine == "codex":
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
            item = mapping(event.get("item"))
            if event.get("type") == "item.completed" and item.get("type") in {"command_execution", "mcp_tool_call", "web_search"}:
                tool_count += 1
        else:
            if event.get("type") == "system":
                resolved_model = event.get("model", resolved_model)
            if event.get("type") == "result":
                usage = event.get("usage", {})
            for item in content(event):
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    tool_count += 1
    complete = any(e.get("type") == ("turn.completed" if engine == "codex" else "result") and not e.get("is_error") for e in parsed)
    if any(e.get("type") in {"turn.failed", "error"} for e in parsed):
        code = code or 65
    if code == 0 and not complete:
        code = 65
        stderr.write_text(error + "\nMissing completed agent turn event", encoding="utf-8")
    return {"exit_code": code, "command": cmd, "duration_ms": round((time.perf_counter()-started)*1000),
            "usage": usage, "tool_uses": tool_count, "resolved_model": resolved_model,
            "isolation": "fresh process and staged runtime files; filesystem secrecy and global skill exclusion unverified"}
