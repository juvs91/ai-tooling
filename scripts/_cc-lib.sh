#!/usr/bin/env bash
set -euo pipefail

# Returns the real directory of the current script, resolving symlinks.
cc_script_dir() {
  local src="${BASH_SOURCE[0]}"
  while [[ -L "$src" ]]; do
    local dir
    dir="$(cd -P "$(dirname "$src")" && pwd)"
    src="$(readlink "$src")"
    [[ "$src" != /* ]] && src="${dir}/${src}"
  done
  cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd
}

# Repo root (ai-tooling)
cc_repo_dir() {
  local d
  d="$(cc_script_dir)"
  cd -P "${d}/.." >/dev/null 2>&1 && pwd
}

# Convenience: ensure commands exist
cc_require() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "ERROR: missing dependency: $cmd" >&2
    exit 1
  }
}
