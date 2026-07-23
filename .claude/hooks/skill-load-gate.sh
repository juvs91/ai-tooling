#!/usr/bin/env bash
# distributable: true
# event: PreToolUse
# matcher: Agent|EnterPlanMode
# timeout: 5
# skill-load-gate.sh — bloquea Agent/EnterPlanMode si no se ha leído ningún SKILL.md
# en esta sesión. Complementa track-skill-load.sh (PostToolUse en Read).
# No verifica que sea el skill *correcto* (requeriría clasificación semántica) — solo
# que se haya cargado *alguno* antes de proceder.
# Bypass: crear .claude/no-skill-gate (mismo archivo que ya respeta skill-autoload.sh).

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0
[ -f ".claude/no-skill-gate" ] && exit 0

SESSION_ID=$(echo "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
MARKER=".claude/sessions/${SESSION_ID}-skill-loaded"
[ -f "$MARKER" ] && exit 0

cat >&2 <<'EOF'
SKILL GATE: aún no se detectó ningún Read de un SKILL.md en esta sesión.

Antes de usar Agent o EnterPlanMode:
  1. Revisa la tabla de routing en AGENTS.md
  2. Si una fila matchea el pedido del usuario, haz Read de ese SKILL.md
  3. Reintenta

Si genuinamente ninguna fila de la tabla aplica: crea .claude/no-skill-gate para bypass.
EOF
exit 2
