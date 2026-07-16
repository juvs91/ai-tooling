#!/bin/bash
# distributable: true
# event: PreToolUse
# matcher: Edit|Write
# timeout: 5
# scope-gate.sh — PreToolUse hook (Edit|Write)
# Si .claude/task-scope.json existe en el CWD del proyecto activo, bloquea edits fuera del scope.
# El modelo actualiza task-scope.json dinámicamente al avanzar entre subtareas.
# Sin task-scope.json = sin restricción (opt-in por tarea).
# mode field en task-scope.json activa enforcement por tipo de tarea:
#   analysis: solo ai-notes/findings/ y .claude/plans/
#   validate: ningún write permitido
#   synthesize: ai-notes/, docs/, *.md únicamente
#   build|full: usa allowed_patterns[] (comportamiento original)

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
SCOPE_FILE="$CWD/.claude/task-scope.json"
[ -f "$SCOPE_FILE" ] || exit 0

RELATIVE="${FILE#$CWD/}"

# Siempre permitir escribir el propio task-scope.json (el agente debe poder inicializar el scope)
[ "$RELATIVE" = ".claude/task-scope.json" ] && exit 0

TASK_NAME=$(jq -r '.task // .task_id // "tarea actual"' "$SCOPE_FILE")
STEP=$(jq -r '.current_step // ""' "$SCOPE_FILE")
MODE=$(jq -r '.mode // "full"' "$SCOPE_FILE")
BASE_MODE=$(echo "$MODE" | cut -d: -f1)

# Mode-specific enforcement — antes de allowed_patterns[]
case "$BASE_MODE" in
  analysis)
    case "$RELATIVE" in
      ai-notes/findings/*|.claude/plans/*) exit 0 ;;
    esac
    echo "scope-gate[analysis]: '$RELATIVE' fuera del scope de análisis." >&2
    echo "  Solo permite writes en: ai-notes/findings/ y .claude/plans/" >&2
    echo "  Analizar ≠ Documentar. Usa mode=synthesize para crear docs." >&2
    exit 2
    ;;
  validate)
    echo "scope-gate[validate]: modo validate no permite writes." >&2
    exit 2
    ;;
  synthesize)
    case "$RELATIVE" in
      ai-notes/*|docs/*|*.md) exit 0 ;;
    esac
    echo "scope-gate[synthesize]: '$RELATIVE' no es documentación." >&2
    exit 2
    ;;
  build|full)
    ;;
esac

# Comportamiento original: allowed_patterns[] (para mode=build|full)
ALLOWED=$(jq -r '.allowed_patterns // [] | .[]' "$SCOPE_FILE" 2>/dev/null)
[ -z "$ALLOWED" ] && exit 0

while IFS= read -r pattern; do
    case "$RELATIVE" in $pattern) exit 0 ;; esac
done <<< "$ALLOWED"

ALLOWED_LIST=$(jq -r '.allowed_patterns | join(", ")' "$SCOPE_FILE")
echo "scope-gate: '$RELATIVE' fuera del scope." >&2
echo "  Tarea: '$TASK_NAME' | Paso: '${STEP:-no definido}'" >&2
echo "  Scope actual: $ALLOWED_LIST" >&2
echo "  → Actualiza .claude/task-scope.json si necesitas un nuevo scope, o confirma con el usuario." >&2
exit 2
