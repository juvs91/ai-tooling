#!/usr/bin/env bash
# distributable: true
# event: PostToolUse
# matcher: Edit|Write|MultiEdit
# timeout: 10
# migration-gate.sh — PostToolUse hook
# Fires when a SQLAlchemy/SQLModel model file is edited in a project that uses
# Alembic, reminding Claude to generate a migration.
# Advisory only (exit 0 always).
#
# Output: PostToolUse plain stdout goes to the debug log and never reaches
# Claude — the advisory must be emitted as
# {"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":…}}.
#
# Input contract: JSON via stdin
# {"session_id":"…","cwd":"…","tool_name":"Edit","tool_input":{"file_path":"/path/to/models.py",...}}

INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // "."' 2>/dev/null)
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null | tr -cd 'a-zA-Z0-9_-')

# Only trigger on existing Python files
[[ "$FILE" == *.py ]] || exit 0
case "$FILE" in /*) ;; *) FILE="$CWD/$FILE" ;; esac
[ -f "$FILE" ] || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-}"
[ -n "$ROOT" ] && [ -d "$ROOT" ] || ROOT=$(git -C "$(dirname "$FILE")" rev-parse --show-toplevel 2>/dev/null || echo "/")

# 1. The project must actually use Alembic: nearest alembic.ini (or a
#    pyproject.toml with [tool.alembic]) between the file and the project root.
#    Without this, every repo with Pydantic schemas or ML "models/" got noise.
ALEMBIC_DIR=""
dir=$(dirname "$FILE")
while :; do
  if [ -f "$dir/alembic.ini" ] || grep -qs '^\[tool\.alembic\]' "$dir/pyproject.toml"; then
    ALEMBIC_DIR="$dir"
    break
  fi
  [ "$dir" = "$ROOT" ] || [ "$dir" = "/" ] && break
  dir=$(dirname "$dir")
done
[ -n "$ALEMBIC_DIR" ] || exit 0

# 2. The file must import SQLAlchemy/SQLModel AND declare ORM structure.
#    Path names alone (models.py, schemas.py) and bare `Column(` (pandas) no
#    longer trigger.
grep -qE '^[[:space:]]*(from|import)[[:space:]]+(sqlalchemy|sqlmodel)' "$FILE" 2>/dev/null || exit 0
grep -qE '(declarative_base|DeclarativeBase|mapped_column|Mapped\[|__tablename__|Column\(|Table\(|table=True)' "$FILE" 2>/dev/null || exit 0

# 3. Once per file per session — repeating on every edit is noise.
if [ -n "$SESSION_ID" ]; then
  STATE_DIR="$ROOT/.claude/sessions"
  FILE_KEY=$(printf '%s' "$FILE" | cksum | cut -d' ' -f1)
  MARKER="$STATE_DIR/${SESSION_ID}-migration-${FILE_KEY}"
  [ -f "$MARKER" ] && exit 0
  mkdir -p "$STATE_DIR" 2>/dev/null && touch "$MARKER" 2>/dev/null
fi

REL_FILE="${FILE#$ROOT/}"
REL_ALEMBIC="${ALEMBIC_DIR#$ROOT/}"
[ "$REL_ALEMBIC" = "$ALEMBIC_DIR" ] && [ "$ALEMBIC_DIR" = "$ROOT" ] && REL_ALEMBIC="."

MSG="MODEL FILE MODIFIED: ${REL_FILE}
SQLAlchemy model changes need an Alembic migration (alembic project: ${REL_ALEMBIC}).
  1. cd ${REL_ALEMBIC} && alembic revision --autogenerate -m '<describe_change>'
  2. Review the generated revision — autogenerate misses renames, server defaults and some type changes.
  3. Apply it ONLY against a local/dev database. Check which database the environment points to before running 'alembic upgrade head'; never run it against QA/prod from an agent session.
Finish the migration before building API/frontend work on top of the new model."

jq -n --arg ctx "$MSG" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $ctx}}'

exit 0
