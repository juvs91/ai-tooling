#!/bin/bash
# distributable: true
# event: PreToolUse
# matcher: Workflow|Agent
# timeout: 5
# worktree-isolation-gate.sh — PreToolUse hook (Workflow + Agent tools)
# Ref: ADR-0008-worktree-gitops-integration.md
#
# Advierte si:
#   1. Un Workflow script tiene parallel(agent()) sin isolation: 'worktree'
#   2. El tool Agent es invocado directamente con lenguaje que sugiere paralelismo
#      (el Agent tool NO soporta isolation: 'worktree' — usar Workflow en su lugar)
#
# Siempre exit 0 — advierte pero no bloquea.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

# ── Case 1: Workflow tool ────────────────────────────────────────────────────
if [ "$TOOL" = "Workflow" ]; then
  SCRIPT=$(echo "$INPUT" | jq -r '.tool_input.script // empty')
  [ -z "$SCRIPT" ] && exit 0

  # Ya tiene isolation — OK
  if echo "$SCRIPT" | grep -qE "isolation.*worktree"; then
    exit 0
  fi

  if echo "$SCRIPT" | grep -q "parallel(" && echo "$SCRIPT" | grep -q "agent("; then
    HAS_WRITES=0
    if echo "$SCRIPT" | grep -qE "Edit|Write|isolation|worktree|write|edit"; then
      HAS_WRITES=1
    fi

    if [ "$HAS_WRITES" -gt "0" ]; then
      echo "WORKTREE [warn]: parallel(agent()) con posibles writes sin isolation: 'worktree'." >&2
      echo "  Agrega { isolation: 'worktree' } a cada agent() para evitar conflictos en disco." >&2
      echo "  Ref: ADR-0008-worktree-gitops-integration.md" >&2
    else
      echo "WORKTREE [info]: parallel(agent()) sin isolation: 'worktree' detectado." >&2
      echo "  Si los agentes solo leen archivos, es seguro ignorar este aviso." >&2
      echo "  Si escriben archivos, agrega isolation: 'worktree' para evitar conflictos." >&2
    fi
  fi

  exit 0
fi

# ── Case 2: Agent tool (no soporta isolation: 'worktree') ───────────────────
if [ "$TOOL" = "Agent" ]; then
  PROMPT=$(echo "$INPUT" | jq -r '.tool_input.prompt // empty')
  [ -z "$PROMPT" ] && exit 0

  # Detecta lenguaje que sugiere múltiples agentes paralelos siendo lanzados
  if echo "$PROMPT" | grep -qiE "parallel|en paralelo|simultán|concurrente|múltiples agentes|multiple agents|lanza.*agent|spawn.*agent"; then
    echo "WORKTREE [warn]: Agent tool no soporta isolation: 'worktree'." >&2
    echo "  Para paralelismo con aislamiento de archivos, usa el Workflow tool:" >&2
    echo "    await parallel([" >&2
    echo "      () => agent(task1, { isolation: 'worktree' })," >&2
    echo "      () => agent(task2, { isolation: 'worktree' })," >&2
    echo "    ]);" >&2
    echo "  Para análisis read-only, el Agent tool directo es seguro." >&2
    echo "  Ref: ADR-0008-worktree-gitops-integration.md" >&2
  fi

  exit 0
fi

exit 0
