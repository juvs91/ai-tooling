#!/bin/bash
# Hook: Bloquea ediciones fuera del directorio permitido
# Configurar como PreToolUse para Edit y Write en .claude/settings.json
#
# settings.json:
# {
#   "hooks": {
#     "PreToolUse": [
#       {
#         "matcher": "Edit|Write",
#         "hooks": [{"type": "command", "command": "bash .ralph/hooks/validate-file-boundary.sh"}]
#       }
#     ]
#   }
# }

INPUT=$(cat)

FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('file_path',''))" 2>/dev/null)

# Si no hay file_path, permitir (es Read, Glob, etc.)
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# ── CONFIGURAR ESTOS PATHS ──
BASE="{{PROJECT_ROOT}}"
ALLOWED_1="$BASE/{{WORKING_DIRECTORY}}"
ALLOWED_2="$BASE/.ralph"

if [[ "$FILE_PATH" == "$ALLOWED_1"* ]] || [[ "$FILE_PATH" == "$ALLOWED_2"* ]]; then
  exit 0
else
  echo "BLOQUEADO: Solo se pueden modificar archivos dentro de {{WORKING_DIRECTORY}}/ y .ralph/"
  echo "Intentaste: $FILE_PATH"
  exit 2
fi
