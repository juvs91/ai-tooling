#!/bin/bash
# protect-secrets.sh — PreToolUse hook para Edit/Write/MultiEdit
# Bloquea escritura de secrets en archivos trackeados por git.
#
# Claude Code pasa JSON por stdin:
#   Edit:  { "tool_name": "Edit",  "tool_input": { "file_path": "...", "new_string": "..." } }
#   Write: { "tool_name": "Write", "tool_input": { "file_path": "...", "content": "..." } }
# Exit 0 = permitir, Exit 2 = bloquear (stderr = razón)

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Si no hay file_path, permitir
[ -z "$FILE_PATH" ] && exit 0

# ── Reglas ─────────────────────────────────────────────────────────────────

# 1. Proteger archivos trackeados por git (no gitignored)
#    git check-ignore -q retorna 0 si el archivo ES ignorado, 1 si NO lo es
FILE_DIR=$(dirname "$FILE_PATH")
FILE_BASE=$(basename "$FILE_PATH")
if ! git -C "$FILE_DIR" check-ignore -q "$FILE_BASE" 2>/dev/null; then
  # El archivo NO está gitignored → verificar si el contenido tiene secrets
  CONTENT=$(echo "$INPUT" | jq -r '.tool_input.content // .tool_input.new_string // empty')
  # Matches literal secrets: alphanumeric/symbol values 20+ chars, NOT variable refs ($VAR, "${VAR}")
  if echo "$CONTENT" | grep -qE '(API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY)=[A-Za-z0-9.+/_\-]{20,}'; then
    echo "BLOCKED: Intentando escribir un secret en un archivo trackeado por git: $FILE_PATH" >&2
    echo "   Guarda las credentials en un archivo en .gitignore (ej: .env, profile-envs/)." >&2
    exit 2
  fi
fi

# 2. Warn en edits directos a .claude/settings.json compartido (evitar override accidental)
if echo "$FILE_PATH" | grep -qE '\.claude/settings\.json$' && ! echo "$FILE_PATH" | grep -q 'settings.local'; then
  echo "WARNING: Modificando .claude/settings.json compartido. Considera usar settings.local.json." >&2
  exit 0
fi

exit 0
