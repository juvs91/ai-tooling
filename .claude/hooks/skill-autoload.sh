#!/usr/bin/env bash
# skill-autoload.sh — Autocarga STATELESS de workflow-coordinator
# Evento: UserPromptSubmit
# Mecanismo:
#   1. SIN estado, SIN flags, SIN archivos temporales
#   2. Siempre carga workflow-coordinator + routing table
#   3. Output minimal y no intrusivo
#   4. workflow-coordinator decide si mantenerse o descargarse

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

[ -z "$SESSION_ID" ] && exit 0
[ -f ".claude/no-skill-gate" ] && exit 0

SKILL_FILE=".agents/skills/workflow/workflow-coordinator/SKILL.md"
AGENTS_FILE="AGENTS.md"

if [ -f "$SKILL_FILE" ] && [ -f "$AGENTS_FILE" ]; then
  # Instrucción mandatory sin routing table — workflow-coordinator lee AGENTS.md él mismo
  echo "⚠️ MANDATORY: Call Skill tool NOW → skill=\"workflow-coordinator\" — then let it route."
fi

exit 0
