#!/bin/bash
# ============================================================
# serper-mcp.sh — Wrapper para Serper Search MCP
# ============================================================
# CC no expande $VAR en env blocks de .mcp.json al spawnar el proceso.
# Este script carga .env explícitamente y exporta SERPER_API_KEY
# antes de arrancar el servidor.
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
[ -f "$ENV_FILE" ] && set -a && source "$ENV_FILE" && set +a

if [ -z "$SERPER_API_KEY" ]; then
  echo "ERROR: Falta SERPER_API_KEY en .env" >&2
  exit 1
fi

echo "[serper-mcp] Iniciando Serper Search MCP..." >&2

exec npx -y serper-search-scrape-mcp-server
