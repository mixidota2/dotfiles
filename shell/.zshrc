# User-local tools installed by uv and similar installers.
[[ -f "$HOME/.local/bin/env" ]] && source "$HOME/.local/bin/env"
export PATH="$HOME/.local/bin:$PATH"

# Development tool versions.
if command -v mise >/dev/null 2>&1; then
  eval "$(mise activate zsh)"
fi

# Initialize the completion system before any script calls compdef.
autoload -Uz compinit && compinit

# Google Cloud SDK (Homebrew gcloud-cli or standalone ~/google-cloud-sdk).
for gcloud_dir in "$HOME/google-cloud-sdk" /opt/homebrew/share/google-cloud-sdk /usr/local/share/google-cloud-sdk; do
  if [[ -f "$gcloud_dir/path.zsh.inc" ]]; then
    source "$gcloud_dir/path.zsh.inc"
    [[ -f "$gcloud_dir/completion.zsh.inc" ]] && source "$gcloud_dir/completion.zsh.inc"
    break
  fi
done
unset gcloud_dir

# uv completion.
if command -v uvx >/dev/null 2>&1; then
  eval "$(uvx --generate-shell-completion zsh)"
fi

# Change the current shell directory when leaving Yazi.
function y() {
  local tmp cwd
  tmp="$(mktemp -t 'yazi-cwd.XXXXXX')"
  yazi "$@" --cwd-file="$tmp"
  if cwd="$(command cat -- "$tmp")" && [[ -n "$cwd" && "$cwd" != "$PWD" ]]; then
    builtin cd -- "$cwd"
  fi
  rm -f -- "$tmp"
}

# Cortex CLI completion is generated locally by Cortex.
[[ -s "$HOME/.zsh/completions/cortex.zsh" ]] && source "$HOME/.zsh/completions/cortex.zsh"

# Render a Markdown file full-screen, including Mermaid diagrams.
function mdview() {
  if (( $# != 1 )); then
    print -u2 "Usage: mdview <markdown-file>"
    return 2
  fi

  if [[ ! -f "$1" ]]; then
    print -u2 "mdview: not a file: $1"
    return 2
  fi

  command nvim -n \
    -c 'lua require("md-render").preview.show_pager({ max_width = vim.o.columns })' \
    -- "$1"
}

# Load machine-local overrides (e.g. ANTHROPIC_VERTEX_PROJECT_ID) if present.
[[ -f "$HOME/.zshrc.local" ]] && source "$HOME/.zshrc.local"

# Claude Code: 対話では Vertex AI を使い、claude -p（--print）のときだけサブスクリプションを使う
function claude() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -p|--print) command claude "$@"; return ;;
    esac
  done
  local vertex_project="${ANTHROPIC_VERTEX_PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-}}"
  if [[ -z "$vertex_project" ]] && command -v gcloud >/dev/null 2>&1; then
    vertex_project="$(gcloud config get-value project 2>/dev/null)"
  fi
  CLAUDE_CODE_USE_VERTEX=1 \
    ANTHROPIC_VERTEX_PROJECT_ID="$vertex_project" \
    CLOUD_ML_REGION="${CLOUD_ML_REGION:-global}" \
    command claude "$@"
}
