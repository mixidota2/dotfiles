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
MCP servers, or global `CLAUDE.md` are installed by this repository.

## Skills

The six directories in `skills-local/.codex/skills` are the shared source of truth.
The installer links each directory separately into `~/.claude/skills`, a
[personal skill location supported by Claude Code](https://code.claude.com/docs/en/skills).
Individual directory symlinks are supported. New local skill directories with a
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

- `create-validated-skill` intentionally targets Codex skills. Its evaluation
  scripts use `codex exec` and need a separately installed/authenticated Codex CLI
  plus Python/PyYAML. Linking it does not provide Claude-native model evaluation.
- `pi-extension-development` requires the target Pi environment and behavior
  tests; `herdr` requires Herdr for actual operations.
- `eli5`, `design-python-code`, and `sanitize-artifacts` primarily contain
  reusable guidance; tool availability still depends on the running agent.

Installation tests validate links, metadata, backups, and command arguments.
They do not claim end-to-end Claude runtime/model validation or perform login.
