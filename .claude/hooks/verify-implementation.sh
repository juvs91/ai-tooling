#!/usr/bin/env bash
# verify-implementation.sh — PostToolUse hook
# Scans newly written/edited Python files for unimplemented stub functions.
# Advisory only (exit 0 always) — output is shown to Claude as context.
#
# Input contract: JSON via stdin
# {"tool_name":"Edit","tool_input":{"file_path":"/path/to/file.py",...}}

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Only check Python files
[[ "$FILE" == *.py ]] || exit 0
[ -f "$FILE" ] || exit 0

# Use Python AST to find functions whose sole body is `pass` or `...`
STUBS=$(python3 - "$FILE" 2>/dev/null <<'PYEOF'
import ast, sys

path = sys.argv[1]
try:
    source = open(path).read()
    tree = ast.parse(source)
except Exception:
    sys.exit(0)

stubs = []
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    body = node.body
    if len(body) != 1:
        continue
    stmt = body[0]
    # `pass` statement
    if isinstance(stmt, ast.Pass):
        stubs.append(node.name)
        continue
    # `...` (Ellipsis) expression
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is ...:
        stubs.append(node.name)

if stubs:
    print(",".join(stubs))
PYEOF
)

# Also scan for TODO/FIXME/STUB comment lines (grep -c exits 1 on 0 matches; use arithmetic)
TODO_COUNT=$(grep -cE '^\s*#\s*(TODO|FIXME|STUB|PLACEHOLDER)' "$FILE" 2>/dev/null; true)
TODO_COUNT=$(( ${TODO_COUNT:-0} + 0 ))

if [ -n "$STUBS" ] || [ "$TODO_COUNT" -gt 0 ]; then
    echo ""
    echo "⚠ IMPLEMENTATION INCOMPLETE: $FILE"
    if [ -n "$STUBS" ]; then
        echo "  Unimplemented functions (body is only pass/...): $STUBS"
        echo "  ACTION: Replace pass/... with real implementation code"
    fi
    if [ "$TODO_COUNT" -gt 0 ]; then
        echo "  TODO/FIXME comments found: $TODO_COUNT (resolve before finishing)"
    fi
    echo "  RULE: Never leave pass stubs — implement the full logic now."
    echo ""
fi

exit 0
