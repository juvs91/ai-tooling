#!/usr/bin/env bash
# auto-sync-daily.sh — SessionStart: mantiene skills, hooks y tools sincronizados con ai-tooling.
# distributable: true
# event: SessionStart
# matcher: ""
# timeout: 30
# async: true
#
# Tres sub-sistemas, cada uno con su propio throttle y opt-out via marker
# (.ai-tooling: "sync_skills": false / "sync_hooks": false / "sync_doclens": false):
#   1. Skills — llama a .agents/sync_skills.sh. Ese script YA trae su propio
#      throttle (.agents/.last_sync) — este hook no reimplementa nada, solo
#      lo invoca en cada sesión; el script decide si ya sincronizó hoy.
#   2. Hooks/scripts — llama a install-hooks.sh del repo fuente (ai-tooling).
#      install-hooks.sh NO trae throttle propio (reescribe settings.local.json
#      en cada corrida), así que este hook mantiene uno nuevo aquí:
#      .claude/.last_hook_sync. OJO: regenera settings.local.json — si el
#      proyecto registra sus propios hooks en settings.json, usa
#      "sync_hooks": false para evitar duplicados.
#   3. doclens — llama a doclens/sync.sh del toolbox (copia versionada hacia
#      scripts/doclens.py). Es no-op cuando las versiones coinciden — no
#      necesita throttle propio, igual que sync_skills.sh.
#
# Ubicación de ai-tooling (sin rutas absolutas en el marker):
#   $AI_TOOLING_HOME  →  .local_path del marker (~ y $HOME se expanden)  →  sin sync de hooks
#
# Salvaguardas:
#   - Throttle de hooks solo se escribe si install-hooks.sh terminó OK — un
#     fallo ya no queda oculto 24h.
#   - Skills: si .agents/skills tiene cambios sin commitear, NO se sincroniza
#     (sync_skills.sh sobrescribía ediciones locales — ADR-0043 Addendum 4).
#   - No se sincroniza un repo consigo mismo (ai-tooling con su propio marker).
#   - Lock con mkdir (portable, sin flock en macOS) contra sesiones simultáneas.
#   - Todo se registra en .claude/sync.log (antes: salida descartada).
#
# No borra nada (ni skills obsoletas ni hooks eliminados en ai-tooling) — eso
# sigue siendo un paso manual, igual que sync_skills.sh tampoco poda.
# Ver ADR-0047.

set -uo pipefail

INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$(pwd)"

REPO_ROOT="${CLAUDE_PROJECT_DIR:-}"
[ -n "$REPO_ROOT" ] && [ -d "$REPO_ROOT" ] || REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || echo "$CWD")
MARKER="$REPO_ROOT/.ai-tooling"

# Sin marker no hay de dónde sincronizar — no-op silencioso, no es un error.
[ -f "$MARKER" ] || exit 0

mkdir -p "$REPO_ROOT/.claude"
LOG="$REPO_ROOT/.claude/sync.log"
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"; }

_mtime() {
  if [[ "$(uname)" == "Darwin" ]]; then stat -f%m "$1" 2>/dev/null || echo 0
  else stat -c%Y "$1" 2>/dev/null || echo 0; fi
}

# ── Lock ──────────────────────────────────────────────────────────────────────
LOCK="$REPO_ROOT/.claude/.sync.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  # Lock huérfano (sesión que murió a mitad de sync): >10 min se reclama.
  if [ $(( $(date +%s) - $(_mtime "$LOCK") )) -gt 600 ]; then
    rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 0
    log "lock huérfano reclamado"
  else
    exit 0
  fi
fi
trap 'rm -rf "$LOCK"' EXIT

# ── Resolver ai-tooling ───────────────────────────────────────────────────────
AI_TOOLING_DIR="${AI_TOOLING_HOME:-}"
if [ -z "$AI_TOOLING_DIR" ]; then
  AI_TOOLING_DIR=$(jq -r '.local_path // empty' "$MARKER" 2>/dev/null)
  AI_TOOLING_DIR="${AI_TOOLING_DIR/#\~/$HOME}"
  AI_TOOLING_DIR="${AI_TOOLING_DIR//\$HOME/$HOME}"
fi

if [ -n "$AI_TOOLING_DIR" ] && [ -d "$AI_TOOLING_DIR" ]; then
  SRC_REAL=$(cd "$AI_TOOLING_DIR" && pwd -P)
  DST_REAL=$(cd "$REPO_ROOT" && pwd -P)
  if [ "$SRC_REAL" = "$DST_REAL" ]; then
    log "skip: el repo es la fuente de ai-tooling (no se sincroniza consigo mismo)"
    exit 0
  fi
fi

# ── Opt-outs por marker (default: todo activo) ────────────────────────────────
_flag() { # _flag <nombre> -> "true"/"false" (jq // trata false como vacío: usar has())
  jq -r --arg f "$1" 'if has($f) then .[$f] else true end' "$MARKER" 2>/dev/null || echo "true"
}
SYNC_SKILLS=$(_flag sync_skills)
SYNC_HOOKS=$(_flag sync_hooks)
SYNC_DOCLENS=$(_flag sync_doclens)

# ── 1. Skills ─────────────────────────────────────────────────────────────────
if [ "$SYNC_SKILLS" = "true" ] && [ -f "$REPO_ROOT/.agents/sync_skills.sh" ]; then
  if [ -n "$(git -C "$REPO_ROOT" status --porcelain -- .agents/skills 2>/dev/null)" ]; then
    log "skills: skip — .agents/skills tiene cambios locales sin commitear"
  elif OUT=$(bash "$REPO_ROOT/.agents/sync_skills.sh" 2>&1); then
    [ -n "$OUT" ] && log "skills: OK — $(printf '%s' "$OUT" | tail -n 1)"
  else
    log "skills: ERROR — $(printf '%s' "$OUT" | tail -n 3 | tr '\n' ' ')"
  fi
fi

# ── 2. Hooks/scripts ───────────────────────────────────────────────────────────
if [ "$SYNC_HOOKS" = "true" ] && [ -n "$AI_TOOLING_DIR" ] && [ -f "$AI_TOOLING_DIR/scripts/install-hooks.sh" ]; then
  THROTTLE="$REPO_ROOT/.claude/.last_hook_sync"
  NEEDS_SYNC=true

  if [ -f "$THROTTLE" ]; then
    AGE_HOURS=$(( ($(date +%s) - $(_mtime "$THROTTLE")) / 3600 ))
    [ "$AGE_HOURS" -lt 24 ] && NEEDS_SYNC=false
  fi

  if $NEEDS_SYNC; then
    if OUT=$(bash "$AI_TOOLING_DIR/scripts/install-hooks.sh" "$REPO_ROOT" 2>&1); then
      date -u +"%Y-%m-%dT%H:%M:%SZ" > "$THROTTLE"
      log "hooks: OK desde $AI_TOOLING_DIR"
    else
      log "hooks: ERROR (se reintenta en la próxima sesión) — $(printf '%s' "$OUT" | tail -n 3 | tr '\n' ' ')"
    fi
  fi
elif [ -n "$AI_TOOLING_DIR" ] && [ "$SYNC_HOOKS" = "true" ]; then
  log "hooks: skip — no existe $AI_TOOLING_DIR/scripts/install-hooks.sh"
fi

# ── 3. doclens ─────────────────────────────────────────────────────────────────
# sync.sh ya es no-op cuando las versiones coinciden (compara VERSION del
# toolbox vs scripts/.doclens-version del consumer) — sin throttle externo,
# misma filosofía que sync_skills.sh: el hook invoca, el script decide.
if [ "$SYNC_DOCLENS" = "true" ] && [ -n "$AI_TOOLING_DIR" ] && [ -f "$AI_TOOLING_DIR/doclens/sync.sh" ]; then
  if OUT=$(bash "$AI_TOOLING_DIR/doclens/sync.sh" "$REPO_ROOT" 2>&1); then
    [ -n "$OUT" ] && log "doclens: OK — $(printf '%s' "$OUT" | grep -E 'sincronizado|al día|ya está' | tail -n 1)"
  else
    log "doclens: ERROR — $(printf '%s' "$OUT" | tail -n 3 | tr '\n' ' ')"
  fi
fi

# Mantener el log acotado
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 500 ]; then
  tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

exit 0
