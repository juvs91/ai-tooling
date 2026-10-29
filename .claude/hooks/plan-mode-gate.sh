#!/usr/bin/env bash
# plan-mode-gate.sh — Inyecta reglas de plan mode UNA VEZ por sesión
# Evento: UserPromptSubmit. Patrón: idéntico a skill-autoload.sh
# distributable: true
# event: UserPromptSubmit
# matcher: ""
# timeout: 5

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')

[ -z "$SESSION_ID" ] && exit 0

# ADR-0073: estado en la raíz canónica de la sesión, no en el cwd del proceso
# (que sigue a un `cd` o a un worktree).
LIB="$(dirname "${BASH_SOURCE[0]}")/task-scope-lib.sh"
ROOT=""
if [ -f "$LIB" ] && . "$LIB" && declare -F state_root >/dev/null; then
  ROOT=$(state_root "$CWD")
fi
ROOT=${ROOT:-$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)}
ROOT=${ROOT:-$CWD}

[ -f "$ROOT/.claude/no-plan-gate" ] && exit 0   # escape hatch manual

SESSION_ID=$(echo "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
[ -z "$SESSION_ID" ] && exit 0

SESSIONS_DIR="$ROOT/.claude/sessions"
MARKER="${SESSIONS_DIR}/${SESSION_ID}-plan-gate"

mkdir -p "$SESSIONS_DIR"

# Asegurar sessions/ en .claude/.gitignore
CLAUDE_GITIGNORE="$ROOT/.claude/.gitignore"
if [ ! -f "$CLAUDE_GITIGNORE" ] || ! grep -qF 'sessions/' "$CLAUDE_GITIGNORE" 2>/dev/null; then
  echo 'sessions/' >> "$CLAUDE_GITIGNORE"
fi

# Limpieza lazy: borrar marcadores >48h
find "$SESSIONS_DIR" -maxdepth 1 -type f -mmin +2880 -delete 2>/dev/null || true

# Si ya se emitió en esta sesión → salir silencioso
[ -f "$MARKER" ] && exit 0

cat << 'EOF'
🚨 MANDATO OBLIGATORIO — PLAN MODE (no-negociable, aplica toda la sesión):
DEBES llamar EnterPlanMode ANTES de responder si: (a) la tarea toca ≥3 archivos, (b) el usuario dice "planea/diseña/propón/cómo harías/qué harías", o (c) el request es ambiguo/arquitectónico.
DEBES llamar ExitPlanMode cuando el plan esté completo y el usuario apruebe implementar.
VIOLACIÓN: responder con pasos/diseño sin estar en plan mode = error grave. DETENTE y llama EnterPlanMode ahora si aplica.
EOF

touch "$MARKER" 2>/dev/null || true
exit 0
