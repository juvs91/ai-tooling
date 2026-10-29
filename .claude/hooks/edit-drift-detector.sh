#!/bin/bash
# distributable: true
# event: PostToolUse
# matcher: Edit|Write|Bash
# timeout: 5
# edit-drift-detector.sh — PostToolUse hook (Edit|Write|Bash)
# Rastrea número de edits desde el último test run.
# Advierte con urgencia creciente: 8 edits (aviso suave), 15 (medio), 25+ (crítico).
# Al detectar test run: resetea contador y emite quality checkpoint.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' | tr -cd 'a-zA-Z0-9_-')

# ADR-0073: estado en la raíz canónica de la sesión (antes en el .cwd: tras un
# `cd` creaba .claude/drift-state/ en subdirectorios — llegó a commitearse) y
# contador por sesión (antes por cksum del cwd: cada `cd` reiniciaba la cuenta).
LIB="$(dirname "${BASH_SOURCE[0]}")/task-scope-lib.sh"
ROOT=""
if [ -f "$LIB" ] && . "$LIB" && declare -F state_root >/dev/null; then
  ROOT=$(state_root "$CWD")
fi
ROOT=${ROOT:-$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)}
ROOT=${ROOT:-$CWD}

STATE_DIR="$ROOT/.claude/drift-state"
mkdir -p "$STATE_DIR" 2>/dev/null || true
COUNTER_KEY="${SESSION_ID:-$(echo "$ROOT" | cksum | cut -d' ' -f1)}"
COUNTER_FILE="$STATE_DIR/edit-count-$COUNTER_KEY"

# Limpia archivos de drift-state con más de 24h
find "$STATE_DIR" -maxdepth 1 -type f -mmin +1440 -delete 2>/dev/null || true

# Bash que corre tests → RESET + quality checkpoint
if [ "$TOOL" = "Bash" ]; then
    CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
    if echo "$CMD" | grep -qE "vitest|jest|npm test|npx tsc|test:run|pytest"; then
        echo "0" > "$COUNTER_FILE"
        echo "drift-detector: Tests ejecutados — contador reseteado." >&2
        echo "  Quality checkpoint:" >&2
        echo "  1. Errores TS resueltos? (npx tsc --noEmit)" >&2
        echo "  2. TODOs actualizados? Tareas marcadas done tienen verification?" >&2
        echo "  3. Scope siguiente tarea definido en .claude/sessions/<session>-task-scope.json?" >&2
        echo "  4. Codigo de produccion no roto? (revisar imports/exports)" >&2
    fi
    exit 0
fi

case "$TOOL" in Edit|Write) ;; *) exit 0 ;; esac

FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0

CURRENT=$(cat "$COUNTER_FILE" 2>/dev/null || echo "0")
NEW=$((CURRENT + 1))
echo "$NEW" > "$COUNTER_FILE"

if [ "$NEW" -eq 8 ]; then
    echo "drift-detector [8 edits]: Sin tests desde el inicio. Considera correr tsc --noEmit." >&2
elif [ "$NEW" -eq 15 ]; then
    echo "drift-detector [15 edits]: CORRE: tsc --noEmit && test:run — muchos cambios sin verificar." >&2
elif [ "$NEW" -ge 25 ]; then
    echo "drift-detector [$NEW edits]: DETENTE. Verifica ANTES de continuar. Riesgo de drift alto." >&2
fi

exit 0
