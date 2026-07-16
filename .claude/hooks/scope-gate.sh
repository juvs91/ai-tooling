#!/bin/bash
# distributable: true
# event: PreToolUse
# matcher: Edit|Write
# timeout: 5
# scope-gate.sh — PreToolUse hook (Edit|Write)
#
# If .claude/task-scope.json exists, enforces write scope per declared mode.
# Without task-scope.json: no restriction (opt-in per task).
#
# Modes:
#   analysis  — writes only to paths in analysis_write_paths[] (or generic fallback)
#   validate  — no writes at all
#   synthesize — writes only to doc directories and root-level markdown
#   build|full — respects allowed_patterns[] (original behavior)
#
# analysis_write_paths[] in task-scope.json is set by intent-bootstrap.sh
# based on what doc directories actually exist in the project.

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
SCOPE_FILE="$CWD/.claude/task-scope.json"
[ -f "$SCOPE_FILE" ] || exit 0

RELATIVE="${FILE#$CWD/}"

# Always allow writing task-scope.json itself
[ "$RELATIVE" = ".claude/task-scope.json" ] && exit 0

TASK_NAME=$(jq -r '.task // .task_id // "current task"' "$SCOPE_FILE")
STEP=$(jq -r '.current_step // ""' "$SCOPE_FILE")
MODE=$(jq -r '.mode // "full"' "$SCOPE_FILE")
BASE_MODE=$(echo "$MODE" | cut -d: -f1)

case "$BASE_MODE" in

  analysis)
    # Read project-specific write paths from task-scope.json (set by intent-bootstrap)
    WRITE_PATHS=$(jq -r '.analysis_write_paths[]? // empty' "$SCOPE_FILE" 2>/dev/null)

    if [ -n "$WRITE_PATHS" ]; then
      while IFS= read -r wpath; do
        [ -z "$wpath" ] && continue
        case "$RELATIVE" in
          ${wpath}/*|${wpath}) exit 0 ;;
        esac
      done <<< "$WRITE_PATHS"
      ALLOWED_DISPLAY=$(echo "$WRITE_PATHS" | tr '\n' ' ')
    else
      # Generic fallback when analysis_write_paths not set
      case "$RELATIVE" in
        .claude/plans/*|findings/*|notes/*) exit 0 ;;
      esac
      ALLOWED_DISPLAY=".claude/plans/  findings/  notes/"
    fi

    echo "scope-gate[analysis]: '$RELATIVE' is outside analysis scope." >&2
    echo "  Allowed: ${ALLOWED_DISPLAY}" >&2
    echo "  To change: set 'analysis_write_paths' in .claude/task-scope.json" >&2
    echo "  Analyze ≠ Document. Use mode=synthesize to create docs." >&2
    exit 2
    ;;

  validate)
    echo "scope-gate[validate]: no writes allowed in validate mode." >&2
    exit 2
    ;;

  synthesize)
    # Read docs_dirs set by intent-bootstrap (discovered from actual project structure)
    DOCS_DIRS=$(jq -r '.docs_dirs[]? // empty' "$SCOPE_FILE" 2>/dev/null)

    if [ -n "$DOCS_DIRS" ]; then
      while IFS= read -r ddir; do
        [ -z "$ddir" ] && continue
        case "$RELATIVE" in
          ${ddir}/*|${ddir}) exit 0 ;;
        esac
      done <<< "$DOCS_DIRS"
      DOCS_DISPLAY=$(echo "$DOCS_DIRS" | tr '\n' ' ')
    else
      # Generic fallback when docs_dirs not set in task-scope.json
      case "$RELATIVE" in
        ai-notes/*|docs/*|notes/*|documentation/*|wiki/*) exit 0 ;;
      esac
      DOCS_DISPLAY="ai-notes/ docs/ notes/ documentation/ wiki/"
    fi

    # Root-level markdown always allowed (README.md, CHANGELOG.md, etc.)
    case "$RELATIVE" in
      *.md)
        [[ "$RELATIVE" == */* ]] || exit 0
        ;;
    esac

    echo "scope-gate[synthesize]: '$RELATIVE' is not a documentation path." >&2
    echo "  Allowed: ${DOCS_DISPLAY}root-level *.md" >&2
    echo "  To change: set 'docs_dirs' in .claude/task-scope.json" >&2
    exit 2
    ;;

  build|full)
    # Fall through to allowed_patterns[] check below
    ;;

esac

# build|full: respect allowed_patterns[] from task-scope.json
ALLOWED=$(jq -r '.allowed_patterns // [] | .[]' "$SCOPE_FILE" 2>/dev/null)
[ -z "$ALLOWED" ] && exit 0

while IFS= read -r pattern; do
    case "$RELATIVE" in $pattern) exit 0 ;; esac
done <<< "$ALLOWED"

ALLOWED_LIST=$(jq -r '.allowed_patterns | join(", ")' "$SCOPE_FILE")
echo "scope-gate: '$RELATIVE' is outside task scope." >&2
echo "  Task: '$TASK_NAME' | Step: '${STEP:-not set}'" >&2
echo "  Allowed patterns: $ALLOWED_LIST" >&2
echo "  → Update .claude/task-scope.json if you need a wider scope." >&2
exit 2
