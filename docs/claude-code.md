# Claude Code

## Installation

On macOS with Homebrew, run `./install.sh --tools`. The Brewfile includes the
[official `claude-code` cask](https://code.claude.com/docs/en/setup#install-claude-code).
Homebrew manages this installation; update with `brew upgrade claude-code`.
The Brewfile also installs `node`, which provides npm/npx for the external tk
skill installer. Native Claude Code itself does not require Node.js.
If Claude Code was installed through npm or the native installer, inspect
`claude doctor` first and choose one installation method rather than keeping
multiple copies on PATH.

`./install.sh` alone only links configuration and local skills. `--dry-run --tools`
prints the Brewfile command without downloading or installing tools.
The Brewfile is macOS-oriented; it is not a Linux/Windows installer. For Linux,
WSL, or native Windows, use the platform's [official installation instructions](https://code.claude.com/docs/en/setup).
The shell link installer is tested independently on Unix systems.

Check `claude --version` and `claude doctor`, then start `claude` and sign in
interactively yourself. No credentials, account settings, permissions, hooks,
MCP servers, or global `CLAUDE.md` are installed by this repository; only the
settings keys described below are merged.

## Global instructions

The installer links `~/.claude/AGENTS.md` to the same `codex/.codex/AGENTS.md`
that Codex uses. Claude Code has no user-level `AGENTS.md` location; it reads
[`AGENTS.md` and `.claude/AGENTS.md` in the working directory and the directories
above it](https://code.claude.com/docs/en/memory#when-claude-code-reads-agents-md),
so this file applies when you work under `$HOME`. It requires Claude Code
v2.1.277 or later.

By default Claude Code reads `AGENTS.md` only when there is no `CLAUDE.md`,
`.claude/CLAUDE.md`, or `CLAUDE.local.md` in the working directory or above it.
`claude/settings-additions.json` sets
[**Project instructions** to `claude-md-and-agents-md`](https://code.claude.com/docs/en/memory#choose-which-instruction-files-load)
so the global instructions also load in repositories that have a `CLAUDE.md`.

## Settings

`~/.claude/settings.json` is not linked: Claude Code and other apps (such as
Orca's hooks) write their own settings there. The installer merges only the keys
in `claude/settings-additions.json` into it with `claude/merge_settings.py`,
keeping every other key, and rewrites the file only when a value changes. A file
that is not a JSON object is left untouched with a warning. Merging needs
`python3`; without it the installer prints the file to merge by hand. To manage
another setting here, add its key to `claude/settings-additions.json`.

## Skills

The six directories in `skills-local/.codex/skills` are the shared source of truth.
The installer links each directory separately into `~/.claude/skills`, a
[personal skill location supported by Claude Code](https://code.claude.com/docs/en/skills).
Individual directory symlinks are supported. Unmarked local skill directories with a
`SKILL.md` are picked up when you rerun `./install.sh`; edits to existing linked
skills follow the checkout immediately. Keep the checkout at a stable location,
or rerun the installer after moving it.

Both installers stop before changing files if `~/.claude/skills` itself is a
symlink. Inspect that target and migrate to a real skills directory with
individual skill links first; this prevents writes into another app or checkout.
Other skills and Claude's `synced` directory are left in place. A same-name local
skill is moved to the installer's unique backup path before linking; review local
customizations there. Already-correct links are left unchanged. If you remove a
skill from the repository, inspect and remove its old link yourself; the installer
does not delete skills. Claude settings and authentication are never linked.

Restore or update the external tk skill with `./skills/install.sh`. It uses the
standalone [tasukura-skills repository](https://github.com/mixidota2/tasukura-skills)
and explicitly targets both `codex` and `claude-code`, using the skills CLI's
agent-specific paths. Existing Claude tk directories or links are backed up
outside the active skill directory before replacement, with unique backup names;
relative link targets are preserved. If downloading fails, the previous Claude tk
skill is restored and any partial replacement is preserved at a separate printed
backup path for inspection. The task database is not changed.

Start a new Claude Code session and check `/skills`. Invoke `/tk`, `/eli5 topic`,
`/design-python-code`, `/sanitize-artifacts`, `/herdr`,
`/pi-extension-development`, or `/create-validated-skill` as appropriate.
These are Claude Code slash commands; the `$skill-name` examples and statement
that `/eli5` is not a command in the shared instructions describe Codex only.

All six local skills have standard `name` and `description` frontmatter, but
being discoverable does not guarantee every workflow is agent-independent:

- `create-validated-skill` shares its creation workflow and evaluation manifests
  across both agents. Its runner has separate `--engine codex` and `--engine claude`
  adapters and needs Python/PyYAML plus the selected, already authenticated CLI.
  Package compatibility and adapter unit tests do not establish real Claude model
  validation. Claude writes are blocked by default. Explicit
  `--claude-accept-workspace-edits` enables files-only edits in disposable trial
  workspaces using per-run `acceptEdits`; no shell tools or permission bypass. The runner never configures login or copies credentials. See the
  [evaluation protocol](../skills-local/.codex/skills/create-validated-skill/references/evaluation-schema.md).
- `pi-extension-development` requires the target Pi environment and behavior
  tests; `herdr` requires Herdr for actual operations.
- `eli5`, `design-python-code`, and `sanitize-artifacts` primarily contain
  reusable guidance; tool availability still depends on the running agent.

Installation tests validate links, metadata, backups, and command arguments.
They do not claim end-to-end Claude runtime/model validation or perform login.
