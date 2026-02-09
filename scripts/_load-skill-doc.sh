#!/bin/bash
# Loader de documentación de skills dinámicos
# Usage: ./scripts/_load-skill-doc.sh <skill-name>
#
# Lee el markdown de documentación desde docs/<skill-name>/
# y lo imprime en stdout para que el MCP lo lea.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

SKILL_NAME="${1:-}"

if [ -z "$SKILL_NAME" ]; then
  echo "ERROR: Se requiere el nombre del skill" >&2
  echo "Usage: $0 <skill-name>" >&2
  echo "" >&2
  echo "Skills disponibles:" >&2
  ls -1 "$PROJECT_DIR/docs" 2>/dev/null | grep -v "^\." | head -10
  exit 1
fi

# Buscar documentación en múltiples rutas
DOCS_PATHS=(
  "$PROJECT_DIR/docs/$SKILL_NAME"
  "$PROJECT_DIR/docs/$SKILL_NAME/usage.md"
  "$PROJECT_DIR/docs/$SKILL_NAME.md"
  "$PROJECT_DIR/docs/commands/$SKILL_NAME.md"
)

DOC_FILE=""
for path in "${DOCS_PATHS[@]}"; do
  if [ -f "$path" ]; then
    DOC_FILE="$path"
    break
  fi
done

if [ -z "$DOC_FILE" ]; then
  echo "ERROR: No se encontró documentación para '$SKILL_NAME'" >&2
  echo "Rutas buscadas:" >&2
  printf "  - %s\n" "${DOCS_PATHS[@]}" >&2
  exit 1
fi

# Imprimir contenido del markdown a stdout
cat "$DOC_FILE"
