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
#
# ADR-0073: el estado vive en la raíz canónica (state_root), no en el cwd del
# proceso — tras un `cd` o dentro de un worktree el marker quedaba en otro
# directorio y skill-load-gate bloqueaba Agent/EnterPlanMode. Además, en el hilo
# principal (eventos sin agent_id) registra <sid>-main-top = toplevel actual;
# task-verify.sh lo usa para no borrar el scope del padre desde un subagente.

INPUT=$(cat)
eval "$(echo "$INPUT" | jq -r '@sh "SESSION_ID=\(.session_id // "") AGENT_ID=\(.agent_id // "") CWD=\(.cwd // ".")"' 2>/dev/null)"
[ -z "${SESSION_ID:-}" ] && exit 0
SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
[ -z "$SESSION_ID" ] && exit 0

MATCHED=0
echo "$INPUT" | jq -c '.tool_input // {}' 2>/dev/null | grep -qE '\.agents/skills/[^"]*SKILL\.md' && MATCHED=1

# Guard de costo: el registro main-top solo se recalcula si el .cwd cambió
# (cache sin forks en un dir privado del usuario). Subagentes: ni cache ni registro.
CACHE=""
if [ -z "$AGENT_ID" ]; then
  CDIR="${TMPDIR:-/tmp}"; CDIR="${CDIR%/}/claude-$(id -u)"
  if { [ -d "$CDIR" ] || mkdir -m 700 "$CDIR" 2>/dev/null; } && [ -O "$CDIR" ] && [ ! -L "$CDIR" ]; then
    CACHE="$CDIR/tsl-$SESSION_ID.cwd"
  fi
fi
LAST=""; LAST_REG=""
[ -n "$CACHE" ] && [ -f "$CACHE" ] && { IFS= read -r LAST; IFS= read -r LAST_REG; } < "$CACHE"

# Camino rápido (sin forks de git): nada que marcar y, si aplica, cwd sin
# cambio con el registro aún presente.
if [ "$MATCHED" = "0" ]; then
  [ -z "$CACHE" ] && exit 0
  # LAST_REG "-" = cwd fuera de git (no hay toplevel que registrar)
  [ "$LAST" = "$CWD" ] && { [ "$LAST_REG" = "-" ] || { [ -n "$LAST_REG" ] && [ -f "$LAST_REG" ]; }; } && exit 0
fi

LIB="$(dirname "${BASH_SOURCE[0]}")/task-scope-lib.sh"
ROOT=""
if [ -f "$LIB" ] && . "$LIB" && declare -F state_root >/dev/null; then
  ROOT=$(state_root "$CWD")
fi
ROOT=${ROOT:-$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)}
ROOT=${ROOT:-$CWD}
SESSIONS="$ROOT/.claude/sessions"

if [ "$MATCHED" = "1" ]; then
  mkdir -p "$SESSIONS"
  touch "$SESSIONS/${SESSION_ID}-skill-loaded"
  # Transición (una release): consumers con un skill-load-gate viejo que aún
  # lee el path relativo al cwd. Solo si ese .claude ya gitignora sessions/.
  if [ -f .claude/.gitignore ] && grep -qF 'sessions/' .claude/.gitignore 2>/dev/null; then
    mkdir -p .claude/sessions && touch ".claude/sessions/${SESSION_ID}-skill-loaded"
  fi
fi

if [ -n "$CACHE" ]; then
  REG="$SESSIONS/${SESSION_ID}-main-top"
  if [ "$LAST" != "$CWD" ] || [ ! -f "$REG" ]; then
    TOP=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
    if [ -n "$TOP" ]; then
      declare -F canon_path >/dev/null && TOP=$(canon_path "$TOP")
      mkdir -p "$SESSIONS" && printf '%s\n' "$TOP" > "$REG"
    else
      REG="-"
    fi
    TMPC=$(mktemp "$CACHE.XXXXXX" 2>/dev/null) && printf '%s\n%s\n' "$CWD" "$REG" > "$TMPC" && mv -f "$TMPC" "$CACHE"
  fi
fi
exit 0
