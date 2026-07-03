#!/bin/bash
# worktree-isolation-gate.sh — PreToolUse hook (Workflow tool)
# Ref: ADR-0008-worktree-gitops-integration.md
#
# Advierte si un Workflow script tiene parallel(agent()) sin isolation: 'worktree'.
# Agentes paralelos que escriben archivos sin isolation pueden conflictuar en disco.
#
# Siempre exit 0 — advierte pero no bloquea (puede ser workflow de solo lectura).

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL" != "Workflow" ] && exit 0

SCRIPT=$(echo "$INPUT" | jq -r '.tool_input.script // empty')
[ -z "$SCRIPT" ] && exit 0

if echo "$SCRIPT" | grep -q "parallel(" \
   && echo "$SCRIPT" | grep -q "agent(" \
   && ! echo "$SCRIPT" | grep -q "isolation.*worktree"; then
  echo "WORKTREE: parallel(agent()) sin isolation: 'worktree' detectado." >&2
  echo "  Agentes escritores paralelos pueden conflictuar en disco." >&2
  echo "  Considera: agent(prompt, { isolation: 'worktree' }) para los que escriben archivos." >&2
fi

exit 0
