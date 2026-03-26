#!/bin/bash
# cloudsql-mcp.sh: Lanzador del MCP de CloudSQL (postgres-mcp)
# Lee .cloudsql-env para obtener WPC_ENV y credenciales por ambiente.
#
# Uso: Referenciado automáticamente por .mcp.json como servidor MCP "cloudsql"
# Para cambiar ambiente: editar .cloudsql-env → WPC_ENV=dev|qa|prod → Reload Window
#
# Tunnel requerido antes de usar:
#   dev:  gcloud compute ssh cloudsql-proxy --project wc-prj-dev  --zone us-central1-a -- -NL 5432:localhost:5432
#   qa:   gcloud compute ssh cloudsql-proxy --project wc-prj-qa   --zone us-central1-a -- -NL 5432:localhost:5432
#   prod: gcloud compute ssh cloudsql-proxy --project wc-prj-prod --zone us-central1-a -- -NL 5432:localhost:5432

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.cloudsql-env"

# Cargar variables de ambiente
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE no encontrado." >&2
  echo "Copiar desde: .cloudsql-env.template y llenar credenciales." >&2
  exit 1
fi

# shellcheck source=../.cloudsql-env
# shellcheck disable=SC1091
source "$ENV_FILE"

WPC_ENV="${WPC_ENV:-dev}"

# Seleccionar credenciales según WPC_ENV
case "$WPC_ENV" in
  dev)
    DB_USER="${DEV_USER:?DEV_USER no definido en .cloudsql-env}"
    DB_PASSWORD="${DEV_PASSWORD:?DEV_PASSWORD no definido en .cloudsql-env}"
    DB_DATABASE="${DEV_DATABASE:-prices}"
    DB_PORT="${DEV_PORT:-5432}"
    ;;
  qa)
    DB_USER="${QA_USER:?QA_USER no definido en .cloudsql-env}"
    DB_PASSWORD="${QA_PASSWORD:?QA_PASSWORD no definido en .cloudsql-env}"
    DB_DATABASE="${QA_DATABASE:-prices}"
    DB_PORT="${QA_PORT:-5432}"
    ;;
  prod)
    DB_USER="${PROD_USER:?PROD_USER no definido en .cloudsql-env}"
    DB_PASSWORD="${PROD_PASSWORD:?PROD_PASSWORD no definido en .cloudsql-env}"
    DB_DATABASE="${PROD_DATABASE:-prices}"
    DB_PORT="${PROD_PORT:-5432}"
    ;;
  *)
    echo "ERROR: WPC_ENV='$WPC_ENV' inválido. Usar: dev | qa | prod" >&2
    exit 1
    ;;
esac

# URL-encode password (caracteres especiales como @, ?, {, }, etc.)
DB_PASSWORD_ENCODED=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$DB_PASSWORD" 2>/dev/null \
  || printf '%s' "$DB_PASSWORD" | sed 's/@/%40/g; s/{/%7B/g; s/}/%7D/g; s/?/%3F/g; s/#/%23/g')

DB_MAIN_URL="postgresql://${DB_USER}:${DB_PASSWORD_ENCODED}@localhost:${DB_PORT}/${DB_DATABASE}"

POSTGRES_MCP="/Users/jeguzman/.nvm/versions/node/v20.20.0/lib/node_modules/postgres-mcp/dist/index.js"

if [ ! -f "$POSTGRES_MCP" ]; then
  echo "ERROR: postgres-mcp no encontrado en: $POSTGRES_MCP" >&2
  echo "Instalar: npm install -g postgres-mcp" >&2
  exit 1
fi

exec node "$POSTGRES_MCP" --connection-string="$DB_MAIN_URL"
