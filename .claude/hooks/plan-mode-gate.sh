#!/usr/bin/env bash
# plan-mode-gate.sh — Inyecta reglas de plan mode UNA VEZ por sesión
# Evento: UserPromptSubmit. Patrón: idéntico a skill-autoload.sh
# distributable: true
# event: UserPromptSubmit
# matcher: ""
# timeout: 5

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

[ -z "$SESSION_ID" ] && exit 0
[ -f ".claude/no-plan-gate" ] && exit 0   # escape hatch manual

SESSION_ID=$(echo "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
[ -z "$SESSION_ID" ] && exit 0

SESSIONS_DIR=".claude/sessions"
MARKER="${SESSIONS_DIR}/${SESSION_ID}-plan-gate"

mkdir -p "$SESSIONS_DIR"

# Asegurar sessions/ en .claude/.gitignore
CLAUDE_GITIGNORE=".claude/.gitignore"
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
