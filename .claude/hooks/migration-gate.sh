#!/usr/bin/env bash
# distributable: true
# event: PostToolUse
# matcher: Edit|Write|MultiEdit
# timeout: 10
# migration-gate.sh — PostToolUse hook
# Fires when a SQLAlchemy model file is edited, reminding Claude to run Alembic.
# Advisory only (exit 0 always) — output is shown to Claude as context.
#
# Input contract: JSON via stdin
# {"tool_name":"Edit","tool_input":{"file_path":"/path/to/models.py",...}}

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Only trigger on Python files
[[ "$FILE" == *.py ]] || exit 0
[ -f "$FILE" ] || exit 0

# Check if this looks like a SQLAlchemy model file:
# - Path contains /models/ or is named models.py / db.py / database.py
# - OR file content contains Base/declarative_base/mapped_column/Column
is_model_path=0
case "$FILE" in
    */models/*.py|*/models.py|*/db.py|*/database.py|*/schemas.py) is_model_path=1 ;;
esac

is_model_content=0
if grep -qE '(declarative_base|DeclarativeBase|mapped_column|Column\(|relationship\()' "$FILE" 2>/dev/null; then
    is_model_content=1
fi

if [ "$is_model_path" -eq 0 ] && [ "$is_model_content" -eq 0 ]; then
    exit 0
fi

echo ""
echo "🗄 MODEL FILE MODIFIED: $FILE"
echo "  Alembic migration required after SQLAlchemy model changes."
echo "  MANDATORY STEPS:"
echo "    1. alembic revision --autogenerate -m 'describe_your_change'"
echo "    2. alembic upgrade head"
echo "    3. alembic current  (verify new revision is applied)"
echo "  Do NOT proceed to API/frontend work until migration is applied."
echo ""

exit 0
