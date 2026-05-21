#!/usr/bin/env bash
set -euo pipefail

# Remote Codex session helper:
# - Prefer SSH when SSH_PRIVATE_KEY is available.
# - Fallback to HTTPS token auth when GH_TOKEN / REPO_WRITE_TOKEN / GITHUB_TOKEN is available.
# - Validate access with git ls-remote.

repo_dir="${1:-.}"
cd "$repo_dir"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: not a git repository: $repo_dir" >&2
  exit 1
fi

origin_url="$(git remote get-url origin 2>/dev/null || true)"
if [[ -z "$origin_url" ]]; then
  echo "ERROR: origin remote is not set." >&2
  exit 1
fi

extract_repo_path() {
  local url="$1"
  local path=""
  if [[ "$url" == git@github.com:* ]]; then
    path="${url#git@github.com:}"
  elif [[ "$url" == https://github.com/* ]]; then
    path="${url#https://github.com/}"
  elif [[ "$url" == http://github.com/* ]]; then
    path="${url#http://github.com/}"
  fi
  path="${path%.git}"
  if [[ "$path" == */* ]]; then
    printf '%s\n' "$path"
    return 0
  fi
  return 1
}

repo_path="$(extract_repo_path "$origin_url" || true)"
if [[ -z "$repo_path" && -n "${GITHUB_REPOSITORY:-}" ]]; then
  repo_path="${GITHUB_REPOSITORY}"
fi
if [[ -z "$repo_path" ]]; then
  echo "ERROR: could not parse owner/repo from origin=$origin_url" >&2
  exit 1
fi

ssh_private_key="${SSH_PRIVATE_KEY:-}"
token_value="${GH_TOKEN:-${REPO_WRITE_TOKEN:-${GITHUB_TOKEN:-}}}"

if [[ -n "$ssh_private_key" ]]; then
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  printf '%s\n' "$ssh_private_key" > "$HOME/.ssh/id_ed25519"
  chmod 600 "$HOME/.ssh/id_ed25519"

  if command -v ssh-keyscan >/dev/null 2>&1; then
    ssh-keyscan -t ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null || true
    chmod 600 "$HOME/.ssh/known_hosts" || true
  fi

  git remote set-url origin "git@github.com:${repo_path}.git"
  git config --local core.sshCommand "ssh -i $HOME/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

  echo "Auth mode: SSH"
  git ls-remote --heads origin >/dev/null
  echo "OK: SSH auth and origin are ready."
  exit 0
fi

if [[ -n "$token_value" ]]; then
  mkdir -p "$HOME/.config/codex"
  askpass_path="$HOME/.config/codex/git-askpass.sh"
  cat > "$askpass_path" <<'EOF'
#!/usr/bin/env sh
case "$1" in
  *Username*) echo "x-access-token" ;;
  *Password*) echo "${CODEX_GIT_TOKEN:-}" ;;
  *) echo "" ;;
esac
EOF
  chmod 700 "$askpass_path"

  git remote set-url origin "https://github.com/${repo_path}.git"
  git config --local core.askPass "$askpass_path"
  git config --local core.sshCommand ""

  export CODEX_GIT_TOKEN="$token_value"
  GIT_ASKPASS="$askpass_path" GIT_TERMINAL_PROMPT=0 git ls-remote --heads origin >/dev/null
  echo "Auth mode: HTTPS token"
  echo "OK: HTTPS token auth and origin are ready."
  exit 0
fi

echo "ERROR: no auth secret found." >&2
echo "Set one of:" >&2
echo "  - SSH_PRIVATE_KEY (recommended)" >&2
echo "  - GH_TOKEN or REPO_WRITE_TOKEN or GITHUB_TOKEN" >&2
exit 1
