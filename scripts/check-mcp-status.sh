#!/usr/bin/env bash
# check-mcp-status.sh: Verifica el estado de todos los servicios MCP del proyecto
# Usado por: tunnel-health skill, CLAUDE.md

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

OK="✅ OK"
WARN="⚠️  WARN"
FAIL="❌ FAIL"

print_row() {
  printf "  %-28s %s\n" "$1" "$2"
}

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║       MCP Services Health Check — ai-tooling         ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# 1. SSH Tunnel → AlloyDB (puerto 5435)
if nc -z localhost 5435 2>/dev/null; then
  print_row "AlloyDB tunnel (5435)" "$OK"
else
  print_row "AlloyDB tunnel (5435)" "$FAIL — ejecutar: ssh-tunnel alloydb"
fi

# 2. Squit MCP (localhost:8000)
if nc -z localhost 8000 2>/dev/null; then
  print_row "Squit MCP (8000)" "$OK"
else
  print_row "Squit MCP (8000)" "$WARN — remoto: squit-mcp.deacero.us"
fi

# 3. CloudSQL (SSH tunnel, puerto 5432)
if nc -z localhost 5432 2>/dev/null; then
  WPC_ENV=$(grep -E '^WPC_ENV=' "$PROJECT_DIR/.cloudsql-env" 2>/dev/null | cut -d= -f2 || echo "?")
  print_row "CloudSQL tunnel (5432)" "$OK — env: $WPC_ENV"
else
  print_row "CloudSQL tunnel (5432)" "$WARN — túnel inactivo (opcional)"
fi

# 4. Proxy Claude-Code (puerto 8082/8083)
PROXY_OK=false
for port in 8082 8083 8084 8085; do
  if curl -sf "http://localhost:$port/health" >/dev/null 2>&1; then
    print_row "Claude proxy ($port)" "$OK"
    PROXY_OK=true
    break
  fi
done
if ! $PROXY_OK; then
  print_row "Claude proxy" "$WARN — no activo (optional para sesión nativa)"
fi

# 5. Node.js (postgres-mcp)
POSTGRES_MCP="/Users/jeguzman/.nvm/versions/node/v20.20.0/lib/node_modules/postgres-mcp/dist/index.js"
if [ -f "$POSTGRES_MCP" ]; then
  print_row "postgres-mcp binary" "$OK"
else
  print_row "postgres-mcp binary" "$FAIL — npm install -g postgres-mcp"
fi

# 6. uvx (atlassian MCP)
if command -v uvx &>/dev/null; then
  print_row "uvx (atlassian MCP)" "$OK"
else
  print_row "uvx (atlassian MCP)" "$FAIL — pip install uv"
fi

# 7. jq (requerido por hooks)
if command -v jq &>/dev/null; then
  print_row "jq (hooks dependency)" "$OK"
else
  print_row "jq (hooks dependency)" "$FAIL — brew install jq"
fi

# 8. ruff (quality-gate hook)
if command -v ruff &>/dev/null; then
  print_row "ruff (quality-gate hook)" "$OK"
else
  print_row "ruff (quality-gate hook)" "$WARN — pip install ruff (opcional)"
fi

echo ""
