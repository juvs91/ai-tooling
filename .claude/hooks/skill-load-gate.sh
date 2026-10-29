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
#
# ADR-0073: el marker se busca en la raíz canónica de la sesión (state_root) —
# tras un `cd` o dentro de un worktree, el cwd del proceso es otro directorio y
# el gate bloqueaba aunque el skill sí se hubiera leído. También se acepta el
# path relativo al cwd (compatibilidad con un track-skill-load.sh viejo).

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
[ -z "$SESSION_ID" ] && exit 0

LIB="$(dirname "${BASH_SOURCE[0]}")/task-scope-lib.sh"
ROOT=""
if [ -f "$LIB" ] && . "$LIB" && declare -F state_root >/dev/null; then
  ROOT=$(state_root "$CWD")
fi
ROOT=${ROOT:-$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)}
ROOT=${ROOT:-$CWD}

[ -f "$ROOT/.claude/no-skill-gate" ] && exit 0
[ -f ".claude/no-skill-gate" ] && exit 0

SESSION_ID=$(echo "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
[ -f "$ROOT/.claude/sessions/${SESSION_ID}-skill-loaded" ] && exit 0
[ -f ".claude/sessions/${SESSION_ID}-skill-loaded" ] && exit 0

cat >&2 <<'EOF'
SKILL GATE: aún no se detectó ningún Read de un SKILL.md en esta sesión.

Antes de usar Agent o EnterPlanMode:
  1. Revisa la tabla de routing en AGENTS.md
  2. Si una fila matchea el pedido del usuario, haz Read de ese SKILL.md
  3. Reintenta

Si genuinamente ninguna fila de la tabla aplica: crea .claude/no-skill-gate para bypass.
EOF
exit 2
