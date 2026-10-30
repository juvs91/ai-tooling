#!/usr/bin/env bash
# doclens/sync.sh — espejo de doclens/ desde ai-tooling hacia un repo (ADR-0076).
# Misma estructura en el toolbox y en cada consumidor:
#   doclens/{doclens.py, requirements.txt, VERSION, README.md, sync.sh, adapters/, .gitignore}
#       versionados — espejo exacto de lo COMMITEADO (HEAD) en ai-tooling/doclens
#   doclens/.venv/   local, no versionado — lo crea/repara este script
# NUNCA editar la copia del consumer: los cambios se hacen en ai-tooling/doclens.
#
# Uso:
#   sync.sh [repo]            espejo + migración de la estructura vieja + venv
#   sync.sh [repo] --force    re-copiar aunque ya coincida; reintentar el venv tras un fallo
#   sync.sh [repo] --check    solo reporta (lo usa harness-doctor)
#   sync.sh [repo] --venv     solo asegura el venv (arranque perezoso de doc-guard)
# [repo] explícito se usa tal cual (raíz del proyecto, como CLAUDE_PROJECT_DIR: en un
# monorepo puede ser un subdirectorio). Sin [repo]: la raíz git del cwd.
# Si el destino es el propio repo de este script (toolbox, o un consumer corriendo su
# copia espejo) no copia nada: solo asegura el venv.
#
# Lo invoca auto-sync-daily.sh (subsistema 3) en cada sesión: no-op cuando ya está al día.
# Env (nunca se usa ni se toca el venv propio del proyecto):
#   DOCLENS_PYTHON       intérprete YA preparado con las dependencias: se usa en lugar de
#                        doclens/.venv (mismo significado en doc-guard)
#   DOCLENS_BASE_PYTHON  Python base para crear doclens/.venv (si no: /usr/bin/python3,
#                        luego python3 del PATH; ≥3.9)
set -euo pipefail

DOCLENS_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
EXPLICIT=false
if [ "$#" -gt 0 ] && [ -z "$1" ]; then shift; fi   # sync.sh "" --check ≡ sync.sh --check
case "${1:-}" in ""|-*) TARGET="$(pwd)" ;; *) TARGET="$1"; EXPLICIT=true; shift ;; esac
FLAG="${1:-}"
case "$FLAG" in --force|--check|--venv|"") ;; *) echo "doclens: flag desconocida: $FLAG" >&2; exit 2 ;; esac

[ -d "$TARGET" ] || { echo "doclens: ERROR — no existe $TARGET" >&2; exit 3; }
if $EXPLICIT; then
  REPO_ROOT="$(cd "$TARGET" && pwd -P)"
else
  REPO_ROOT="$(cd "$TARGET" && { git rev-parse --show-toplevel 2>/dev/null || pwd; })"
  REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"
fi
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
# doclens/ del destino como symlink: nunca se escribe ni se borra a través de él
if [ -L "$DST" ]; then
  if [ "$(cd "$DST" 2>/dev/null && pwd -P)" = "$DOCLENS_HOME" ]; then
    SELF=true   # apunta a este mismo doclens: solo el venv
  else
    echo "doclens: ERROR — $DST es un symlink a otra carpeta; no se sincroniza (reemplázalo por una carpeta)" >&2
    exit 3
  fi
elif [ -e "$DST" ] && [ ! -d "$DST" ]; then
  echo "doclens: ERROR — $DST existe y no es una carpeta; no se toca" >&2
  exit 3
fi

# ── fuente del espejo: lo commiteado (HEAD), no el working tree del toolbox ───
# Así un archivo borrado o editado sin commitear en ai-tooling no llega a los consumers.
SRC_MODE=fs
if git -C "$DOCLENS_HOME" rev-parse -q --verify HEAD >/dev/null 2>&1 \
   && [ -n "$(git -C "$DOCLENS_HOME" ls-tree --name-only HEAD -- . 2>/dev/null)" ]; then
  SRC_MODE=git
fi
# Archivos que nunca son parte del espejo (en el toolbox sin git, y en el destino)
files_in() {
  (cd "$1" && find . -type f ! -path './.venv*' ! -path '*/__pycache__/*' ! -path '*.inspect/*' \
     ! -path '*/.ruff_cache/*' ! -path '*/.mypy_cache/*' ! -path '*/.pytest_cache/*' \
     ! -name '.DS_Store' ! -name '*.pyc' ! -name '*.orig' ! -name '*.rej' ! -name '*.swp' ! -name '*~' \
     | sed 's|^\./||') | LC_ALL=C sort
}
list_src() {
  if [ "$SRC_MODE" = git ]; then
    git -C "$DOCLENS_HOME" ls-tree -r -z --name-only HEAD -- . | tr '\0' '\n' | LC_ALL=C sort
  else
    files_in "$DOCLENS_HOME"
  fi
}
src_cat() { if [ "$SRC_MODE" = git ]; then git -C "$DOCLENS_HOME" show "HEAD:./$1"; else cat "$DOCLENS_HOME/$1"; fi; }
src_is_exec() {
  if [ "$SRC_MODE" = git ]; then
    [ "$(git -C "$DOCLENS_HOME" ls-tree -z HEAD -- "$1" | cut -c1-6)" = 100755 ]
  else
    [ -x "$DOCLENS_HOME/$1" ]
  fi
}
list_dst() { if [ -d "$DST" ]; then files_in "$DST"; fi; }
in_sync() {  # contenido, no VERSION: un adapter cambiado sin subir versión también cuenta
  local src f
  src=$(list_src)
  [ -d "$DST" ] && [ "$src" = "$(list_dst)" ] || return 1
  while IFS= read -r f; do src_cat "$f" | cmp -s - "$DST/$f" || return 1; done <<< "$src"
}
has_legacy() {  # rastros NUESTROS: stamp del sync viejo o el motor con su constante
  [ -f "$LEG_STAMP" ] || { [ -f "$LEG_PY" ] && grep -q 'DOCLENS_VERSION' "$LEG_PY" 2>/dev/null; }
}
dst_version() { cat "$DST/VERSION" 2>/dev/null || echo none; }
src_version() { src_cat VERSION 2>/dev/null || echo none; }

# ── venv ──────────────────────────────────────────────────────────────────────
deps_ok() { "$1" -c 'import fitz, pdfplumber, pypdf, PIL' >/dev/null 2>&1; }
venv_healthy() { [ -x "$VENV/bin/python" ] && deps_ok "$VENV/bin/python"; }
# Python base explícito y aislado: ni el venv/conda activado del proyecto, ni un
# .python-version (uv/pyenv) del consumer deciden con qué se crea el venv de doclens.
clean_env() { env -u VIRTUAL_ENV -u CONDA_PREFIX -u PYTHONPATH -u PYTHONHOME UV_PYTHON_DOWNLOADS=never "$@"; }
pick_python() {
  local c
  for c in "${DOCLENS_BASE_PYTHON:-}" /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    clean_env "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1 && { echo "$c"; return 0; }
  done
  return 1
}
# Lock compartido con doc-guard. Guarda el PID: si el dueño murió (p. ej. SIGKILL al
# vencer un timeout) se libera de inmediato; sin PID legible, a los 10 min.
lock_stale() {
  local pid; pid=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$pid" ]; then ! kill -0 "$pid" 2>/dev/null; else [ -n "$(find "$LOCK" -maxdepth 0 -mmin +10 2>/dev/null)" ]; fi
}
take_lock() {
  mkdir -p "$DST"
  if ! mkdir "$LOCK" 2>/dev/null; then
    lock_stale || return 1
    rm -rf "$LOCK"
    mkdir "$LOCK" 2>/dev/null || return 1
  fi
  echo $$ > "$LOCK/pid"
}
release_lock() { [ "$(cat "$LOCK/pid" 2>/dev/null || true)" = "$$" ] && rm -rf "$LOCK"; return 0; }
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
  trap release_lock EXIT
  if ! base=$(pick_python); then
    echo "doclens: ERROR — no hay python3 >= 3.9 (define DOCLENS_BASE_PYTHON)" >&2; rc=1
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
  release_lock
  trap - EXIT
  return "$rc"
}
smoke() {
  "$1" - "$DST" <<'PY'
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
  elif in_sync; then echo "doclens al día ($(dst_version))"
  else echo "update disponible: $(dst_version) -> $(src_version)"
  fi
  exit 0
fi

# ── espejo + migración ────────────────────────────────────────────────────────
if ! $SELF && [ "$FLAG" != "--venv" ] && { has_legacy || [ "$FLAG" = "--force" ] || ! in_sync; }; then
  BK="$REPO_ROOT/.claude/.harness-backup-$(date -u +%Y%m%dT%H%M%SZ)-doclens"
  FROM_V=$(dst_version); if has_legacy; then FROM_V="$(cat "$LEG_STAMP" 2>/dev/null || echo none) (scripts/)"; fi
  # Respaldo de lo que se va a pisar o borrar (contenido local de doclens/, salvo el venv)
  if [ -d "$DST" ] && ! in_sync; then
    list_dst | while IFS= read -r f; do mkdir -p "$BK/doclens/$(dirname "$f")"; cp -p "$DST/$f" "$BK/doclens/$f"; done
  fi
  # Copias nuevas en un staging del mismo filesystem; luego mv archivo por archivo
  # (rename atómico). No se mueve el directorio: .venv y el lock quedan en su sitio.
  STAGE=$(mktemp -d "$REPO_ROOT/.doclens-sync.XXXXXX")
  trap 'rm -rf "$STAGE"' EXIT
  SRC_LIST=$(list_src)
  while IFS= read -r f; do
    mkdir -p "$STAGE/$(dirname "$f")"
    if ! src_cat "$f" > "$STAGE/$f" 2>/dev/null; then
      rm -f "$STAGE/$f"; echo "doclens: AVISO — no se pudo copiar '$f' (nombre no soportado); omitido"; continue
    fi
    if src_is_exec "$f"; then chmod +x "$STAGE/$f"; fi
  done <<< "$SRC_LIST"
  mkdir -p "$DST"
  while IFS= read -r f; do
    if [ -f "$STAGE/$f" ]; then mkdir -p "$DST/$(dirname "$f")"; mv -f "$STAGE/$f" "$DST/$f"; fi
  done <<< "$SRC_LIST"
  # Lo que ya no existe en el toolbox se borra (quedó en el respaldo)
  list_dst | while IFS= read -r f; do grep -qxF -- "$f" <<< "$SRC_LIST" || rm -f "$DST/$f"; done
  # Rutas relativas: un '[' o '*' en la ruta del repo no puede volverse patrón
  (cd "$DST" && find . -mindepth 1 -type d -empty ! -path './.venv*' -delete 2>/dev/null) || true
  rm -rf "$STAGE"; trap - EXIT
  # Estructura vieja → respaldo y fuera (el venv se mueve en ensure_venv)
  if has_legacy; then
    mkdir -p "$BK/scripts"
    for f in "$LEG_PY" "$LEG_REQ" "$LEG_STAMP"; do if [ -f "$f" ]; then cp -p "$f" "$BK/scripts/" && rm -f "$f"; fi; done
    echo "doclens: estructura vieja migrada de scripts/ a doclens/"
  fi
  if [ -d "$BK" ]; then echo "doclens: respaldo en ${BK#"$REPO_ROOT"/}"; fi
  echo "doclens sincronizado: $FROM_V -> $(dst_version)"
  # Archivos del consumer que siguen apuntando a la estructura vieja (no se editan: solo aviso)
  REFS=$(cd "$REPO_ROOT" && git ls-files 2>/dev/null | grep -vE '^(doclens/|\.claude/\.harness-backup-|\.claude/hooks/doc-guard\.sh$|ai-notes/|docs/)' \
    | while IFS= read -r f; do if [ -f "$f" ]; then grep -lE 'scripts/doclens\.py|\.venv-doc-tools' "$f" 2>/dev/null || true; fi; done || true)
  if [ -n "$REFS" ]; then echo "doclens: AVISO — siguen usando la ruta vieja scripts/doclens.py:" $REFS; fi
elif ! $SELF && [ "$FLAG" != "--venv" ]; then
  echo "doclens al día ($(dst_version))"
fi

# ── intérprete: DOCLENS_PYTHON ya preparado, o doclens/.venv ──────────────────
if [ -n "${DOCLENS_PYTHON:-}" ]; then
  DOCLENS_PYTHON=$(command -v "$DOCLENS_PYTHON" 2>/dev/null || echo "$DOCLENS_PYTHON")   # admite "python3"
  if [ -x "$DOCLENS_PYTHON" ] && deps_ok "$DOCLENS_PYTHON"; then
    echo "doclens: usa DOCLENS_PYTHON=$DOCLENS_PYTHON (no crea doclens/.venv)"
    smoke "$DOCLENS_PYTHON"; exit 0
  fi
  # Compatibilidad: antes DOCLENS_PYTHON era el Python base; no fallar, usar el venv
  echo "doclens: AVISO — DOCLENS_PYTHON=$DOCLENS_PYTHON no tiene las dependencias; se usa doclens/.venv (para el Python base usa DOCLENS_BASE_PYTHON)"
  : "${DOCLENS_BASE_PYTHON:=$DOCLENS_PYTHON}"
fi
VRC=0; ensure_venv || VRC=$?
[ "$VRC" = 0 ] || exit "$VRC"
smoke "$VENV/bin/python"
