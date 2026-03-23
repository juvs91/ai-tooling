#!/usr/bin/env bash
# Runs ruff linter on modified Python files after edits.
# Async — does not block Claude, just surfaces warnings.

FILE="${CLAUDE_TOOL_INPUT_FILE_PATH:-}"

if [ -z "$FILE" ]; then
  exit 0
fi

# Only lint Python files
case "$FILE" in
  *.py) ;;
  *) exit 0 ;;
esac

# Only run if ruff is available
if ! command -v ruff &>/dev/null; then
  exit 0
fi

# Run ruff — output warnings but don't block (exit 0 always)
OUTPUT=$(ruff check "$FILE" 2>&1)
if [ -n "$OUTPUT" ]; then
  echo "⚠️  quality-gate (ruff): $FILE"
  echo "$OUTPUT"
fi

exit 0
