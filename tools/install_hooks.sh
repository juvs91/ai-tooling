#!/usr/bin/env bash
# install_hooks.sh — Instala el git pre-commit hook ADR gate en .git/hooks/
#
# Uso (una vez después de clonar o como paso de onboarding):
#   bash tools/install_hooks.sh
#
# Hooks instalados:
#   pre-commit — ADR gate: bloquea commits que modifiquen rutas guardadas
#                sin un nuevo ADR-*.md staged junto al cambio.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo ".")"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

if [[ ! -d "$HOOKS_DIR" ]]; then
  echo "[ai-tooling:hooks] ERROR: .git/hooks no encontrado. ¿Es un repo git?" >&2
  exit 1
fi

PRE_COMMIT="$HOOKS_DIR/pre-commit"
BANNER="# ai-tooling:adr-gate"

HOOK_BODY='#!/usr/bin/env bash
# ai-tooling:adr-gate — ADR gate pre-commit hook (NO BORRAR ESTA LÍNEA)
# Bloquea commits que modifiquen rutas guardadas sin un nuevo ADR staged.
# Instalado por tools/install_hooks.sh — re-ejecuta para actualizar.
set -euo pipefail

STAGED=$(git diff --cached --name-only 2>/dev/null || true)
NEW_FILES=$(git diff --cached --name-only --diff-filter=A 2>/dev/null || true)
COMMIT_MSG=""
if [[ -f ".git/COMMIT_EDITMSG" ]]; then
  COMMIT_MSG=$(cat ".git/COMMIT_EDITMSG")
fi

python tools/check_adr_gate.py \
  --changed-files "$STAGED" \
  --new-files     "$NEW_FILES" \
  --commit-message "$COMMIT_MSG"'

if [[ -f "$PRE_COMMIT" ]]; then
  if grep -q "ai-tooling:adr-gate" "$PRE_COMMIT" 2>/dev/null; then
    # Hook de ai-tooling ya instalado — actualizar in-place
    echo "$HOOK_BODY" > "$PRE_COMMIT"
    chmod +x "$PRE_COMMIT"
    echo "[ai-tooling:hooks] Actualizado: $PRE_COMMIT"
  else
    # Existe un hook de otro sistema — hacer append para no sobreescribir
    {
      echo ""
      echo "# --- BEGIN ai-tooling:adr-gate (appended by install_hooks.sh) ---"
      echo 'STAGED=$(git diff --cached --name-only 2>/dev/null || true)'
      echo 'NEW_FILES=$(git diff --cached --name-only --diff-filter=A 2>/dev/null || true)'
      echo 'COMMIT_MSG=$(cat .git/COMMIT_EDITMSG 2>/dev/null || true)'
      echo 'python tools/check_adr_gate.py --changed-files "$STAGED" --new-files "$NEW_FILES" --commit-message "$COMMIT_MSG"'
      echo "# --- END ai-tooling:adr-gate ---"
    } >> "$PRE_COMMIT"
    echo "[ai-tooling:hooks] Agregado al hook existente: $PRE_COMMIT"
    echo "[ai-tooling:hooks] WARN: verifica que el comportamiento combinado sea correcto."
  fi
else
  echo "$HOOK_BODY" > "$PRE_COMMIT"
  chmod +x "$PRE_COMMIT"
  echo "[ai-tooling:hooks] Instalado: $PRE_COMMIT"
fi

echo "[ai-tooling:hooks] ADR gate activo en cada commit."
