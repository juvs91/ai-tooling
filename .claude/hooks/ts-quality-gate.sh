#!/bin/bash
# distributable: true
# event: PostToolUse
# matcher: Edit|Write
# timeout: 30
# async: true
# ts-quality-gate.sh — PostToolUse hook (Edit|Write on *.ts/*.tsx)
# Corre tsc, guarda error count en quality-state/, appendea audit log.
# Patrón two-hook: este guarda estado → quality-enforce.sh (PreToolUse) bloquea el siguiente edit.

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
case "$FILE" in *.ts|*.tsx) ;; *) exit 0 ;; esac

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
[ -f "$CWD/tsconfig.json" ] || exit 0
command -v npx >/dev/null 2>&1 || exit 0

STATE_DIR="$CWD/.claude/quality-state"
mkdir -p "$STATE_DIR" 2>/dev/null || true
PROJECT_HASH=$(echo "$CWD" | cksum | cut -d' ' -f1)
ERROR_FILE="$STATE_DIR/ts-$PROJECT_HASH"
AUDIT_LOG="$CWD/.claude/quality-events.log"

RELATIVE="${FILE#$CWD/}"
TS_OUTPUT=$(cd "$CWD" && npx tsc --noEmit 2>&1 || true)
FILE_ERRORS=$(echo "$TS_OUTPUT" | grep "$RELATIVE" | grep -c "error TS" 2>/dev/null || echo "0")
TS_CODES=$(echo "$TS_OUTPUT" | grep "$RELATIVE" | grep -o "TS[0-9]*" | sort -u | tr '\n' ',' | sed 's/,$//')

TIMESTAMP=$(date -u +%FT%TZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)

if [ "$FILE_ERRORS" -gt "0" ]; then
    echo "$FILE_ERRORS" > "$ERROR_FILE"
    SAMPLE=$(echo "$TS_OUTPUT" | grep "$RELATIVE" | grep "error TS" | head -3)
    echo "$TIMESTAMP | ts | $FILE_ERRORS errors | $RELATIVE | $TS_CODES" >> "$AUDIT_LOG" 2>/dev/null || true
    echo "MANDATORY ACTION REQUIRED: $FILE_ERRORS TypeScript errors in $(basename "$FILE")." >&2
    echo "$SAMPLE" >&2
    echo "You MUST fix these errors before proceeding to the next task. Run: npx tsc --noEmit" >&2
else
    rm -f "$ERROR_FILE" 2>/dev/null || true
    echo "$TIMESTAMP | ts | 0 errors | $RELATIVE | FIXED" >> "$AUDIT_LOG" 2>/dev/null || true
fi

exit 0  # PostToolUse siempre exit 0
