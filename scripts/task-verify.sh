#!/bin/bash
# distributable: true
# task-verify.sh — portable macOS/Linux
# Corre el completion_checklist de .claude/task-scope.json y reporta pass/fail.
# Usage: ./scripts/task-verify.sh
# Exit 0: task completa. Exit 1: ítems pendientes.

SCOPE_FILE=".claude/task-scope.json"
[ ! -f "$SCOPE_FILE" ] && { echo "No task-scope.json found. Nothing to verify."; exit 0; }

MODE=$(jq -r '.mode // "full"' "$SCOPE_FILE")
LANG_SUFFIX=$(echo "$MODE" | cut -d: -f2 -s)
BASE_MODE=$(echo "$MODE" | cut -d: -f1)
TASK_ID=$(jq -r '.task_id // "unnamed"' "$SCOPE_FILE")
CHECKLIST=$(jq -r '.completion_checklist[]? // empty' "$SCOPE_FILE")

echo "=== Task Verify: $TASK_ID (mode: $MODE) ==="
echo ""

PASS=0
FAIL=0
INCOMPLETE=()

while IFS= read -r check; do
  [ -z "$check" ] && continue
  CMD=$(echo "$check" | sed 's/[[:space:]]*#.*//' | xargs)
  LABEL=$(echo "$check" | grep -o '#.*' | sed 's/^# *//' || true)
  [ -z "$LABEL" ] && LABEL="$CMD"

  OUTPUT=$(eval "$CMD" 2>&1)
  STATUS=$?

  if [ $STATUS -eq 0 ] && [ -n "$OUTPUT" ]; then
    echo "✅ $LABEL"
    echo "   → $OUTPUT"
    PASS=$((PASS + 1))
  else
    echo "❌ $LABEL"
    echo "   CMD: $CMD"
    [ -n "$OUTPUT" ] && echo "   → $OUTPUT"
    FAIL=$((FAIL + 1))
    INCOMPLETE+=("$LABEL")
  fi
done <<< "$CHECKLIST"

echo ""
echo "--- $PASS passed, $FAIL failed ---"

if [ $FAIL -eq 0 ]; then
  echo ""
  echo "✅ Task appears COMPLETE."
  case "$BASE_MODE" in
    build|validate)
      case "$LANG_SUFFIX" in
        ts)  echo "→ Run: npx tsc --noEmit && pnpm test:run && pnpm test:e2e" ;;
        py)  echo "→ Run: ruff check . && pytest" ;;
        go)  echo "→ Run: go vet ./... && go test ./..." ;;
        rs)  echo "→ Run: cargo check && cargo test" ;;
        *)   echo "→ Run your project's test suite before merging" ;;
      esac
      ;;
    analysis|synthesize)
      echo "→ No code changed. No tests required."
      ;;
  esac
  exit 0
else
  echo ""
  echo "⚠️  Task INCOMPLETE. Pending items:"
  for item in "${INCOMPLETE[@]}"; do echo "   - $item"; done
  exit 1
fi
