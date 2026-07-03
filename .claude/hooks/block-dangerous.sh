#!/bin/bash
# block-dangerous.sh — PreToolUse hook (cualquier tool, incluyendo Bash)
# Exit 0 = permitir, Exit 2 = bloquear (stderr = razón)
#
# Claude Code pasa JSON por stdin:
#   { "tool_name": "...", "tool_input": { ... } }

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
TOOL_INPUT=$(echo "$INPUT" | jq -c '.tool_input // {}')

# ── Sección 1: Parámetros destructivos en cualquier tool ─────────────────
# Bloquea cualquier herramienta que tenga parámetros con valor true
# que indiquen operación destructiva irreversible.
# Extensible: agrega entradas al array DANGEROUS_PARAMS sin tocar el resto.
# jq -r (raw): evita comillas JSON en output; sin -r, join(", ") retorna
# '"discard_changes"' (con comillas) y [ -n ] siempre sería true.
DANGEROUS_PARAMS='["discard_changes","force_delete","wipe_data","purge","drop_all"]'

blocked=$(echo "$TOOL_INPUT" | jq -r --argjson params "$DANGEROUS_PARAMS" '
  to_entries
  | map(select(
      (.key as $k | $params | index($k) != null)
      and .value == true
    ))
  | map(.key)
  | join(", ")
')

if [ -n "$blocked" ]; then
  echo "BLOCKED: '$TOOL' con parámetros destructivos ($blocked=true) — confirmar manualmente si requerido" >&2
  exit 2
fi

# Si no hay comando Bash, ya no hay más reglas que aplicar
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

# 5. rm -rf sobre un git worktree activo (Ref: ADR-0008)
if echo "$COMMAND" | grep -qE 'rm\s+-[a-zA-Z]*r[a-zA-Z]*f'; then
  for wt_path in $(git worktree list --porcelain 2>/dev/null | awk '/^worktree /{print $2}'); do
    if echo "$COMMAND" | grep -qF "$wt_path"; then
      echo "BLOCKED: '$wt_path' es un git worktree activo. Usa: git worktree remove $wt_path" >&2
      exit 2
    fi
  done
fi

# Todo lo demás: permitir
exit 0
