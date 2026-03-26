#!/bin/bash
# ============================================================
# alloydb-mcp.sh — Wrapper para AlloyDB MCP (ODS)
# ============================================================
# CC ya carga .env en el entorno del proceso.
# Este script solo construye DB_MAIN_URL con la password correcta
# (CC no expande $VAR embebidas en strings del env block de .mcp.json).
#
# Requiere en .env:
#   ALLOYDB_PASSWORD  — solo el password del usuario postgres en AlloyDB ODS
# Requiere:
#   SSH tunnel activo en localhost:5435
#
# Valores fijos (AlloyDB ODS — no usar WPC_BACKEND_ALLOYDB_* que apuntan a CloudSQL):
#   Host: localhost, Port: 5435, User: postgres, DB: ods
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
[ -f "$ENV_FILE" ] && set -a && source "$ENV_FILE" && set +a

DB_PASS="${ALLOYDB_PASSWORD}"

if [ -z "$DB_PASS" ]; then
  echo "ERROR: Falta ALLOYDB_PASSWORD en .env (solo el password, no la URL completa)" >&2
  exit 1
fi

# URL-encode del password (por si tiene caracteres especiales)
DB_PASS_ENCODED=$(node -e "process.stdout.write(encodeURIComponent('$DB_PASS'))" 2>/dev/null || echo "$DB_PASS")

export DB_MAIN_URL="postgresql://ods:${DB_PASS_ENCODED}@localhost:5435/postgres"

echo "[alloydb-mcp] localhost:5435/postgres como postgres" >&2

POSTGRES_MCP="$(npm root -g)/postgres-mcp/dist/index.js"
if [ ! -f "$POSTGRES_MCP" ]; then
  echo "ERROR: postgres-mcp no encontrado. Instalar: npm install -g postgres-mcp" >&2
  exit 1
fi

exec node "$POSTGRES_MCP"
