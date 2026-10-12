#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dry_run=false
install_tools=false

usage() {
  cat <<'EOF'
Usage: ./install.sh [--dry-run] [--tools]

  --dry-run  Print filesystem changes and tool commands without applying them.
  --tools    Install Homebrew dependencies (including Claude Code) and tk.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=true ;;
    --tools) install_tools=true ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# Never write through a whole-directory Claude skills link into another app or
# the checkout. Per-skill links are supported; inspect/migrate this root first.
if [ -L "$HOME/.claude/skills" ]; then
  printf 'Claude skills root is a symlink: %s. Inspect it and use a real skills directory before installing.\n' "$HOME/.claude/skills" >&2
  exit 1
fi

timestamp=$(date '+%Y%m%d-%H%M%S')
backup_root=${DOTFILES_BACKUP_DIR:-"$HOME/.dotfiles-backups/$timestamp"}

run() {
  if [ "$dry_run" = true ]; then
    printf '+'
    printf ' %s' "$@"
    printf '\n'
  else
    "$@"
  fi
}

link_file() {
  source=$1
  # Existing unmarked skills remain accepted. Never replace an installed skill
  # with an explicitly marked candidate, including its support files.
  case "$source" in
    "$repo_dir"/skills-local/.codex/skills/*/*)
      skill_relative=${source#"$repo_dir/skills-local/.codex/skills/"}
      skill_name=${skill_relative%%/*}
      if [ -e "$repo_dir/skills-local/.codex/skills/$skill_name/.candidate" ] ||
         [ -L "$repo_dir/skills-local/.codex/skills/$skill_name/.candidate" ]; then
        return
      fi
      ;;
  esac
  relative=${source#"$repo_dir/"}
  relative=${relative#*/}
  link_path "$source" "$relative"
}

link_path() {
  source=$1
  relative=$2
  destination=$HOME/$relative

  # A parent directory can itself link into the checkout. Never move a source
  # file/directory onto its own backup or replace it with a self-link.
  if [ "$destination" -ef "$source" ] ||
     { [ -L "$destination" ] && [ "$(readlink "$destination")" = "$source" ]; }; then
    printf 'linked  %s\n' "$destination"
    return
  fi

  if [ -e "$destination" ] || [ -L "$destination" ]; then
    backup=$backup_root/$relative
    # A repeated run (or a fixed backup root) must not replace an earlier backup.
    backup_suffix=0
    while [ -e "$backup" ] || [ -L "$backup" ]; do
      backup_suffix=$((backup_suffix + 1))
      backup=$backup_root/$relative.$backup_suffix
    done
    printf 'backup  %s -> %s\n' "$destination" "$backup"
    run mkdir -p "$(dirname "$backup")"
    if [ -L "$destination" ]; then
      target=$(readlink "$destination")
      case "$target" in
        /*) ;;
        *) target=$(dirname "$destination")/$target ;;
      esac
      run ln -s "$target" "$backup"
      run rm "$destination"
    else
      run mv "$destination" "$backup"
    fi
  fi

  printf 'link    %s -> %s\n' "$destination" "$source"
  run mkdir -p "$(dirname "$destination")"
  run ln -s "$source" "$destination"
}

link_package() {
  package=$1
  # Tests and local applications can create these files inside the checkout.
  # Keep real configuration files, including dotfiles and newly added files.
  find "$repo_dir/$package" -type d -name __pycache__ -prune -o \
    -type f ! -name .DS_Store ! -name '*.pyc' ! -name '*.pyo' -print |
    while IFS= read -r source; do
      link_file "$source"
    done
}

install_user_tools() {
  if ! command -v brew >/dev/null 2>&1; then
    printf 'Homebrew is required to install Brewfile dependencies.\n' >&2
    exit 1
  fi
  run brew bundle --file "$repo_dir/Brewfile"

  # Brewfile provides uv. A dry run only prints that prerequisite installation.
  if [ "$dry_run" = false ] && ! command -v uv >/dev/null 2>&1; then
    printf 'uv was not found after brew bundle.\n' >&2
    exit 1
  fi

  if command -v tk >/dev/null 2>&1; then
    printf 'tool    tk is already installed at %s\n' "$(command -v tk)"
  else
    run uv tool install git+https://github.com/mixidota2/tasukura.git
  fi
}

for package in shell cmux wezterm yazi nvim herdr orca pi codex tk skills-local; do
  link_package "$package"
done

# Keep Claude's skills directory independent so local/account-synced skills remain.
# Only directories containing SKILL.md are discoverable skills, not support files.
for skill_dir in "$repo_dir"/skills-local/.codex/skills/*; do
  [ -f "$skill_dir/SKILL.md" ] || continue
  if [ -e "$skill_dir/.candidate" ] || [ -L "$skill_dir/.candidate" ]; then
    printf 'skip candidate  %s\n' "$skill_dir"
    continue
  fi
  skill_name=${skill_dir##*/}
  link_path "$skill_dir" ".claude/skills/$skill_name"
done

# Pi and Codex share one skill installation.
pi_skills=$HOME/.pi/agent/skills
codex_skills=$HOME/.codex/skills
if [ ! -e "$pi_skills" ] && [ ! -L "$pi_skills" ]; then
  printf 'link    %s -> %s\n' "$pi_skills" "$codex_skills"
  run mkdir -p "$(dirname "$pi_skills")"
  run ln -s "$codex_skills" "$pi_skills"
fi

if [ "$install_tools" = true ]; then
  install_user_tools
fi

printf 'Done. Restart applications or reload their configuration.\n'
