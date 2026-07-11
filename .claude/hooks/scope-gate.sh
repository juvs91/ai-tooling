#!/bin/bash
# distributable: true
# event: PreToolUse
# matcher: Edit|Write
# timeout: 5
# scope-gate.sh — PreToolUse hook (Edit|Write)
# Si .claude/task-scope.json existe en el CWD del proyecto activo, bloquea edits fuera del scope.
# El modelo actualiza task-scope.json dinámicamente al avanzar entre subtareas.
# Sin task-scope.json = sin restricción (opt-in por tarea).

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
SCOPE_FILE="$CWD/.claude/task-scope.json"
[ -f "$SCOPE_FILE" ] || exit 0

TASK_NAME=$(jq -r '.task // "tarea actual"' "$SCOPE_FILE")
STEP=$(jq -r '.current_step // ""' "$SCOPE_FILE")
ALLOWED=$(jq -r '.allowed_patterns // [] | .[]' "$SCOPE_FILE" 2>/dev/null)
[ -z "$ALLOWED" ] && exit 0

RELATIVE="${FILE#$CWD/}"
while IFS= read -r pattern; do
    case "$RELATIVE" in $pattern) exit 0 ;; esac
done <<< "$ALLOWED"

ALLOWED_LIST=$(jq -r '.allowed_patterns | join(", ")' "$SCOPE_FILE")
echo "scope-gate: '$RELATIVE' fuera del scope." >&2
echo "  Tarea: '$TASK_NAME' | Paso: '${STEP:-no definido}'" >&2
echo "  Scope actual: $ALLOWED_LIST" >&2
echo "  → Actualiza .claude/task-scope.json si necesitas un nuevo scope, o confirma con el usuario." >&2
exit 2
