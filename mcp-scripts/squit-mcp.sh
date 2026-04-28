#!/bin/bash
# ============================================================
# squit-mcp.sh — Wrapper para Squit Search MCP
# ============================================================
# CC no expande $VAR en env blocks de .mcp.json al spawnar el proceso.
# Este script carga .env explícitamente y exporta SQUIT_API_KEY
# antes de arrancar el servidor.
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
[ -f "$ENV_FILE" ] && set -a && source "$ENV_FILE" && set +a

if [ -z "$SQUIT_API_KEY" ]; then
  echo "ERROR: Falta SQUIT_API_KEY en .env" >&2
  exit 1
fi

echo "[squit-mcp] Iniciando Squit Search MCP..." >&2

exec npx -y mcp-remote \
  https://squit-mcp.deacero.us/mcp \
  --header "X-API-Key:${SQUIT_API_KEY}"