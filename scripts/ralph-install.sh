#!/usr/bin/env bash
# ralph-install.sh — symlink ralph files from this repo into the local environment
# Usage: ./scripts/ralph-install.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Ralph install from: $REPO_ROOT"

# --- ralph_loop.sh ---
RALPH_DIR="$HOME/.ralph"
TARGET="$RALPH_DIR/ralph_loop.sh"

if [[ ! -d "$RALPH_DIR" ]]; then
    echo "ERROR: ralph not installed at ~/.ralph — install ralph first"
    exit 1
fi

if [[ -f "$TARGET" && ! -L "$TARGET" ]]; then
    cp "$TARGET" "${TARGET}.bak"
    echo "  Backed up: ${TARGET}.bak"
fi

ln -sf "$REPO_ROOT/vendor/ralph/ralph_loop.sh" "$TARGET"
echo "OK: ralph_loop.sh → $TARGET"

# --- claude-stdio (project-specific) ---
# This file lives inside each project's .ralph/bin/ directory.
# Pass the project root as the first argument, or it will be skipped.
if [[ "${1:-}" != "" ]]; then
    PROJECT_ROOT="$1"
    CLAUDE_STDIO_TARGET="$PROJECT_ROOT/.ralph/bin/claude-stdio"

    if [[ ! -d "$PROJECT_ROOT/.ralph/bin" ]]; then
        echo "ERROR: $PROJECT_ROOT/.ralph/bin does not exist"
        exit 1
    fi

    if [[ -f "$CLAUDE_STDIO_TARGET" && ! -L "$CLAUDE_STDIO_TARGET" ]]; then
        cp "$CLAUDE_STDIO_TARGET" "${CLAUDE_STDIO_TARGET}.bak"
        echo "  Backed up: ${CLAUDE_STDIO_TARGET}.bak"
    fi

    ln -sf "$REPO_ROOT/vendor/ralph/claude-stdio" "$CLAUDE_STDIO_TARGET"
    echo "OK: claude-stdio → $CLAUDE_STDIO_TARGET"
else
    echo ""
    echo "NOTE: claude-stdio symlink is project-specific. To symlink for a project, run:"
    echo "  $0 /path/to/project"
fi

echo ""
echo "Done."
