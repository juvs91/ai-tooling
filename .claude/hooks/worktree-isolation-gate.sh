#!/bin/bash
# distributable: true
# event: PreToolUse
# matcher: Workflow
# timeout: 5
# worktree-isolation-gate.sh — PreToolUse hook (Workflow)
# Ref: ADR-0044-worktree-gitops-integration.md, ADR-0073-worktrees-nativos-harness.md
#
# Advierte si un script de Workflow lanza más agent() de los que llevan
# isolation: 'worktree' — agentes en paralelo escribiendo el mismo árbol se pisan.
#
# Siempre exit 0 — advierte pero no bloquea. El aviso va como
# hookSpecificOutput.additionalContext: el stderr de un PreToolUse con exit 0
# NUNCA llega al modelo. Sin permissionDecision a propósito: "allow" saltaría
# el prompt de permisos (auto-aprobaría el Workflow) y "defer" descarta el
# additionalContext.
#
# ADR-0073: se eliminó el caso del tool Agent. Decía que Agent no soporta
# isolation: 'worktree' — falso (sí lo soporta) — y decidía por palabras del
# prompt ("en paralelo"), que no dicen nada de concurrencia real.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL" = "Workflow" ] || exit 0

SCRIPT=$(echo "$INPUT" | jq -r '.tool_input.script // empty')
[ -z "$SCRIPT" ] && exit 0

# Sin paralelismo no hay conflicto en disco
echo "$SCRIPT" | grep -qE 'parallel\(|pipeline\(|Promise\.all' || exit 0

AGENTS=$(printf '%s' "$SCRIPT" | grep -oE '(^|[^a-zA-Z0-9_.])agent\(' | wc -l | tr -d ' ')
ISOLATED=$(printf '%s' "$SCRIPT" | tr '\n' ' ' | grep -oE "isolation[[:space:]]*:[[:space:]]*['\"]worktree['\"]" | wc -l | tr -d ' ')

[ "$AGENTS" -gt 0 ] || exit 0
[ "$ISOLATED" -ge "$AGENTS" ] && exit 0

MSG="WORKTREE: el Workflow lanza $AGENTS agent() en paralelo y solo $ISOLATED con isolation: 'worktree'. Si esos agentes ESCRIBEN archivos, agrega { isolation: 'worktree' } a cada agent() para que no se pisen en disco; si solo leen, ignora este aviso. Ref: ADR-0073."
jq -n --arg ctx "$MSG" '{hookSpecificOutput: {hookEventName: "PreToolUse", additionalContext: $ctx}}'
exit 0
