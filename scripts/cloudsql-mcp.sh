#!/bin/bash
# ============================================================
# CloudSQL MCP Wrapper - Inversión de Dependencia
# El MCP llama a este script, y este script lee el ambiente
# activo desde .cloudsql-env para conectar al Cloud SQL correcto.
#
# Uso:
#   1. Configurar .cloudsql-env con el ambiente deseado
#   2. Levantar el túnel SSH correspondiente
#   3. El MCP se conecta automáticamente
#
# Cambiar ambiente:
#   Editar .cloudsql-env → WPC_ENV=dev|qa|prod
#   Recargar VS Code (Cmd+Shift+P → Reload Window)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.cloudsql-env"

# Verificar que existe el archivo de configuración
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: No se encontró $ENV_FILE" >&2
  echo "Crear archivo con formato:" >&2
  echo "" >&2
  echo "WPC_ENV=prod" >&2
  echo "PROD_USER=user_prod" >&2
  echo "PROD_PASSWORD=password_prod" >&2
  echo "PROD_DATABASE=database_prod" >&2
  echo "PROD_PORT=5432" >&2
  echo "" >&2
  echo "O para QA/DEV cambiar WPC_ENV y los prefijos PROD_ → QA_ / DEV_" >&2
  exit 1
fi

# Cargar configuración
source "$ENV_FILE"

# Validar ambiente
WPC_ENV="${WPC_ENV:-prod}"
case "$WPC_ENV" in
  dev|qa|prod) ;;
  *)
    echo "ERROR: WPC_ENV=$WPC_ENV no es válido. Usar: dev, qa o prod" >&2
    exit 1
    ;;
esac

# Seleccionar credenciales según ambiente
case "$WPC_ENV" in
  dev)
    DB_USER="${DEV_USER:-}"
    DB_PASS="${DEV_PASSWORD:-}"
    DB_NAME="${DEV_DATABASE:-}"
    DB_PORT="${DEV_PORT:-5432}"
    ;;
  qa)
    DB_USER="${QA_USER:-}"
    DB_PASS="${QA_PASSWORD:-}"
    DB_NAME="${QA_DATABASE:-}"
    DB_PORT="${QA_PORT:-5432}"
    ;;
  prod)
    DB_USER="${PROD_USER:-}"
    DB_PASS="${PROD_PASSWORD:-}"
    DB_NAME="${PROD_DATABASE:-}"
    DB_PORT="${PROD_PORT:-5432}"
    ;;
esac

# Validar que tenemos credenciales
if [ -z "$DB_USER" ] || [ -z "$DB_PASS" ] || [ -z "$DB_NAME" ]; then
  echo "ERROR: Credenciales incompletas para ambiente '$WPC_ENV'" >&2
  echo "Verificar .cloudsql-env tiene ${WPC_ENV^^}_USER, ${WPC_ENV^^}_PASSWORD, ${WPC_ENV^^}_DATABASE" >&2
  exit 1
fi

# URL-encode del password (caracteres especiales)
if command -v node >/dev/null 2>&1; then
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
else
  # Fallback simple si node no está disponible
  DB_PASS_ENCODED="$DB_PASS"
fi

DB_URL="postgresql://${DB_USER}:${DB_PASS_ENCODED}@localhost:${DB_PORT}/${DB_NAME}"

# Info al stderr (no interfere con el protocolo MCP en stdout)
echo "[cloudsql-mcp] Ambiente: $WPC_ENV | DB: $DB_NAME | Puerto: $DB_PORT" >&2

# Ejecutar postgres-mcp con la URL construida
export DB_MAIN_URL="$DB_URL"
if [ -f "$PROJECT_DIR/node_modules/.bin/postgres-mcp" ]; then
  exec "$PROJECT_DIR/node_modules/.bin/postgres-mcp"
elif command -v npx >/dev/null 2>&1; then
  exec npx postgres-mcp
else
  echo "ERROR: Ni node_modules/postgres-mcp ni npx están disponibles" >&2
  exit 1
fi
