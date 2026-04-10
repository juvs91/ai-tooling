#!/bin/bash
# ============================================================
# atlassian-mcp.sh — Wrapper genérico para MCPs de Atlassian
# ============================================================
# Claude Code no expande $VAR en bloques env de .mcp.json.
# Este script carga .env explícitamente y selecciona el producto.
#
# Uso:
#   bash atlassian-mcp.sh [jira_confluence|bitbucket]
#   ATLASSIAN_PRODUCT=bitbucket bash atlassian-mcp.sh
#
# Requiere en .env según producto:
#   jira_confluence: ATLASSIAN_CONFLUENCE_TOKEN, ATLASSIAN_JIRA_API_TOKEN
#   bitbucket:       ATLASSIAN_BITBUCKET_API_TOKEN
# Opcionales en .env (con defaults):
#   ATLASSIAN_BASE_URL, ATLASSIAN_USERNAME, BITBUCKET_DEFAULT_WORKSPACE
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
[ -f "$ENV_FILE" ] && set -a && source "$ENV_FILE" && set +a

# Producto: arg $1 > env ATLASSIAN_PRODUCT > default jira_confluence
PRODUCT="${1:-${ATLASSIAN_PRODUCT:-jira_confluence}}"

BASE_URL="${ATLASSIAN_BASE_URL:-https://deacero.atlassian.net}"
USERNAME="${ATLASSIAN_USERNAME:-jeguzman@deacero.com}"

case "$PRODUCT" in
  bitbucket)
    if [ -z "$ATLASSIAN_BITBUCKET_API_TOKEN" ]; then
      echo "ERROR: Falta ATLASSIAN_BITBUCKET_API_TOKEN en .env" >&2
      exit 1
    fi
    export ATLASSIAN_USER_EMAIL="$USERNAME"
    export ATLASSIAN_API_TOKEN="$ATLASSIAN_BITBUCKET_API_TOKEN"
    export BITBUCKET_DEFAULT_WORKSPACE="${BITBUCKET_DEFAULT_WORKSPACE:-deacero}"
    echo "[atlassian-mcp] Conectando a bitbucket.org ($BITBUCKET_DEFAULT_WORKSPACE)..." >&2
    exec npx -y @aashari/mcp-server-atlassian-bitbucket
    ;;

  jira_confluence|*)
    if [ -z "$ATLASSIAN_CONFLUENCE_TOKEN" ] || [ -z "$ATLASSIAN_JIRA_API_TOKEN" ]; then
      echo "ERROR: Falta ATLASSIAN_CONFLUENCE_TOKEN o ATLASSIAN_JIRA_API_TOKEN en .env" >&2
      exit 1
    fi
    export CONFLUENCE_URL="${CONFLUENCE_URL:-${BASE_URL}/wiki}"
    export CONFLUENCE_USERNAME="$USERNAME"
    export CONFLUENCE_API_TOKEN="$ATLASSIAN_CONFLUENCE_TOKEN"
    export JIRA_URL="${JIRA_URL:-$BASE_URL}"
    export JIRA_USERNAME="$USERNAME"
    export JIRA_API_TOKEN="$ATLASSIAN_JIRA_API_TOKEN"
    UVX="${HOME}/.local/bin/uvx"
    [ ! -f "$UVX" ] && UVX="$(command -v uvx 2>/dev/null)"
    if [ -z "$UVX" ]; then
      echo "ERROR: uvx no encontrado. Instalar con: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
      exit 1
    fi
    echo "[atlassian-mcp] Conectando a $BASE_URL (Jira + Confluence)..." >&2
    exec "$UVX" mcp-atlassian
    ;;
esac
