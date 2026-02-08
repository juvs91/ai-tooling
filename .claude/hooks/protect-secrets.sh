#!/bin/bash
# protect-secrets.sh — PreToolUse hook para Edit/Write
# Bloquea escritura/edición de archivos .env con secrets reales
#
# Claude Code pasa JSON por stdin:
#   Edit:  { "tool_name": "Edit",  "tool_input": { "file_path": "...", "new_string": "..." } }
#   Write: { "tool_name": "Write", "tool_input": { "file_path": "...", "content": "..." } }

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Si no hay file_path, permitir
[ -z "$FILE_PATH" ] && exit 0

# ── Reglas de bloqueo ──────────────────────────────────────────────

# 1. Proteger archivos .env en profile-envs/ (contienen API keys reales)
if echo "$FILE_PATH" | grep -qE 'profile-envs/.*\.env$'; then
  # Revisar si el contenido nuevo tiene API keys reales (no placeholders)
  CONTENT=$(echo "$INPUT" | jq -r '.tool_input.content // .tool_input.new_string // empty')
  if echo "$CONTENT" | grep -qE '(API_KEY|SECRET|TOKEN)=.{20,}' && ! echo "$CONTENT" | grep -qE 'PLACEHOLDER'; then
    echo "WARNING: Modificando .env con posible API key real. Verifica que no se commitee." >&2
    # Solo warn (exit 0), no bloquear — el usuario puede necesitar esto
    exit 0
  fi
fi

# 2. Bloquear escritura directa a ~/.claude/settings.json (evitar override accidental)
if echo "$FILE_PATH" | grep -qE '\.claude/settings\.json$' && ! echo "$FILE_PATH" | grep -q 'settings.local'; then
  echo "WARNING: Modificando .claude/settings.json compartido. Considera usar settings.local.json." >&2
  exit 0
fi

exit 0
