#!/usr/bin/env bash
# doclens/doctor.sh — diagnóstico de doclens + doc-guard en ESTA máquina (sobre todo Windows
# nativo con Git Bash, donde nunca se ha validado).
#
# Uso:  bash doclens/doctor.sh [proyecto]   (por omisión: la raíz git del cwd)
#       Se puede correr desde un consumer o desde el clon de ai-tooling.
#       Para compartir el resultado:  bash doclens/doctor.sh > doclens-doctor.txt 2>&1
#
# NO modifica nada: no crea el venv, no toca el proyecto ni ~/.cache/doclens. Las pruebas
# del hook corren sobre COPIAS en un directorio temporal (que se borra al final), con caché,
# TMPDIR y estado propios. La salida incluye rutas (con tu usuario) y versiones; no imprime
# variables de entorno ni secretos.
#
# Qué responde:
#   1. Sistema y herramientas: bash, git, jq, python real o alias de la Microsoft Store, tesseract
#   2. Proyecto: hook instalado y registrado, finales de línea CRLF, venv (dónde y qué importa)
#   3. Hook en vivo, con un PDF mínimo:
#        A) sin doclens disponible: ¿bloquea o deja pasar el PDF crudo?
#        B) con el intérprete del venv: ¿produce el digest de punta a punta?
#      Cada una con la ruta tal como la manda Claude Code (en Windows, C:\...) y en forma POSIX.
#   4. Resumen con un veredicto por punto.
# Exit siempre 0.
set -uo pipefail

HOME_DL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [ -n "${1:-}" ]; then
  PROJ="$1"
else
  PROJ=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
fi
[ -d "$PROJ" ] || { echo "no existe el proyecto: $PROJ"; exit 0; }
PROJ="$(cd "$PROJ" && pwd -P)"

SUMMARY=""
note() { SUMMARY="$SUMMARY$1"$'\n'; }
hdr() { echo; echo "== $* =="; }
kv() { printf '  %-22s %s\n' "$1" "$2"; }

# Corre "$@" con un tope de segundos (no hay timeout(1) en macOS ni siempre en Git Bash).
# fd 3: en un script, un comando en segundo plano recibe /dev/null como stdin; sin esto el
# hook leería una petición vacía (y saldría con 0, como si dejara pasar el PDF)
tmo() {
  local s="$1" p w rc; shift
  exec 3<&0
  "$@" <&3 3<&- & p=$!
  exec 3<&-
  ( sleep "$s"; kill "$p" 2>/dev/null ) 2>/dev/null & w=$!
  wait "$p"; rc=$?
  kill "$w" 2>/dev/null; wait "$w" 2>/dev/null
  return "$rc"
}
first() { head -1 | tr -d '\r' | cut -c1-160; }
has_cr() { [ -f "$1" ] && LC_ALL=C grep -q $'\r' "$1" 2>/dev/null; }

UNAME=$(uname -s 2>/dev/null || echo desconocido)
case "$UNAME" in MINGW*|MSYS*|CYGWIN*) WIN=true ;; *) WIN=false ;; esac
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

echo "doclens doctor — $(date '+%Y-%m-%d %H:%M') — proyecto: $PROJ"
echo "doclens del doctor: $HOME_DL (versión $(cat "$HOME_DL/VERSION" 2>/dev/null || echo '?'))"

# ── 1. Sistema y herramientas ──────────────────────────────────────────────────
hdr "1. Sistema y herramientas"
kv "sistema" "$UNAME $(uname -r 2>/dev/null) $([ "$WIN" = true ] && echo '(Windows nativo: Git Bash/MSYS)')"
kv "bash" "${BASH_VERSION:-?}"
kv "git" "$(git --version 2>/dev/null || echo 'NO')"
if command -v jq >/dev/null 2>&1; then
  kv "jq" "$(jq --version 2>/dev/null) ($(command -v jq))"; HAS_JQ=true
else
  kv "jq" "NO"; HAS_JQ=false
fi

# python: el hook usa "python3" (canonicalizar rutas, leer el JSON sin jq). En Windows,
# python3/python suelen ser el alias de la Microsoft Store en WindowsApps, que no ejecuta nada.
PY3_OK=false; GOOD_PY=""
for c in python3 python py; do
  p=$(command -v "$c" 2>/dev/null) || { kv "$c" "NO"; continue; }
  args=(-c 'import sys; print(sys.version.split()[0])'); [ "$c" = py ] && args=(-3 "${args[@]}")
  v=$(tmo 15 "$c" "${args[@]}" 2>&1 | first); rc=$?
  case "$p" in *WindowsApps*) store=" [alias de la Microsoft Store]" ;; *) store="" ;; esac
  if [ "$rc" = 0 ] && printf '%s' "$v" | grep -qE '^[0-9]+\.[0-9]+'; then
    kv "$c" "$v ($p)$store"
    [ "$c" = python3 ] && PY3_OK=true
    [ -z "$GOOD_PY" ] && GOOD_PY="$c"
  else
    kv "$c" "NO FUNCIONA ($p)$store: $v"
  fi
done
if $HAS_JQ || $PY3_OK; then
  note "OK     el hook puede leer la petición (jq o python3)"
else
  note "FALLA  sin jq ni python3 funcional: el hook bloquea TODOS los PDFs ('faltan jq y python3'). Instala jq o Python 3 (y desactiva el alias de la Store)"
fi
$PY3_OK || note "AVISO  sin python3 funcional: el hook usa su camino de respaldo (sin ruta canónica). Python instalado como 'python' o 'py' no cuenta: el hook llama 'python3'"

if command -v tesseract >/dev/null 2>&1; then
  kv "tesseract" "$(tmo 15 tesseract --version 2>&1 | first)"
else
  t=""
  for d in "/c/Program Files/Tesseract-OCR" "/c/Program Files (x86)/Tesseract-OCR"; do [ -x "$d/tesseract.exe" ] && t="$d"; done
  kv "tesseract" "NO en PATH${t:+ (instalado en $t, falta agregarlo al PATH)}"
  note "AVISO  sin tesseract en PATH: doclens funciona, pero sin OCR (escaneos y texto dentro de figuras)"
fi
kv "uv" "$(command -v uv >/dev/null 2>&1 && uv --version 2>/dev/null || echo NO)"
kv "setsid / perl" "$(command -v setsid >/dev/null 2>&1 && echo sí || echo no) / $(command -v perl >/dev/null 2>&1 && echo sí || echo no)"
kv "ps" "$(ps -o args= -p $$ >/dev/null 2>&1 && echo 'POSIX (-o args)' || echo 'sin -o args (MSYS)')"

# ── 2. Proyecto ────────────────────────────────────────────────────────────────
hdr "2. Proyecto"
HOOK="$PROJ/.claude/hooks/doc-guard.sh"
if [ -f "$HOOK" ]; then
  kv "doc-guard.sh" "sí"
else
  kv "doc-guard.sh" "NO (.claude/hooks/doc-guard.sh)"
  note "FALLA  el hook no está en el proyecto: los PDFs llegan crudos al modelo (con Kimi, la sesión muere con 400)"
fi
REG=""
for s in "$PROJ/.claude/settings.json" "$PROJ/.claude/settings.local.json"; do
  [ -f "$s" ] && grep -q 'doc-guard' "$s" 2>/dev/null && REG="$REG ${s##*/}"
done
if [ -n "$REG" ]; then kv "registrado en" "$REG"; else
  kv "registrado en" "NINGUNO"
  [ -f "$HOOK" ] && note "FALLA  doc-guard.sh existe pero no está registrado en .claude/settings*.json: nunca corre"
fi
kv "marker .ai-tooling" "$([ -f "$PROJ/.ai-tooling" ] && echo sí || echo no)"
DLP="$PROJ/doclens"
kv "doclens/" "$([ -f "$DLP/doclens.py" ] && echo "sí (versión $(cat "$DLP/VERSION" 2>/dev/null || echo '?'))" || echo NO)"

# CRLF: con core.autocrlf=true (default de Git for Windows), un .sh con \r rompe bash
CR=""
for f in "$HOOK" "$DLP/sync.sh" "$DLP/doclens.py" "$DLP/doctor.sh" "$PROJ/.claude/hooks/"*.sh; do
  has_cr "$f" && CR="$CR ${f#"$PROJ"/}"
done
if [ -n "$CR" ]; then
  kv "finales CRLF" "SÍ:$CR"
  note "FALLA  archivos con CRLF (bash falla con \$'\\r'):$CR — git config core.autocrlf input y vuelve a hacer checkout"
else
  kv "finales CRLF" "no"
fi
kv "core.autocrlf" "$(git -C "$PROJ" config core.autocrlf 2>/dev/null || echo '(sin definir)')"

# venv: el hook busca doclens/.venv/bin/python; en Windows un venv queda en Scripts/python.exe
VB="$DLP/.venv/bin/python"; VS="$DLP/.venv/Scripts/python.exe"; VENV=""
if [ -x "$VB" ]; then VENV="$VB"; elif [ -f "$VS" ]; then VENV="$VS"; fi
if [ -z "$VENV" ]; then
  kv "venv" "NO existe (doclens/.venv)"
  note "AVISO  sin venv de doclens: el hook intentará crearlo en el primer PDF (doclens/sync.sh --venv)"
else
  kv "venv" "$VENV"
  [ "$VENV" = "$VS" ] && note "FALLA  el venv tiene la forma de Windows (Scripts/python.exe) y el hook busca .venv/bin/python: no lo encuentra"
  for m in fitz PIL cv2; do
    r=$(tmo 30 "$VENV" -c "import $m; print(getattr($m, '__version__', getattr($m, 'VersionBind', 'ok')))" 2>&1 | first)
    kv "  import $m" "$r"
  done
fi
# Lo que doclens.py asume de POSIX y Python para Windows no tiene: os.getuid (dueño del
# caché), permisos 0700 reales (validación del caché) y SIGALRM/SIGXCPU (cortes de tiempo)
PYCHK="${DOCLENS_PYTHON:-$VENV}"; [ -n "$PYCHK" ] || PYCHK="$GOOD_PY"
if [ -n "$PYCHK" ]; then
  r=$(tmo 30 "$PYCHK" -c '
import os, signal, sys, tempfile
d = tempfile.mkdtemp(); os.chmod(d, 0o700); m = os.stat(d).st_mode & 0o777; os.rmdir(d)
falta = [n for n, ok in (("os.getuid", hasattr(os, "getuid")), ("SIGALRM", hasattr(signal, "SIGALRM")),
                         ("SIGXCPU", hasattr(signal, "SIGXCPU"))) if not ok]
print(sys.platform, "| permisos tras chmod 700:", oct(m), "| falta:", ", ".join(falta) or "nada")' 2>&1 | first)
  kv "POSIX en $(basename "$PYCHK")" "$r"
  case "$r" in
    *"falta: nada"*) case "$r" in *"0o700"*) ;; *) note "FALLA  el sistema no respeta permisos 0700: doclens rechazaría el caché ('caché inseguro')" ;; esac ;;
    *falta:*) note "FALLA  Python sin APIs POSIX que doclens.py usa (${r##*falta: }): doclens falla al arrancar — requiere port o WSL2" ;;
  esac
fi
[ -e "$DLP/.venv.failed" ] && { kv "venv.failed" "sí: $(cat "$DLP/.venv.failed" 2>/dev/null | first)"; note "AVISO  hubo un fallo al crear el venv (doclens/.venv.failed)"; }
[ -n "${DOCLENS_PYTHON:-}" ] && kv "DOCLENS_PYTHON" "$DOCLENS_PYTHON"

# ── 3. Hook en vivo (sobre copias en un temporal) ──────────────────────────────
hdr "3. Prueba del hook en vivo"
SRC_HOOK="$HOOK"; [ -f "$SRC_HOOK" ] || SRC_HOOK="$HOME_DL/adapters/claude-code/doc-guard.sh"
SRC_DL="$DLP/doclens.py"; [ -f "$SRC_DL" ] || SRC_DL="$HOME_DL/doclens.py"
kv "hook probado" "$SRC_HOOK"
W=$(mktemp -d 2>/dev/null || mktemp -d -t doclens-doctor) || { echo "  no se pudo crear un temporal"; W=""; }
cleanup() { [ -n "$W" ] && [ -d "$W" ] && rm -r -f "$W"; }
trap cleanup EXIT
if [ -n "$W" ]; then
  W="$(cd "$W" && pwd -P)"
  mkdir -m 700 "$W/tmp" "$W/cache" "$W/xdg"   # 0700: doclens rechaza un caché abierto
  # PDF mínimo (MuPDF repara la tabla xref faltante)
  printf '%s\n' '%PDF-1.4' '1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj' '2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj' \
    '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj' \
    '4 0 obj<</Length 47>>stream' 'BT /F1 14 Tf 20 50 Td (DOCLENS DOCTOR OK) Tj ET' 'endstream endobj' \
    '5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj' 'trailer<</Root 1 0 R>>' '%%EOF' > "$W/prueba.pdf"

  # proyecto de prueba: copia del hook y de doclens.py; SIN sync.sh (así nunca crea un venv)
  mkproj() {
    mkdir -p "$1/.claude/hooks" "$1/doclens"
    cp "$SRC_HOOK" "$1/.claude/hooks/doc-guard.sh"; cp "$SRC_DL" "$1/doclens/doclens.py"
    [ -d "$HOME_DL/models" ] && cp -R "$HOME_DL/models" "$1/doclens/" 2>/dev/null
    [ -d "$DLP/models" ] && cp -R "$DLP/models" "$1/doclens/" 2>/dev/null
  }
  # json_path <ruta> → la ruta escapada para JSON (las \ de Windows se duplican)
  json_path() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
  probe() {  # probe <proyecto> <ruta-del-pdf> → imprime "rc|segundos|primera línea de stderr"
    local root="$1" fp t0 rc out
    fp=$(json_path "$2"); t0=$SECONDS
    out=$(printf '{"tool_name":"Read","tool_input":{"file_path":"%s"}}' "$fp" \
      | (cd "$root" && tmo 110 env CLAUDE_PROJECT_DIR="$root" TMPDIR="$W/tmp" DOCLENS_CACHE="$W/cache" \
          XDG_CACHE_HOME="$W/xdg" DOCLENS_HOOK_WAIT=60 bash "$root/.claude/hooks/doc-guard.sh" 2>&1 >/dev/null))
    rc=$?
    printf '%s|%s|%s' "$rc" "$((SECONDS - t0))" "$(printf '%s' "$out" | first)"
  }
  verdict() {  # verdict <etiqueta> <resultado de probe>
    local rc="${2%%|*}" rest="${2#*|}"; local s="${rest%%|*}" msg="${rest#*|}"
    case "$rc" in
      0) kv "$1" "rc=0 en ${s}s → DEJA PASAR el PDF crudo"; echo "DEJA" ;;
      2) case "$msg" in
           *pre-procesado*) if grep -rqs 'DOCLENS DOCTOR OK' "$W/cache"; then kv "$1" "rc=2 en ${s}s → digest correcto"; echo "OK"
                            else kv "$1" "rc=2 en ${s}s → dice pre-procesado pero el digest no tiene el texto"; echo "RARO"; fi ;;
           *) kv "$1" "rc=2 en ${s}s → bloquea sin digest: $msg"; echo "BLOQUEA" ;;
         esac ;;
      *) kv "$1" "rc=$rc en ${s}s → error del hook (Claude Code lo trata como no bloqueante: el PDF pasa): $msg"; echo "ERROR" ;;
    esac
  }
  PATHS=("$W/prueba.pdf"); $WIN && PATHS=("$(winpath "$W/prueba.pdf")" "$W/prueba.pdf")

  mkproj "$W/pa"
  # 0) un hook roto (CRLF, sintaxis) también sale con 2: "bloquea" el PDF, pero también
  # TODOS los Reads normales. Un .txt debe pasar con rc=0
  echo "  0) Read de un archivo de texto: debe PASAR (rc=0)"
  echo "nota" > "$W/nota.txt"
  r=$(probe "$W/pa" "${PATHS[0]%prueba.pdf}nota.txt"); rc0="${r%%|*}"
  if [ "$rc0" = 0 ]; then
    kv "    nota.txt" "rc=0 → pasa"
    note "OK     el hook deja pasar Reads normales (prueba 0)"
  else
    kv "    nota.txt" "rc=$rc0 → $(printf '%s' "${r#*|*|}")"
    note "FALLA  el hook está roto: bloquea también Reads normales (rc=$rc0). Con CRLF o un error de bash, Claude Code no puede leer NINGÚN archivo"
  fi
  echo "  A) sin doclens disponible (sin venv): debe BLOQUEAR, nunca dejar pasar"
  A_BAD=false
  for p in "${PATHS[@]}"; do
    v=$(verdict "    $p" "$(probe "$W/pa" "$p")")
    printf '%s\n' "$v" | sed '$d'
    case "$(printf '%s' "$v" | tail -1)" in DEJA|ERROR) A_BAD=true ;; esac
  done
  if $A_BAD; then note "FALLA  sin doclens, el hook deja pasar el PDF crudo (prueba A)"; else note "OK     sin doclens, el hook bloquea (prueba A)"; fi

  INTERP="${DOCLENS_PYTHON:-$VENV}"
  if [ -n "$INTERP" ]; then
    echo "  B) con el intérprete $INTERP: debe producir el digest"
    mkproj "$W/pb"; mkdir -p "$W/pb/doclens/.venv/bin"
    printf '#!/bin/sh\nexec "%s" "$@"\n' "$INTERP" > "$W/pb/doclens/.venv/bin/python"; chmod +x "$W/pb/doclens/.venv/bin/python"
    B_OK=false; B_ANY=false
    for p in "${PATHS[@]}"; do
      rm -r -f "$W/cache"; mkdir -m 700 "$W/cache"
      v=$(verdict "    $p" "$(probe "$W/pb" "$p")")
      printf '%s\n' "$v" | sed '$d'
      case "$(printf '%s' "$v" | tail -1)" in OK) B_OK=true ;; DEJA|ERROR) B_ANY=true ;; esac
    done
    if $B_OK; then note "OK     doclens produce el digest de punta a punta (prueba B)"
    else note "FALLA  doclens no produjo el digest con el venv (prueba B)$($B_ANY && echo ' y el PDF crudo pasó')"; fi
  else
    echo "  B) omitida: no hay venv ni DOCLENS_PYTHON con qué probar doclens"
  fi
fi

# ── 4. Resumen ─────────────────────────────────────────────────────────────────
hdr "4. Resumen"
printf '%s' "$SUMMARY" | sed 's/^/  /'
echo
echo "Comparte esta salida completa con quien mantiene ai-tooling (incluye rutas con tu usuario)."
exit 0
