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
#   6. worktrees (ADR-0073): .worktreeinclude, huérfanos, conteo, ramas mergeadas
#   7. ADR gate git (ADR-0074): install_hooks.sh --check (falta / migración pendiente)
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
    echo "$REG" | grep -q "/$b" && continue
    # Hooks solo para consumers (ej. auto-sync-daily): no registrarlos en el toolbox es lo correcto
    grep -q "^# consumer-only: true" "$f" 2>/dev/null && continue
    # No es huérfano si otro hook lo referencia (librería sourceada vía variable,
    # ej. LIB="$CWD/.claude/hooks/task-scope-lib.sh" + source "$LIB")
    _ref=false
    for g in "$REPO_ROOT/.claude/hooks"/*.sh; do
      [ "$g" = "$f" ] && continue
      grep -qE "(^|[^a-zA-Z0-9._-])$b" "$g" 2>/dev/null && { _ref=true; break; }
    done
    $_ref && continue
    ORPHANS="$ORPHANS $b"
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
      cmp -s "$f" "$L" || DRIFT="$DRIFT $b"   # cmp es POSIX; shasum no existe en todo Linux
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

# ── 6. worktrees (ADR-0073) ───────────────────────────────────────────────────
# Solo lectura y sin fetch: compara contra refs locales (pueden estar viejas).
if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  # Si el usuario lo borró a propósito (manifest lo registra como provisto), no insistir (ADR-0074)
  WTI_DECLINED=false
  $HAS_JQ && [ -f "$REPO_ROOT/.claude/.harness-manifest.json" ] \
    && [ -n "$(jq -r '.files[".worktreeinclude"] // empty' "$REPO_ROOT/.claude/.harness-manifest.json" 2>/dev/null)" ] \
    && WTI_DECLINED=true
  [ -f "$REPO_ROOT/.worktreeinclude" ] || $WTI_DECLINED \
    || warn "sin .worktreeinclude — los worktrees nativos nacen sin .env/settings.local.json (install-hooks.sh --apply lo crea)"

  PRUNABLE=$(git -C "$REPO_ROOT" worktree prune --dry-run 2>&1)
  [ -n "$PRUNABLE" ] && warn "worktrees huérfanos (dir borrado a mano): git worktree prune"

  WT_COUNT=$(git -C "$REPO_ROOT" worktree list --porcelain | grep -c '^worktree ')
  WT_COUNT=$((WT_COUNT - 1))   # sin el checkout principal
  [ "$WT_COUNT" -gt 4 ] && warn "$WT_COUNT worktrees activos (>4 suele indicar trabajo estancado)"

  # Mergeado = ≥1 commit propio (desde la creación de la rama, por reflog)
  # contenido en el trunk local. Un worktree nativo recién creado no cuenta.
  TRUNK=$(git -C "$REPO_ROOT" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')
  TRUNK=${TRUNK:-$(git -C "$REPO_ROOT" rev-parse --verify -q master >/dev/null && echo master || echo main)}
  MERGED=""
  while IFS= read -r br; do
    [ -z "$br" ] && continue
    created=$(git -C "$REPO_ROOT" reflog show --format=%H "refs/heads/$br" 2>/dev/null | tail -1)
    [ -n "$created" ] || continue
    own=$(git -C "$REPO_ROOT" rev-list --count "$created..$br" 2>/dev/null || echo 0)
    [ "$own" -ge 1 ] || continue
    git -C "$REPO_ROOT" merge-base --is-ancestor "$br" "$TRUNK" 2>/dev/null && MERGED="$MERGED $br"
  done < <(git -C "$REPO_ROOT" worktree list --porcelain | awk 'NR==1,/^$/{next} /^branch refs\/heads\//{sub(/^branch refs\/heads\//,""); print}')
  [ -n "$MERGED" ] && say "worktrees con rama ya mergeada a $TRUNK (candidatos a limpiar):$MERGED"
fi

# ── 7. ADR gate git (ADR-0074) ────────────────────────────────────────────────
# Mismo --check que auto-sync. Solo un instalador que declare `# supports: --check`
# (uno viejo ignoraría el flag e INSTALARÍA — el doctor nunca escribe).
GH_INSTALLER=""
for cand in "${AI_TOOLING_DIR:+$AI_TOOLING_DIR/tools/install_hooks.sh}" "$REPO_ROOT/tools/install_hooks.sh"; do
  [ -n "$cand" ] && [ -f "$cand" ] && grep -qxF '# supports: --check' "$cand" && { GH_INSTALLER="$cand"; break; }
done
if [ -n "$GH_INSTALLER" ]; then
  GH_OUT=$(cd "$REPO_ROOT" && bash "$GH_INSTALLER" --check 2>&1); GH_RC=$?
  case "$GH_RC" in
    11|12) warn "ADR gate git: ${GH_OUT#* }" ;;
    13)    say "ADR gate git: no aplica (${GH_OUT#* })" ;;
    10)    ;;
    *)     warn "ADR gate git: check con error (rc=$GH_RC)" ;;
  esac
fi

# ── Resumen ───────────────────────────────────────────────────────────────────
if [ "$WARN" -eq 0 ]; then
  say "OK — harness sano"
else
  say "$WARN warning(s) — revisar arriba"
fi
exit 0
