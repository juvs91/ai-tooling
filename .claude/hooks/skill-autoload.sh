#!/usr/bin/env bash
# skill-autoload.sh — Autocarga de workflow-coordinator UNA VEZ por sesión
# Evento: UserPromptSubmit
# Mecanismo:
#   1. Lee SESSION_ID del JSON de entrada
#   2. Si ya vio esta sesión (.claude/sessions/<ID>), sale silencioso
#   3. Si es la primera vez, emite la instrucción y marca la sesión
#   4. Limpia marcadores de sesiones >48h para evitar acumulación

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

[ -z "$SESSION_ID" ] && exit 0
[ -f ".claude/no-skill-gate" ] && exit 0

# Sanitizar SESSION_ID para uso seguro como nombre de archivo
SESSION_ID=$(echo "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
[ -z "$SESSION_ID" ] && exit 0

SESSIONS_DIR=".claude/sessions"
SESSION_MARKER="$SESSIONS_DIR/$SESSION_ID"

mkdir -p "$SESSIONS_DIR"

# Asegurar sessions/ en .claude/.gitignore para no commitear estado de sesión
CLAUDE_GITIGNORE=".claude/.gitignore"
if [ ! -f "$CLAUDE_GITIGNORE" ] || ! grep -qF 'sessions/' "$CLAUDE_GITIGNORE" 2>/dev/null; then
  echo 'sessions/' >> "$CLAUDE_GITIGNORE"
fi

# Limpieza lazy: borrar marcadores de sesiones más viejos de 48h
find "$SESSIONS_DIR" -maxdepth 1 -type f -mmin +2880 -delete 2>/dev/null || true

# Si ya procesamos esta sesión → salir silencioso (no repetir la instrucción)
[ -f "$SESSION_MARKER" ] && exit 0

SKILL_FILE=".agents/skills/workflow/workflow-coordinator/SKILL.md"
AGENTS_FILE="AGENTS.md"

if [ -f "$SKILL_FILE" ] && [ -f "$AGENTS_FILE" ]; then
  # Emitir instrucción y marcar DESPUÉS para garantizar que Claude la recibe
  echo "⚠️ MANDATORY: Call Skill tool NOW → skill=\"workflow-coordinator\" — then let it route."
  touch "$SESSION_MARKER" 2>/dev/null || true
fi

exit 0
