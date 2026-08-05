#!/usr/bin/env bash
# auto-sync-daily.sh — SessionStart: mantiene skills y hooks sincronizados con ai-tooling.
# distributable: true
# event: SessionStart
# matcher: ""
# timeout: 30
# async: true
#
# Dos sub-sistemas, cada uno con su propio throttle de 24h:
#   1. Skills — llama a .agents/sync_skills.sh. Ese script YA trae su propio
#      throttle (.agents/.last_sync) — este hook no reimplementa nada, solo
#      lo invoca en cada sesión; el script decide si ya sincronizó hoy.
#   2. Hooks/scripts — llama a install-hooks.sh del repo fuente (ai-tooling).
#      install-hooks.sh NO trae throttle propio (reescribe settings.local.json
#      en cada corrida), así que este hook mantiene uno nuevo aquí:
#      .claude/.last_hook_sync.
#
# No borra nada (ni skills obsoletas ni hooks eliminados en ai-tooling) — eso
# sigue siendo un paso manual, igual que sync_skills.sh tampoco poda.
# Ver ADR-0047.

set -uo pipefail

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$(pwd)"

REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || echo "$CWD")
MARKER="$REPO_ROOT/.ai-tooling"

# Sin marker no hay de dónde sincronizar — no-op silencioso, no es un error.
[ -f "$MARKER" ] || exit 0

# ── 1. Skills ─────────────────────────────────────────────────────────────────
if [ -f "$REPO_ROOT/.agents/sync_skills.sh" ]; then
    bash "$REPO_ROOT/.agents/sync_skills.sh" >/dev/null 2>&1 || true
fi

# ── 2. Hooks/scripts ───────────────────────────────────────────────────────────
AI_TOOLING_DIR=$(jq -r '.local_path // empty' "$MARKER" 2>/dev/null)
AI_TOOLING_DIR="${AI_TOOLING_DIR/#\~/$HOME}"

if [ -n "$AI_TOOLING_DIR" ] && [ -f "$AI_TOOLING_DIR/scripts/install-hooks.sh" ]; then
    THROTTLE="$REPO_ROOT/.claude/.last_hook_sync"
    NEEDS_SYNC=true

    if [ -f "$THROTTLE" ]; then
        if [[ "$(uname)" == "Darwin" ]]; then
            MTIME=$(stat -f%m "$THROTTLE" 2>/dev/null || echo 0)
        else
            MTIME=$(stat -c%Y "$THROTTLE" 2>/dev/null || echo 0)
        fi
        NOW=$(date +%s)
        AGE_HOURS=$(( (NOW - MTIME) / 3600 ))
        [ "$AGE_HOURS" -lt 24 ] && NEEDS_SYNC=false
    fi

    if $NEEDS_SYNC; then
        bash "$AI_TOOLING_DIR/scripts/install-hooks.sh" "$REPO_ROOT" >/dev/null 2>&1 || true
        mkdir -p "$(dirname "$THROTTLE")"
        date -u +"%Y-%m-%dT%H:%M:%SZ" > "$THROTTLE"
    fi
fi

exit 0
