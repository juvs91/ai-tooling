#!/bin/bash
# ============================================================
# atlassian-mcp.sh — Wrapper para Atlassian MCP (Jira + Confluence)
# ============================================================
# CC no expande $VAR en env blocks de .mcp.json al spawnar el proceso.
# Este script carga .env explícitamente y exporta las credenciales
# antes de arrancar uvx mcp-atlassian.
#
# Requiere en .env:
#   ATLASSIAN_CONFLUENCE_TOKEN
#   ATLASSIAN_JIRA_API_TOKEN
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
[ -f "$ENV_FILE" ] && set -a && source "$ENV_FILE" && set +a

if [ -z "$ATLASSIAN_CONFLUENCE_TOKEN" ] || [ -z "$ATLASSIAN_JIRA_API_TOKEN" ]; then
  echo "ERROR: Falta ATLASSIAN_CONFLUENCE_TOKEN o ATLASSIAN_JIRA_API_TOKEN en .env" >&2
  exit 1
fi

export CONFLUENCE_URL="https://deacero.atlassian.net/wiki"
export CONFLUENCE_USERNAME="jeguzman@deacero.com"
export CONFLUENCE_API_TOKEN="$ATLASSIAN_CONFLUENCE_TOKEN"
export JIRA_URL="https://deacero.atlassian.net"
export JIRA_USERNAME="jeguzman@deacero.com"
export JIRA_API_TOKEN="$ATLASSIAN_JIRA_API_TOKEN"

echo "[atlassian-mcp] Conectando a deacero.atlassian.net..." >&2

UVX="${HOME}/.local/bin/uvx"
if [ ! -f "$UVX" ]; then
  UVX="$(command -v uvx 2>/dev/null)"
fi
if [ -z "$UVX" ]; then
  echo "ERROR: uvx no encontrado. Instalar con: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

exec "$UVX" mcp-atlassian
