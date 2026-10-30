#!/usr/bin/env bash
# doclens/sync.sh — espejo de doclens/ desde ai-tooling hacia un repo (ADR-0076).
# Misma estructura en el toolbox y en cada consumidor:
#   doclens/{doclens.py, requirements.txt, VERSION, README.md, sync.sh, adapters/, .gitignore}
#       versionados — espejo exacto de lo versionado en ai-tooling/doclens
#   doclens/.venv/   local, no versionado — lo crea/repara este script
# NUNCA editar la copia del consumer: los cambios se hacen en ai-tooling/doclens.
#
# Uso:
#   sync.sh [repo]            espejo + migración de la estructura vieja + venv (default: cwd)
#   sync.sh [repo] --force    re-copiar aunque ya coincida
#   sync.sh [repo] --check    solo reporta (lo usa harness-doctor)
#   sync.sh [repo] --venv     solo asegura el venv (arranque perezoso de doc-guard)
# Si el destino es el propio repo de este script (toolbox, o un consumer corriendo su
# copia espejo) no copia nada: solo asegura el venv.
#
# Lo invoca auto-sync-daily.sh (subsistema 3) en cada sesión: no-op cuando ya está al día.
# Env: DOCLENS_PYTHON — intérprete base para crear el venv (por defecto /usr/bin/python3,
#      luego python3 del PATH). Nunca se usa ni se toca el venv propio del proyecto.
set -euo pipefail

DOCLENS_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TARGET="${1:-$(pwd)}"
# Si el primer arg es una flag, no es un target
case "$TARGET" in -*) TARGET="$(pwd)"; set -- "$TARGET" "$@" ;; esac
FLAG="${2:-}"
case "$FLAG" in --force|--check|--venv|"") ;; *) echo "doclens: flag desconocida: $FLAG" >&2; exit 2 ;; esac

REPO_ROOT="$(cd "$TARGET" 2>/dev/null && { git rev-parse --show-toplevel 2>/dev/null || pwd; })" \
  || { echo "doclens: ERROR — no existe $TARGET" >&2; exit 3; }
REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"
DST="$REPO_ROOT/doclens"
VENV="$DST/.venv"
LOCK="$DST/.venv.lock"
FAILED="$DST/.venv.failed"
# Estructura vieja (antes de ADR-0076): todo suelto en scripts/
LEG_PY="$REPO_ROOT/scripts/doclens.py"
LEG_REQ="$REPO_ROOT/scripts/requirements-doc-tools.txt"
LEG_STAMP="$REPO_ROOT/scripts/.doclens-version"
LEG_VENV="$REPO_ROOT/scripts/.venv-doc-tools"

[ -f "$DOCLENS_HOME/doclens.py" ] || { echo "doclens: ERROR — no existe $DOCLENS_HOME/doclens.py" >&2; exit 3; }

SELF=false
[ "$DOCLENS_HOME" = "$DST" ] && SELF=true

# Lo que forma el espejo: archivos versionados de doclens/ (nunca .venv, caches ni sidecars)
list_src() {
  if git -C "$DOCLENS_HOME" rev-parse --git-dir >/dev/null 2>&1 \
     && [ -n "$(git -C "$DOCLENS_HOME" ls-files -- . 2>/dev/null)" ]; then
    git -C "$DOCLENS_HOME" ls-files -- . | while IFS= read -r f; do [ -f "$DOCLENS_HOME/$f" ] && echo "$f"; done
  else
    (cd "$DOCLENS_HOME" && find . -type f ! -path './.venv*' ! -path '*/__pycache__/*' ! -path '*.inspect/*' | sed 's|^\./||')
  fi | LC_ALL=C sort
}
list_dst() {
  [ -d "$DST" ] || return 0
  (cd "$DST" && find . -type f ! -path './.venv*' ! -path '*/__pycache__/*' ! -path '*.inspect/*' | sed 's|^\./||') | LC_ALL=C sort
}
in_sync() {  # contenido, no VERSION: un adapter cambiado sin subir versión también cuenta
  local src f
  src=$(list_src)
  [ -d "$DST" ] && [ "$src" = "$(list_dst)" ] || return 1
  while IFS= read -r f; do cmp -s "$DOCLENS_HOME/$f" "$DST/$f" || return 1; done <<< "$src"
}
has_legacy() {  # rastros NUESTROS: stamp del sync viejo o el motor con su constante
  [ -f "$LEG_STAMP" ] || { [ -f "$LEG_PY" ] && grep -q 'DOCLENS_VERSION' "$LEG_PY" 2>/dev/null; }
}
version_of() { cat "$1/VERSION" 2>/dev/null || echo none; }

# ── venv ──────────────────────────────────────────────────────────────────────
venv_healthy() {
  [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c 'import fitz, pdfplumber, pypdf, PIL' >/dev/null 2>&1
}
# Python base explícito y aislado: ni el venv/conda activado del proyecto, ni un
# .python-version (uv/pyenv) del consumer deciden con qué se crea el venv de doclens.
clean_env() { env -u VIRTUAL_ENV -u CONDA_PREFIX -u PYTHONPATH -u PYTHONHOME UV_PYTHON_DOWNLOADS=never "$@"; }
pick_python() {
  local c
  for c in "${DOCLENS_PYTHON:-}" /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    clean_env "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1 && { echo "$c"; return 0; }
  done
  return 1
}
take_lock() {  # mismo lock que doc-guard; uno de más de 10 min es huérfano (proceso muerto)
  mkdir -p "$DST"
  mkdir "$LOCK" 2>/dev/null && return 0
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null || true
    mkdir "$LOCK" 2>/dev/null && return 0
  fi
  return 1
}
ensure_venv() {  # 0 listo · 1 falló · 3 otro proceso lo está preparando
  local reqs="$DST/requirements.txt" base rc=0
  # Venv de la estructura vieja: es nuestro, se mueve (sin red); bin/python sigue sirviendo
  if [ ! -e "$VENV" ] && [ -x "$LEG_VENV/bin/python" ]; then
    mkdir -p "$DST" && mv "$LEG_VENV" "$VENV" && echo "doclens: venv movido de scripts/.venv-doc-tools a doclens/.venv"
  fi
  venv_healthy && return 0
  [ -f "$reqs" ] || { echo "doclens: ERROR — falta $reqs" >&2; return 1; }
  if [ "$FLAG" != "--force" ] && [ -n "$(find "$FAILED" -mmin -60 2>/dev/null)" ]; then
    echo "doclens: ERROR — el venv falló hace menos de 1 h; reintento omitido (sync.sh --force para forzar)" >&2
    return 1
  fi
  take_lock || { echo "doclens: otro proceso está preparando el venv"; return 3; }
  trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT
  if ! base=$(pick_python); then
    echo "doclens: ERROR — no hay python3 >= 3.9 (define DOCLENS_PYTHON)" >&2; rc=1
  else
    echo "doclens: creando venv en doclens/.venv (base: $base)…"
    rm -rf "$VENV"
    if command -v uv >/dev/null 2>&1; then
      (cd "$DST" && clean_env uv venv --no-config -q --python "$base" "$VENV" \
        && clean_env uv pip install --no-config -q --python "$VENV/bin/python" -r "$reqs") || rc=1
    else
      (cd "$DST" && clean_env "$base" -m venv "$VENV" \
        && clean_env "$VENV/bin/python" -m pip install -q -r "$reqs") || rc=1
    fi
    [ "$rc" = 0 ] && ! venv_healthy && rc=1
  fi
  if [ "$rc" = 0 ]; then rm -f "$FAILED"; else date > "$FAILED"; echo "doclens: ERROR — no se pudo preparar el venv (sin red o sin python3); reintento en 1 h" >&2; fi
  rmdir "$LOCK" 2>/dev/null || true
  trap - EXIT
  return "$rc"
}
smoke() {
  "$VENV/bin/python" - "$DST" <<'PY'
import sys; sys.path.insert(0, sys.argv[1])
import doclens
print(f"doclens: smoke OK — doclens {doclens.DOCLENS_VERSION}, sidecar format v{doclens.SIDECAR_FORMAT_VERSION}")
PY
  command -v tesseract >/dev/null 2>&1 \
    || echo "doclens: OCR no disponible (PDFs escaneados y texto en figuras) — brew install tesseract tesseract-lang"
}

# ── --check ───────────────────────────────────────────────────────────────────
if [ "$FLAG" = "--check" ]; then
  if $SELF; then echo "toolbox: solo venv"
  elif has_legacy; then echo "estructura vieja: migración pendiente (scripts/doclens.py → doclens/)"
  elif in_sync; then echo "doclens al día ($(version_of "$DST"))"
  else echo "update disponible: $(version_of "$DST") -> $(version_of "$DOCLENS_HOME")"
  fi
  exit 0
fi

# ── espejo + migración ────────────────────────────────────────────────────────
if ! $SELF && [ "$FLAG" != "--venv" ] && { has_legacy || [ "$FLAG" = "--force" ] || ! in_sync; }; then
  BK="$REPO_ROOT/.claude/.harness-backup-$(date -u +%Y%m%dT%H%M%SZ)-doclens"
  FROM_V=$(version_of "$DST"); has_legacy && FROM_V="$(cat "$LEG_STAMP" 2>/dev/null || echo none) (scripts/)"
  # Respaldo de lo que se va a pisar o borrar (contenido local de doclens/, salvo el venv)
  if [ -d "$DST" ] && ! in_sync; then
    list_dst | while IFS= read -r f; do mkdir -p "$BK/doclens/$(dirname "$f")"; cp -p "$DST/$f" "$BK/doclens/$f"; done
  fi
  # Copias nuevas en un staging del mismo filesystem; luego mv archivo por archivo
  # (rename atómico). No se mueve el directorio: .venv y el lock quedan en su sitio.
  STAGE=$(mktemp -d "$REPO_ROOT/.doclens-sync.XXXXXX")
  trap 'rm -rf "$STAGE"' EXIT
  SRC_LIST=$(list_src)
  while IFS= read -r f; do mkdir -p "$STAGE/$(dirname "$f")"; cp -p "$DOCLENS_HOME/$f" "$STAGE/$f"; done <<< "$SRC_LIST"
  mkdir -p "$DST"
  while IFS= read -r f; do mkdir -p "$DST/$(dirname "$f")"; mv -f "$STAGE/$f" "$DST/$f"; done <<< "$SRC_LIST"
  # Lo que ya no existe en el toolbox se borra (quedó en el respaldo)
  list_dst | while IFS= read -r f; do grep -qxF -- "$f" <<< "$SRC_LIST" || rm -f "$DST/$f"; done
  find "$DST" -mindepth 1 -type d -empty ! -path "$VENV*" -delete 2>/dev/null || true
  rm -rf "$STAGE"; trap - EXIT
  # Estructura vieja → respaldo y fuera (el venv se mueve en ensure_venv)
  if has_legacy; then
    mkdir -p "$BK/scripts"
    for f in "$LEG_PY" "$LEG_REQ" "$LEG_STAMP"; do [ -f "$f" ] && cp -p "$f" "$BK/scripts/" && rm -f "$f"; done
    echo "doclens: estructura vieja migrada de scripts/ a doclens/"
  fi
  [ -d "$BK" ] && echo "doclens: respaldo en ${BK#"$REPO_ROOT"/}"
  echo "doclens sincronizado: $FROM_V -> $(version_of "$DST")"
  # Archivos del consumer que siguen apuntando a la estructura vieja (no se editan: solo aviso)
  REFS=$(cd "$REPO_ROOT" && git ls-files 2>/dev/null | grep -vE '^(doclens/|\.claude/\.harness-backup-|\.claude/hooks/doc-guard\.sh$|ai-notes/|docs/)' \
    | while IFS= read -r f; do [ -f "$f" ] && grep -lE 'scripts/doclens\.py|\.venv-doc-tools' "$f" 2>/dev/null; done || true)
  [ -n "$REFS" ] && echo "doclens: AVISO — siguen usando la ruta vieja scripts/doclens.py:" $REFS
elif ! $SELF && [ "$FLAG" != "--venv" ]; then
  echo "doclens al día ($(version_of "$DST"))"
fi

# ── venv + smoke ──────────────────────────────────────────────────────────────
VRC=0; ensure_venv || VRC=$?
[ "$VRC" = 0 ] || exit "$VRC"
smoke
