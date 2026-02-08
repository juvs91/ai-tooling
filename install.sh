#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"

ln -sf "${ROOT_DIR}/scripts/cc-profile"     "${BIN_DIR}/cc-profile"
ln -sf "${ROOT_DIR}/scripts/cc-chat"        "${BIN_DIR}/cc-chat"
ln -sf "${ROOT_DIR}/scripts/cc-scan"        "${BIN_DIR}/cc-scan"
ln -sf "${ROOT_DIR}/scripts/cc-plan"        "${BIN_DIR}/cc-plan"
ln -sf "${ROOT_DIR}/scripts/cc-agent"       "${BIN_DIR}/cc-agent"
ln -sf "${ROOT_DIR}/scripts/cc-agent-cloud" "${BIN_DIR}/cc-agent-cloud"

echo "[install] linked:"
ls -l "${BIN_DIR}/cc-"* | sed -n '1,120p'

# Scaffold ai-notes/ from templates (first run only, never overwrites)
AI_NOTES="${ROOT_DIR}/ai-notes"
TEMPLATES="${ROOT_DIR}/templates"
mkdir -p "$AI_NOTES"

if [[ -f "${TEMPLATES}/AI_LEARNING.template.md" ]] && [[ ! -f "${AI_NOTES}/AI_LEARNING.md" ]]; then
  cp "${TEMPLATES}/AI_LEARNING.template.md" "${AI_NOTES}/AI_LEARNING.md"
  echo "[install] scaffolded ai-notes/AI_LEARNING.md"
fi

if [[ -f "${TEMPLATES}/GUARDRAILS.template.md" ]] && [[ ! -f "${AI_NOTES}/GUARDRAILS.md" ]]; then
  cp "${TEMPLATES}/GUARDRAILS.template.md" "${AI_NOTES}/GUARDRAILS.md"
  echo "[install] scaffolded ai-notes/GUARDRAILS.md"
fi

# Ensure ~/.local/bin is on PATH (zsh)
if ! echo "$PATH" | tr ':' '\n' | grep -qx "${BIN_DIR}"; then
  if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "${HOME}/.zshrc" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "${HOME}/.zshrc"
    echo "[install] added ~/.local/bin to PATH in ~/.zshrc"
  fi
  echo "[install] run: source ~/.zshrc"
fi

echo "[install] done"
