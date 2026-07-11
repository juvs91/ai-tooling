#!/usr/bin/env bash
# distributable: true
# event: PostToolUse
# matcher: Edit|Write|MultiEdit
# timeout: 15
# async: true
# Runs ruff linter on modified Python files after edits.
# Async — does not block Claude, just surfaces warnings.

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
