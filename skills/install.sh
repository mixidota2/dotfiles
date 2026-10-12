#!/bin/sh
set -eu

if ! command -v npx >/dev/null 2>&1; then
  printf 'npx is required to install external skills.\n' >&2
  exit 1
fi

if [ -L "$HOME/.claude/skills" ]; then
  printf 'Claude skills root is a symlink: %s. Inspect it and use a real skills directory before installing.\n' "$HOME/.claude/skills" >&2
  exit 1
fi

move_preserving_link() (
  source=$1
  destination=$2
  if [ -L "$source" ]; then
    target=$(readlink "$source")
    case "$target" in
      /*) ;;
      *) target=$(dirname "$source")/$target ;;
    esac
    ln -s "$target" "$destination"
    rm "$source"
  else
    mv "$source" "$destination"
  fi
)

backup=
# Keep existing Claude customizations outside its active skill search path before
# npx replaces tk. Do not let --copy write through an existing directory symlink.
claude_tk=$HOME/.claude/skills/tk
if [ -e "$claude_tk" ] || [ -L "$claude_tk" ]; then
  timestamp=$(date '+%Y%m%d-%H%M%S')
  backup_root=${DOTFILES_BACKUP_DIR:-"$HOME/.dotfiles-backups/$timestamp"}
  backup=$backup_root/.claude/skills/tk
  suffix=0
  while [ -e "$backup" ] || [ -L "$backup" ]; do
    suffix=$((suffix + 1))
    backup=$backup_root/.claude/skills/tk.$suffix
  done
  mkdir -p "$(dirname "$backup")"
  move_preserving_link "$claude_tk" "$backup"
  printf 'backup  %s -> %s\n' "$claude_tk" "$backup"
fi

# Explicit agent names avoid relying on Codex's changing legacy/global paths.
# tk usage instructions live in the standalone skills repository.
if npx skills add mixidota2/tasukura-skills \
  --global --agent codex claude-code --copy --yes --skill tk; then
  printf 'tk skill installed for Codex and Claude Code.\n'
else
  status=$?
  if [ -n "$backup" ]; then
    # Retain a partial download for inspection instead of deleting user data.
    if [ -e "$claude_tk" ] || [ -L "$claude_tk" ]; then
      failed=$backup.failed-install
      suffix=0
      while [ -e "$failed" ] || [ -L "$failed" ]; do
        suffix=$((suffix + 1))
        failed=$backup.failed-install.$suffix
      done
      move_preserving_link "$claude_tk" "$failed"
      printf 'Partial tk installation preserved at %s\n' "$failed" >&2
    fi
    move_preserving_link "$backup" "$claude_tk"
    printf 'tk installation failed; restored the previous Claude tk skill.\n' >&2
  else
    printf 'tk installation failed; inspect any partial installation before retrying.\n' >&2
  fi
  exit "$status"
fi
