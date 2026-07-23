#!/usr/bin/env bash
# distributable: true
# event: PostToolUse
# matcher: *
# timeout: 5
# track-skill-load.sh — marca la sesión cuando se lee un SKILL.md de .agents/skills/,
# sin importar qué tool se usó (Read, Bash cat/head/tail, Grep, etc.) ni en qué campo
# del tool_input viene el path.
# Complementa skill-load-gate.sh (PreToolUse en Agent|EnterPlanMode).
# Reusa el directorio .claude/sessions/ que skill-autoload.sh ya crea y gitignora.

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0
SESSION_ID=$(echo "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')

MATCHED=0
echo "$INPUT" | jq -c '.tool_input // {}' 2>/dev/null | grep -qE '\.agents/skills/[^"]*SKILL\.md' && MATCHED=1

[ "$MATCHED" = "1" ] || exit 0
mkdir -p .claude/sessions
touch ".claude/sessions/${SESSION_ID}-skill-loaded"
exit 0
