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
STATE_DIR="$CWD/.claude/quality-state"
[ -d "$STATE_DIR" ] || exit 0

RELATIVE="${FILE#$CWD/}"
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
exit 2
