#!/usr/bin/env bash
# harness-doctor.sh — diagnóstico read-only del harness ai-tooling (ADR-0072 M1).
# distributable: true
#
# Uso:  harness-doctor.sh [repo-root]
# Exit siempre 0 — NUNCA bloquea ni escribe estado del consumer.
#
# Deployment híbrido: auto-sync-daily.sh invoca primero la copia del toolbox
# ($AI_TOOLING_DIR/scripts/harness-doctor.sh — siempre la más nueva) y cae a la
# copia distribuida (scripts/harness-doctor.sh) cuando el toolbox no está
# montado. La copia distribuida se refresca via install-hooks.sh (sync_hooks)
# o a mano.
#
# Chequeos:
#   1. marker .ai-tooling: existe, local_path resuelve a un dir real
#   2. versión: ai_tool_version del marker vs VERSION del toolbox (major diff = warn)
#   3. registry: hooks en .claude/hooks vs registrados en settings.json +
#      settings.local.json -> huérfanos y duplicados cross-file
#   4. drift (informativo): sha256 de distributables del toolbox vs copias locales
#   5. doclens: VERSION del toolbox vs scripts/.doclens-version
set -uo pipefail

REPO_ROOT="${1:-$(pwd)}"
[ -d "$REPO_ROOT" ] || { echo "[doctor] ERROR: no existe $REPO_ROOT"; exit 0; }
REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"
MARKER="$REPO_ROOT/.ai-tooling"

say() { echo "[doctor] $*"; }
WARN=0
warn() { WARN=$((WARN + 1)); say "WARN: $*"; }

# ── 0. jq ─────────────────────────────────────────────────────────────────────
HAS_JQ=false
command -v jq &>/dev/null && HAS_JQ=true
$HAS_JQ || say "jq no disponible — chequeos de marker/registry degradados"

# ── 1. Marker ─────────────────────────────────────────────────────────────────
AI_TOOLING_DIR="${AI_TOOLING_HOME:-}"
if [ ! -f "$MARKER" ]; then
  say "sin marker .ai-tooling — repo no conectado al harness (nada más que chequear)"
  exit 0
fi
if $HAS_JQ && [ -z "$AI_TOOLING_DIR" ]; then
  AI_TOOLING_DIR=$(jq -r '.local_path // empty' "$MARKER" 2>/dev/null)
  AI_TOOLING_DIR="${AI_TOOLING_DIR/#\~/$HOME}"
fi
if [ -n "$AI_TOOLING_DIR" ] && [ ! -d "$AI_TOOLING_DIR" ]; then
  warn "local_path del marker no existe: $AI_TOOLING_DIR (sync caerá a remoto lento o no-op)"
  AI_TOOLING_DIR=""
fi

# ── 2. Versión harness ────────────────────────────────────────────────────────
if $HAS_JQ && [ -n "$AI_TOOLING_DIR" ] && [ -f "$AI_TOOLING_DIR/VERSION" ]; then
  MARKER_V=$(jq -r '.ai_tool_version // "unknown"' "$MARKER")
  TOOLBOX_V=$(cat "$AI_TOOLING_DIR/VERSION")
  [ "${MARKER_V%%.*}" != "${TOOLBOX_V%%.*}" ] && \
    warn "major mismatch: marker ai_tool_version=$MARKER_V vs toolbox VERSION=$TOOLBOX_V"
  say "versiones: marker=$MARKER_V toolbox=$TOOLBOX_V"
fi

# ── 3. Registry: huérfanos y duplicados ───────────────────────────────────────
if [ -d "$REPO_ROOT/.claude/hooks" ]; then
  REG=$(cat "$REPO_ROOT/.claude/settings.json" "$REPO_ROOT/.claude/settings.local.json" 2>/dev/null)
  ORPHANS=""
  for f in "$REPO_ROOT/.claude/hooks"/*.sh; do
    b=$(basename "$f")
    echo "$REG" | grep -q "/$b" || ORPHANS="$ORPHANS $b"
  done
  [ -n "$ORPHANS" ] && warn "hooks huérfanos (en disco, no registrados):$ORPHANS"

  if $HAS_JQ && [ -f "$REPO_ROOT/.claude/settings.json" ] && [ -f "$REPO_ROOT/.claude/settings.local.json" ]; then
    DUPS=$(comm -12 \
      <(grep -oE '[a-z0-9-]+\.sh' "$REPO_ROOT/.claude/settings.json" | sort -u) \
      <(grep -oE '[a-z0-9-]+\.sh' "$REPO_ROOT/.claude/settings.local.json" | sort -u))
    [ -n "$DUPS" ] && warn "hooks duplicados en settings.json Y settings.local.json: $(echo $DUPS | tr '\n' ' ')"
  fi
fi

# ── 4. Drift (informativo — no distingue viejo vs divergencia intencional; eso es M3) ──
if [ -n "$AI_TOOLING_DIR" ] && [ -d "$AI_TOOLING_DIR/.claude/hooks" ]; then
  DRIFT=""
  for f in "$AI_TOOLING_DIR/.claude/hooks"/*.sh; do
    grep -q "distributable: true" "$f" 2>/dev/null || continue
    b=$(basename "$f")
    L="$REPO_ROOT/.claude/hooks/$b"
    if [ -f "$L" ]; then
      [ "$(shasum -a 256 "$f" | cut -d' ' -f1)" != "$(shasum -a 256 "$L" | cut -d' ' -f1)" ] && DRIFT="$DRIFT $b"
    else
      DRIFT="$DRIFT $b(ausente)"
    fi
  done
  [ -n "$DRIFT" ] && say "drift informativo hooks:$DRIFT"
fi

# ── 5. doclens ────────────────────────────────────────────────────────────────
if [ -n "$AI_TOOLING_DIR" ] && [ -f "$AI_TOOLING_DIR/doclens/VERSION" ]; then
  DV_SRC=$(cat "$AI_TOOLING_DIR/doclens/VERSION")
  DV_DST=$(cat "$REPO_ROOT/scripts/.doclens-version" 2>/dev/null || echo "none")
  [ "$DV_SRC" != "$DV_DST" ] && warn "doclens desactualizado: $DV_DST -> $DV_SRC (scripts/sync-doclens.sh)"
fi

# ── Resumen ───────────────────────────────────────────────────────────────────
if [ "$WARN" -eq 0 ]; then
  say "OK — harness sano"
else
  say "$WARN warning(s) — revisar arriba"
fi
exit 0
