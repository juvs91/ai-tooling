#!/bin/bash
# ============================================================
# alloydb-mcp.sh — Wrapper para AlloyDB MCP (ODS)
# ============================================================
# CC ya carga .env en el entorno del proceso.
# Este script solo construye DB_MAIN_URL con la password correcta
# (CC no expande $VAR embebidas en strings del env block de .mcp.json).
#
# Requiere en .env:
#   WPC_BACKEND_ALLOYDB_PASSWORD  (o ALLOYDB_PASSWORD como fallback)
# Requiere:
#   SSH tunnel activo en localhost:5435
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
[ -f "$ENV_FILE" ] && set -a && source "$ENV_FILE" && set +a

# Usar ALLOYDB_PASSWORD si existe, si no WPC_BACKEND_ALLOYDB_PASSWORD
DB_PASS="${ALLOYDB_PASSWORD:-$WPC_BACKEND_ALLOYDB_PASSWORD}"

if [ -z "$DB_PASS" ]; then
  echo "ERROR: Falta ALLOYDB_PASSWORD o WPC_BACKEND_ALLOYDB_PASSWORD en .env" >&2
  exit 1
fi

# URL-encode del password (por si tiene caracteres especiales)
DB_PASS_ENCODED=$(node -e "process.stdout.write(encodeURIComponent('$DB_PASS'))" 2>/dev/null || echo "$DB_PASS")

DB_HOST="${WPC_BACKEND_ALLOYDB_HOST:-localhost}"
DB_PORT="${WPC_BACKEND_ALLOYDB_PORT:-5435}"
DB_USER="${WPC_BACKEND_ALLOYDB_USER:-postgres}"
DB_NAME="${WPC_BACKEND_ALLOYDB_DATABASE:-ods}"

export DB_MAIN_URL="postgresql://${DB_USER}:${DB_PASS_ENCODED}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

echo "[alloydb-mcp] ${DB_HOST}:${DB_PORT}/${DB_NAME} como ${DB_USER}" >&2

POSTGRES_MCP="$(npm root -g)/postgres-mcp/dist/index.js"
if [ ! -f "$POSTGRES_MCP" ]; then
  echo "ERROR: postgres-mcp no encontrado. Instalar: npm install -g postgres-mcp" >&2
  exit 1
fi

exec node "$POSTGRES_MCP"
