#!/usr/bin/env bash
# doclens/sync.sh — sincroniza doclens desde ai-tooling hacia un repo consumidor
# (copia versionada). NUNCA editar la copia local del consumer: los cambios se
# hacen en ai-tooling/doclens/doclens.py y se bajan con este script.
#
# Uso:
#   sync.sh [target-repo]            # sync si hay versión nueva (default: cwd)
#   sync.sh [target-repo] --force    # re-copiar aunque las versiones coincidan
#   sync.sh [target-repo] --check    # solo reporta si hay update disponible
#
# Lo invoca auto-sync-daily.sh (subsystem 3) en cada sesión; este script ya es
# no-op cuando las versiones coinciden — no necesita throttle externo.
set -euo pipefail

DOCLENS_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-$(pwd)}"
# Si el primer arg es una flag, no es un target
case "$TARGET" in -*) TARGET="$(pwd)"; set -- "$TARGET" "$@" ;; esac
FLAG="${2:-${1:-}}"
case "$FLAG" in --*) ;; *) FLAG="" ;; esac

REPO_ROOT="$(cd "$TARGET" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || pwd)"
DEST="$REPO_ROOT/scripts/doclens.py"
STAMP="$REPO_ROOT/scripts/.doclens-version"

[ -f "$DOCLENS_HOME/doclens.py" ] || {
  echo "ERROR: no existe $DOCLENS_HOME/doclens.py" >&2
  exit 3
}

# No sincronizar el toolbox consigo mismo
SRC_REAL=$(cd "$DOCLENS_HOME" && pwd -P)
DST_REAL=$(cd "$REPO_ROOT" && pwd -P)
if [ "$SRC_REAL" = "$DST_REAL" ] || [ "$DST_REAL" = "$(dirname "$SRC_REAL")" ]; then
  echo "doclens: skip — el target es el propio toolbox"
  exit 0
fi

SRC_VERSION=$(cat "$DOCLENS_HOME/VERSION")
DST_VERSION=$(cat "$STAMP" 2>/dev/null || echo "none")

if [ "$FLAG" = "--check" ]; then
  if [ "$SRC_VERSION" = "$DST_VERSION" ]; then
    echo "doclens al día ($DST_VERSION)"
  else
    echo "update disponible: $DST_VERSION -> $SRC_VERSION"
  fi
  exit 0
fi

if [ "$SRC_VERSION" = "$DST_VERSION" ] && [ "$FLAG" != "--force" ]; then
  echo "doclens ya está en $DST_VERSION — nada que hacer"
  exit 0
fi

mkdir -p "$REPO_ROOT/scripts"
cp "$DOCLENS_HOME/doclens.py" "$DEST"
cp "$DOCLENS_HOME/requirements.txt" "$REPO_ROOT/scripts/requirements-doc-tools.txt"
echo "$SRC_VERSION" > "$STAMP"
echo "doclens sincronizado: $DST_VERSION -> $SRC_VERSION"
echo "  -> $DEST"

# Asegurar venv (no falla si ya existe y está sano)
VENV="$REPO_ROOT/scripts/.venv-doc-tools"
if [ ! -x "$VENV/bin/python" ]; then
  echo "creando venv $VENV ..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q -r "$REPO_ROOT/scripts/requirements-doc-tools.txt"
  echo "venv listo"
fi

# Smoke test: el motor importa y reporta versión
"$VENV/bin/python" -c "
import sys; sys.path.insert(0, '$REPO_ROOT/scripts')
import doclens
print(f'smoke OK — doclens {doclens.DOCLENS_VERSION}, sidecar format v{doclens.SIDECAR_FORMAT_VERSION}')
"
