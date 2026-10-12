# Agent execution contract

Use a fresh process per task/arm/trial, with the same user scenario and input
fixture. Do not continue a prior measured conversation. Corrective follow-ups are
diagnostic work, not new independent trials.

## Existing authentication only

The runner invokes an already installed, authenticated official `codex` or
`claude` executable. It never logs in, installs a CLI, reads credentials, links or
copies authentication, changes account settings, or configures persistent access.
Set up the runtime separately under the user's normal authorization. If startup
fails, record `blocked`; do not report an agent behavior result.

Codex uses `exec --ephemeral --json --output-schema`, an output file, and read-only
or workspace-write sandbox according to the fixture contract. Per-run log/database
paths do not make a read-only CODEX_HOME usable in every version. Claude uses
`-p --output-format stream-json --verbose --json-schema`, no session persistence,
explicit tool availability, and default permissions. It does not bypass approval.
Claude writable trials are blocked by default: tool availability is not
permission. With explicit caller approval, `--claude-accept-workspace-edits` uses
per-run `acceptEdits` only in the disposable working directory, exposing Read,
Glob, Grep, Write, and Edit. It exposes no shell, network, or MCP tool and sets no
additional writable directories. MCP is disabled with strict empty server config
and `--disallowedTools mcp__*`; `--tools` alone only restricts built-ins.
This mode cannot run shell tests inside the
agent; an external trusted grader checks the resulting files. No persistent
permission settings, broad allowlist, credential, or bypass mode is changed.
Both Claude modes remain runtime-unverified until the installed CLI is exercised.
Timeout cleanup signals only the process group created by the runner (POSIX).

See official protocols:
- https://learn.chatgpt.com/docs/cli/reference
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/permissions

## Staging and its limits

Only SKILL.md, references, scripts, assets, and agents are staged for the measured
skill. Symlinked runtime inputs are rejected. Evaluation definitions, expected
outputs, and ledger are not staged beside it. Each arm gets a fresh fixture copy.
The executor returns an actual deliverable, with blocked work disclosed; it is
not asked to grade itself or fill a ceremonial phase checklist.

This is data hygiene, not an OS confidentiality boundary. Existing global skills,
plugins, instructions, and readable parent directories can contaminate an arm.
The runner records that limitation and does not automatically adopt a skill.
`--ignore-user-config` and disabled project instructions do not disable every
Codex skill source. Claude configuration-source restrictions also do not prove
filesystem isolation. Never claim a clean baseline unless independently verified.

## External grading

The parent runner applies objective checks to output/files after the process
completes. `command` graders are trusted repository test code, run without a shell,
and must not contain unapproved external actions. They must not derive executable
commands from model output. Keep expected data and graders outside visible
fixtures. The sandbox applies to the agent, not these external test commands.

Native routing is a separate manually designed experiment until discovery is
verified in the selected CLI/runtime. Use positive, negative, near-miss, and sibling
competition prompts without explicit skill names. Only successful observed file
reads count as read evidence; do not mistake the runner's behavior-loading clause
for automatic discovery.
