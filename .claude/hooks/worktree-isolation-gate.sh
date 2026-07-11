#!/bin/bash
# distributable: true
# event: PreToolUse
# matcher: Workflow
# timeout: 5
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

HAS_WRITES=0
if echo "$SCRIPT" | grep -qE "isolation.*worktree"; then
  # Ya tiene isolation — permitir siempre
  exit 0
fi

if echo "$SCRIPT" | grep -q "parallel(" && echo "$SCRIPT" | grep -q "agent("; then
  # Detecta si algún agente en el script puede escribir archivos
  if echo "$SCRIPT" | grep -qE "Edit|Write|isolation|worktree|write|edit"; then
    HAS_WRITES=1
  fi
fi

if [ "$HAS_WRITES" -gt "0" ]; then
  echo "WORKTREE [warn]: parallel(agent()) detectado con posibles writes." >&2
  echo "  Si los agentes paralelos escriben al mismo repo, usa isolation: 'worktree'." >&2
  echo "  Para workflows de solo lectura o escrituras a dirs distintos, es seguro ignorar." >&2
  echo "  Ref: ADR-0008-worktree-gitops-integration.md" >&2
fi

# Workflow de solo lectura — advertir pero permitir
if echo "$SCRIPT" | grep -q "parallel(" && echo "$SCRIPT" | grep -q "agent("; then
  echo "WORKTREE [warn]: parallel(agent()) sin isolation: 'worktree' detectado." >&2
  echo "  Si los agentes escriben archivos, agrega isolation: 'worktree' para evitar conflictos." >&2
fi

exit 0
