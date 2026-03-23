#!/usr/bin/env bash
# Blocks accidental edits to linter/formatter/CI config files.
# Steers agent to fix code instead of weakening configs.

PROTECTED_PATTERNS=(
  "pyproject.toml"
  "ruff.toml"
  ".ruff.toml"
  ".pre-commit-config.yaml"
  ".eslintrc*"
  ".prettierrc*"
  "commitlint.config*"
)

# Claude Code passes the file path via CLAUDE_TOOL_INPUT_FILE_PATH env var
FILE="${CLAUDE_TOOL_INPUT_FILE_PATH:-}"

if [ -z "$FILE" ]; then
  exit 0
fi

BASENAME=$(basename "$FILE")

for pattern in "${PROTECTED_PATTERNS[@]}"; do
  # Simple glob match
  case "$BASENAME" in
    $pattern)
      echo "🚫 config-protection: Editing '$BASENAME' is blocked."
      echo "   Fix the code to comply with the config instead of weakening the rules."
      echo "   To override: add '[skip-config-protection]' to your request."
      exit 2
      ;;
  esac
done

exit 0
