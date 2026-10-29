#!/bin/bash
# distributable: true
# event: PreToolUse
# matcher: Edit|Write
# timeout: 5
# Bloquea edits al ARCHIVO específico si tiene quality errors pendientes en
# .claude/quality-state/ — antes bloqueaba TODO el proyecto por un error en
# cualquier archivo (la clave de estado era por proyecto, no por archivo).

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0
case "$FILE" in *.ts|*.tsx|*.py|*.go|*.rs|*.js|*.jsx) ;; *) exit 0 ;; esac

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
case "$FILE" in /*) ;; *) FILE="$CWD/$FILE" ;; esac

# ADR-0073: misma raíz y misma clave que ts-quality-gate.sh — estado en la raíz
# canónica de la sesión; clave = archivo canónico relativo a ella (absoluto si
# cae fuera, ej. un worktree hermano). Así un `cd` o un worktree no parten el
# estado y dos worktrees con el mismo src/x.ts no se bloquean entre sí.
LIB="$(dirname "${BASH_SOURCE[0]}")/task-scope-lib.sh"
ROOT=""
if [ -f "$LIB" ] && . "$LIB" && declare -F state_root >/dev/null; then
  ROOT=$(state_root "$CWD")
  FILE=$(canon_path "$FILE")
fi
ROOT=${ROOT:-$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)}
ROOT=${ROOT:-$CWD}

STATE_DIR="$ROOT/.claude/quality-state"
[ -d "$STATE_DIR" ] || exit 0

RELATIVE="${FILE#"$ROOT"/}"
FILE_HASH=$(echo "$RELATIVE" | cksum | cut -d' ' -f1)
ERRORS=""
for f in "$STATE_DIR"/*"-$FILE_HASH"; do
    [ -f "$f" ] || continue
    COUNT=$(cat "$f" 2>/dev/null || echo "0")
    [ "$COUNT" -gt "0" ] 2>/dev/null || continue
    LANG=$(basename "$f" | sed 's/-.*//')
    ERRORS="$ERRORS $LANG:$COUNT"
done

[ -z "$ERRORS" ] && exit 0
echo "BLOCKED: Quality errors pending in $RELATIVE:$ERRORS" >&2
echo "Fix them and delete $STATE_DIR/*-$FILE_HASH to unblock." >&2
echo "(If the type checker is no longer installed, e.g. node_modules removed, the state is stale: delete it manually.)" >&2
exit 2
