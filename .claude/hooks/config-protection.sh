#!/usr/bin/env bash
# Blocks accidental edits to linter/formatter/CI config files.
# Steers agent to fix code instead of weakening configs.
#
# Smart mode for pyproject.toml:
#   - ALLOWS edits to dependency/version sections ([project], [project.dependencies], etc.)
#   - BLOCKS edits to tool-config sections ([tool.ruff], [tool.black], etc.)
#
# Full-block files (any edit blocked):
#   ruff.toml, .ruff.toml, .pre-commit-config.yaml, .eslintrc*, .prettierrc*, commitlint.config*

FULL_BLOCK_PATTERNS=(
  "ruff.toml"
  ".ruff.toml"
  ".pre-commit-config.yaml"
  ".eslintrc*"
  ".prettierrc*"
  "commitlint.config*"
)

# Claude Code passes tool input as JSON via stdin
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE" ]; then
  exit 0
fi

BASENAME=$(basename "$FILE")

# ── pyproject.toml: smart check ────────────────────────────────────────────
if [ "$BASENAME" = "pyproject.toml" ]; then
  TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

  if [ "$TOOL" = "Edit" ]; then
    OLD=$(echo "$INPUT" | jq -r '.tool_input.old_string // empty')
    # Allow if the section being edited is NOT a [tool.*] config section
    # (i.e., version bumps, dependency changes, project metadata)
    if ! echo "$OLD" | grep -qE '^\s*\[tool\.'; then
      exit 0  # Not a linter/formatter config section — allow
    fi
    echo "🚫 config-protection: Editing '[tool.*]' config in 'pyproject.toml' is blocked." >&2
    echo "   Fix the code to comply with the linter config instead of weakening the rules." >&2
    echo "   To override: add '[skip-config-protection]' to your request or edit directly in the IDE." >&2
    exit 2
  fi

  # Write (full rewrite) — always block to avoid clobbering linter config
  echo "🚫 config-protection: Full rewrite of 'pyproject.toml' is blocked." >&2
  echo "   Use Edit tool for targeted changes. To override: edit directly in the IDE." >&2
  exit 2
fi

# ── Full-block patterns ────────────────────────────────────────────────────
for pattern in "${FULL_BLOCK_PATTERNS[@]}"; do
  case "$BASENAME" in
    $pattern)
      echo "🚫 config-protection: Editing '$BASENAME' is blocked." >&2
      echo "   Fix the code to comply with the config instead of weakening the rules." >&2
      echo "   To override: add '[skip-config-protection]' to your request or edit directly in the IDE." >&2
      exit 2
      ;;
  esac
done

exit 0
