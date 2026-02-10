#!/usr/bin/env bash
# init.sh — Source this to add ai-tooling scripts to your PATH
# Usage: source init.sh   (or add to ~/.zshrc / ~/.bashrc)

_INIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export PATH="${_INIT_DIR}/scripts:${_INIT_DIR}/bin:$PATH"

echo "ai-tooling ready:"
echo "  scripts/  → cc-proxy-up, cc-switch, cc-health, cc-chat, cc-proxy-init.sh"
echo "  bin/      → ollama-up, ollama-down, ollama-status, ollama-model"
echo ""
echo "Quick start:"
echo "  cc-proxy-up                                          # default provider"
echo "  PROFILE_ENV=profile-envs/cloud.deepseek.env cc-proxy-up cloud-provider-ymls/docker-compose.deepseek.override.yml"
echo "  cc-switch sonnet|opus|zai                            # switch Claude Code mode"
echo "  cc-health                                            # proxy health check"

unset _INIT_DIR
