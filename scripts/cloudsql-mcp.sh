#!/bin/bash
# ============================================================
# CloudSQL MCP Wrapper - Inversión de Dependencia
# ============================================================
# El MCP llama a este script, y este script lee el ambiente
# activo desde .cloudsql-env para conectar al Cloud SQL correcto.
#
# Uso:
#   1. Configurar .cloudsql-env con el ambiente deseado
#   2. Levantar el túnel SSH correspondiente
#   3. El MCP se conecta automáticamente
#
# Cambiar ambiente:
#   Editar .cloudsql-env → DB_ENV=dev|qa|prod
#   Recargar VS Code (Cmd+Shift+P → Reload Window)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.cloudsql-env"

# Verificar que existe el archivo de configuración
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: No se encontró $ENV_FILE" >&2
  echo "Copiar .cloudsql-env.template a .cloudsql-env y configurar credenciales" >&2
  exit 1
fi

# Cargar configuración
source "$ENV_FILE"

# Soporte para variable legacy WPC_ENV
DB_ENV="${DB_ENV:-$WPC_ENV}"

# Validar ambiente
DB_ENV="${DB_ENV:-prod}"
case "$DB_ENV" in
  dev|qa|prod) ;;
  *)
    echo "ERROR: DB_ENV=$DB_ENV no es válido. Usar: dev, qa o prod" >&2
    exit 1
    ;;
esac

# Seleccionar credenciales según ambiente
case "$DB_ENV" in
  dev)
    DB_USER="$DEV_USER"
    DB_PASS="$DEV_PASSWORD"
    DB_NAME="$DEV_DATABASE"
    DB_PORT="${DEV_PORT:-5432}"
    ;;
  qa)
    DB_USER="$QA_USER"
    DB_PASS="$QA_PASSWORD"
    DB_NAME="$QA_DATABASE"
    DB_PORT="${QA_PORT:-5432}"
    ;;
  prod)
    DB_USER="$PROD_USER"
    DB_PASS="$PROD_PASSWORD"
    DB_NAME="$PROD_DATABASE"
    DB_PORT="${PROD_PORT:-5432}"
    ;;
esac

# Validar que tenemos credenciales
if [ -z "$DB_USER" ] || [ -z "$DB_PASS" ] || [ -z "$DB_NAME" ]; then
  echo "ERROR: Credenciales incompletas para ambiente '$DB_ENV'" >&2
  echo "Verificar .cloudsql-env tiene ${DB_ENV^^}_USER, ${DB_ENV^^}_PASSWORD, ${DB_ENV^^}_DATABASE" >&2
  exit 1
fi

# URL-encode del password (caracteres especiales)
DB_PASS_ENCODED=$(node -e "process.stdout.write(encodeURIComponent('$DB_PASS'))" 2>/dev/null)
if [ -z "$DB_PASS_ENCODED" ]; then
  # Fallback: encoding manual de caracteres comunes
  DB_PASS_ENCODED=$(echo -n "$DB_PASS" | sed \
    -e 's/%/%25/g' \
    -e 's/ /%20/g' \
    -e 's/!/%21/g' \
    -e 's/#/%23/g' \
    -e 's/\$/%24/g' \
    -e 's/&/%26/g' \
    -e "s/'/%27/g" \
    -e 's/(/%28/g' \
    -e 's/)/%29/g' \
    -e 's/\*/%2A/g' \
    -e 's/+/%2B/g' \
    -e 's/,/%2C/g' \
    -e 's/\//%2F/g' \
    -e 's/:/%3A/g' \
    -e 's/;/%3B/g' \
    -e 's/=/%3D/g' \
    -e 's/?/%3F/g' \
    -e 's/@/%40/g' \
    -e 's/\[/%5B/g' \
    -e 's/\]/%5D/g' \
    -e 's/\^/%5E/g' \
    -e 's/{/%7B/g' \
    -e 's/}/%7D/g' \
    -e 's/</%3C/g' \
    -e 's/>/%3E/g' \
    -e 's/~/%7E/g')
fi

DB_URL="postgresql://${DB_USER}:${DB_PASS_ENCODED}@localhost:${DB_PORT}/${DB_NAME}"

# Info al stderr (no interfiere con el protocolo MCP en stdout)
echo "[cloudsql-mcp] Ambiente: $DB_ENV | DB: $DB_NAME | Puerto: $DB_PORT" >&2

# Ejecutar postgres-mcp con la URL construida
export DB_MAIN_URL="$DB_URL"
POSTGRES_MCP="$(npm root -g)/postgres-mcp/dist/index.js"
if [ ! -f "$POSTGRES_MCP" ]; then
  echo "ERROR: postgres-mcp no encontrado en $(npm root -g)/postgres-mcp/" >&2
  echo "Instalar con: npm install -g postgres-mcp" >&2
  exit 1
fi
exec node "$POSTGRES_MCP"
