#!/bin/bash
# block-dangerous.sh — PreToolUse hook para Bash
# Exit 0 = permitir, Exit 2 = bloquear (stderr = razón)
#
# Claude Code pasa JSON por stdin con la estructura:
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Si no hay comando (tool_input vacío), permitir
[ -z "$COMMAND" ] && exit 0

# ── Reglas de bloqueo ──────────────────────────────────────────────

# 1. rm -rf con paths peligrosos (/, ~, $HOME)
if echo "$COMMAND" | grep -qE 'rm\s+-[a-zA-Z]*r[a-zA-Z]*f.*(\s+/\s|\s+/\s*$|\s+~/|\s+\$HOME)'; then
  echo "BLOCKED: 'rm -rf' en path peligroso (/, ~). Usa paths específicos." >&2
  exit 2
fi

# 2. git push --force (sin --force-with-lease)
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*(-f|--force)(\s|$)' && ! echo "$COMMAND" | grep -q 'force-with-lease'; then
  echo "BLOCKED: 'git push --force' no permitido. Usa '--force-with-lease' o pide revisión." >&2
  exit 2
fi

# 3. git reset --hard
if echo "$COMMAND" | grep -qE 'git\s+reset\s+--hard'; then
  echo "BLOCKED: 'git reset --hard' no permitido. Usa 'git revert' o 'git stash'." >&2
  exit 2
fi

# 4. git clean -f (borra untracked files)
if echo "$COMMAND" | grep -qE 'git\s+clean\s+-[a-zA-Z]*f'; then
  echo "BLOCKED: 'git clean -f' no permitido. Revisa untracked files manualmente." >&2
  exit 2
fi

# Todo lo demás: permitir
exit 0
