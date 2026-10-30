#!/usr/bin/env bash
# distributable: true
# event: PostToolUse
# matcher: Edit|Write|MultiEdit
# timeout: 15
# async: true
# Runs ruff linter on modified Python files after edits.
# Async — does not block Claude, just surfaces warnings.
#
# Salida: JSON con hookSpecificOutput.additionalContext. Texto plano en stdout de
# un PostToolUse NO le llega al modelo (solo al debug log); en un hook async,
# Claude Code entrega additionalContext en el siguiente turno.

# Claude Code passes tool input as JSON via stdin
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE" ]; then
  exit 0
fi

# Only lint Python files
case "$FILE" in
  *.py) ;;
  *) exit 0 ;;
esac
[ -f "$FILE" ] || exit 0

# ruff del PATH; si no, el de la caché de uv (--offline: nunca descarga, y un hook
# async no tiene timeout — una descarga colgada quedaría viva en background)
if command -v ruff >/dev/null 2>&1; then
  RUFF=(ruff)
elif command -v uvx >/dev/null 2>&1 && uvx --offline ruff --version >/dev/null 2>&1; then
  RUFF=(uvx --offline ruff)
else
  exit 0
fi

# Run ruff — informa, nunca bloquea (exit 0 siempre)
OUTPUT=$("${RUFF[@]}" check --output-format concise --quiet "$FILE" 2>&1)
[ -n "$OUTPUT" ] || exit 0

MAX=15
TOTAL=$(printf '%s\n' "$OUTPUT" | grep -c ':[0-9][0-9]*:[0-9][0-9]*:')
SHOWN=$(printf '%s\n' "$OUTPUT" | head -n "$MAX")
MSG="quality-gate (ruff): $TOTAL hallazgo(s) en $FILE"
[ "$TOTAL" -gt "$MAX" ] && MSG="$MSG (mostrando $MAX)"
MSG="$MSG. Pueden ser preexistentes: corrige solo lo que introdujo tu edición.
$SHOWN"
jq -nc --arg msg "$MSG" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $msg}}'
exit 0
