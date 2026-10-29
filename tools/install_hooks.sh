#!/usr/bin/env bash
# install_hooks.sh — Instala el git hook ADR gate (commit-msg)
#
# Uso (una vez después de clonar o como paso de onboarding):
#   bash tools/install_hooks.sh
#
# Hooks instalados:
#   commit-msg — ADR gate: bloquea commits que modifiquen rutas guardadas
#                sin un nuevo ADR-*.md staged junto al cambio.
#
# ADR-0073:
#   - Directorio de hooks por `git rev-parse --git-path hooks`: correcto en
#     worktrees (.git es un archivo ahí) y respeta core.hooksPath.
#   - commit-msg en vez de pre-commit: pre-commit corre ANTES de que git escriba
#     COMMIT_EDITMSG, así que leía el mensaje del commit ANTERIOR (un [skip-adr]
#     viejo bypaseaba el siguiente y el actual se ignoraba). commit-msg recibe
#     el mensaje real en $1. Las instalaciones previas en pre-commit se migran.
#   - Un hook ajeno se preserva: solo se reescribe el archivo si es nuestro
#     (línea de banner completa); si no, se agrega/actualiza un bloque BEGIN/END.

set -euo pipefail

git rev-parse --git-dir >/dev/null 2>&1 || {
  echo "[ai-tooling:hooks] ERROR: no es un repo git." >&2
  exit 1
}
REPO_ROOT="$(git rev-parse --show-toplevel)"

HOOKS_PATH_CFG="$(git config --get core.hooksPath || true)"
if [[ -n "$HOOKS_PATH_CFG" ]]; then
  case "$HOOKS_PATH_CFG" in
    /*|~*) ABS_CFG="$HOOKS_PATH_CFG" ;;
    *)     ABS_CFG="$REPO_ROOT/$HOOKS_PATH_CFG" ;;
  esac
  if [[ "$HOOKS_PATH_CFG" == *husky* ]] || [[ "$ABS_CFG" != "$REPO_ROOT"/* ]]; then
    echo "[ai-tooling:hooks] WARN: core.hooksPath=$HOOKS_PATH_CFG es global o gestionado por otra herramienta." >&2
    echo "[ai-tooling:hooks] No se instala (afectaría otros repos). Agrega el ADR gate a ese gestor manualmente." >&2
    exit 0
  fi
fi

HOOKS_DIR="$(git rev-parse --path-format=absolute --git-path hooks)"
mkdir -p "$HOOKS_DIR"

COMMIT_MSG_HOOK="$HOOKS_DIR/commit-msg"
PRE_COMMIT="$HOOKS_DIR/pre-commit"
BANNER="# ai-tooling:adr-gate — ADR gate commit-msg hook (NO BORRAR ESTA LÍNEA)"
OLD_BANNER="# ai-tooling:adr-gate — ADR gate pre-commit hook (NO BORRAR ESTA LÍNEA)"
BLOCK_BEGIN="# --- BEGIN ai-tooling:adr-gate (appended by install_hooks.sh) ---"
BLOCK_END="# --- END ai-tooling:adr-gate ---"

# Cuerpo común: corre desde el toplevel del worktree actual; si la rama no
# tiene el checker (rama vieja), no bloquea.
GATE_BODY='(
  cd "$(git rev-parse --show-toplevel)" || exit 0
  [ -f tools/check_adr_gate.py ] || exit 0
  STAGED=$(git diff --cached --name-only 2>/dev/null || true)
  NEW_FILES=$(git diff --cached --name-only --diff-filter=A 2>/dev/null || true)
  COMMIT_MSG=$(grep -v "^#" "$1" 2>/dev/null || true)
  PYTHON_BIN="python3"; command -v python3 >/dev/null 2>&1 || PYTHON_BIN="python"
  "$PYTHON_BIN" tools/check_adr_gate.py \
    --changed-files "$STAGED" \
    --new-files     "$NEW_FILES" \
    --commit-message "$COMMIT_MSG"
) || exit $?'

HOOK_FILE_CONTENT="#!/usr/bin/env bash
$BANNER
# Bloquea commits que modifiquen rutas guardadas sin un nuevo ADR staged.
# Instalado por tools/install_hooks.sh — re-ejecuta para actualizar.
set -uo pipefail
$GATE_BODY"

# Quita un bloque BEGIN/END previo de un archivo (in place)
_strip_block() {
  local f="$1" tmp
  tmp="$(mktemp "$f.XXXXXX")"
  awk -v b="$BLOCK_BEGIN" -v e="$BLOCK_END" '$0==b{skip=1;next} $0==e{skip=0;next} !skip' "$f" > "$tmp"
  cat "$tmp" > "$f"; rm -f "$tmp"
}

# ── Migración: instalaciones previas en pre-commit ────────────────────────────
if [[ -f "$PRE_COMMIT" ]]; then
  if grep -qxF "$OLD_BANNER" "$PRE_COMMIT"; then
    rm -f "$PRE_COMMIT"
    echo "[ai-tooling:hooks] Migrado: eliminado el ADR gate de pre-commit (ahora en commit-msg)"
  elif grep -qxF "$BLOCK_BEGIN" "$PRE_COMMIT"; then
    _strip_block "$PRE_COMMIT"
    echo "[ai-tooling:hooks] Migrado: quitado el bloque ADR gate de $PRE_COMMIT (hook ajeno preservado)"
  fi
fi

# ── Instalar commit-msg ──────────────────────────────────────────────────────
if [[ -f "$COMMIT_MSG_HOOK" ]] && ! grep -qxF "$BANNER" "$COMMIT_MSG_HOOK"; then
  # Hook de otro sistema — preservarlo y (re)poner nuestro bloque al final
  grep -qxF "$BLOCK_BEGIN" "$COMMIT_MSG_HOOK" && _strip_block "$COMMIT_MSG_HOOK"
  {
    echo "$BLOCK_BEGIN"
    echo "$GATE_BODY"
    echo "$BLOCK_END"
  } >> "$COMMIT_MSG_HOOK"
  echo "[ai-tooling:hooks] Agregado al hook existente: $COMMIT_MSG_HOOK"
  echo "[ai-tooling:hooks] WARN: verifica que el comportamiento combinado sea correcto."
else
  printf '%s\n' "$HOOK_FILE_CONTENT" > "$COMMIT_MSG_HOOK"
  chmod +x "$COMMIT_MSG_HOOK"
  echo "[ai-tooling:hooks] Instalado: $COMMIT_MSG_HOOK"
fi

echo "[ai-tooling:hooks] ADR gate activo en cada commit (commit-msg)."
