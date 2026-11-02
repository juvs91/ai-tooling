#!/bin/bash
# distributable: true
# event: PreToolUse
# matcher: Read
# timeout: 120
# doc-guard.sh — PreToolUse hook (Read) — interceptor de documentos
# Exit 0 = permitir, Exit 2 = bloquear (stderr = razón / instrucciones)
#
# POR QUÉ EXISTE:
# Este repo corre agentes contra el backend Kimi (api.kimi.com/coding), que
# acepta image_in/video_in pero NO bloques PDF: un Read directo de .pdf
# responde HTTP 400, el bloque queda en el historial y TODOS los requests
# siguientes fallan — la sesión "muere" (ni /compact ni --resume la salvan).
#
# QUÉ HACE (filosofía: procesar primero, LLM después):
# - .pdf  -> corre doclens/doclens.py, que genera <archivo>.inspect/ con
#   index.md (metadata + texto + topología con coordenadas + tablas en
#   markdown + OCR spa si el PDF es scan), pages/page-N.png (renders) y
#   figures/ (imágenes embebidas + su texto vía OCR). Bloquea el Read
#   original y redirige al agente al index.md.
# - imagen > 8 MB -> genera copia downscaled y redirige a ella.
# - imagen normal -> permite (el modelo de visión la lee directo).
#
# Estructura (ADR-0076, igual en ai-tooling y en cada consumer): doclens/doclens.py +
# doclens/.venv. Sin pasos manuales: si falta el venv, este hook lo prepara la primera
# vez con `bash doclens/sync.sh --venv` (en consumers ya lo hace auto-sync al iniciar
# sesión). OCR opcional: brew install tesseract tesseract-lang.
# DOCLENS_PYTHON (opcional): intérprete ya preparado con las dependencias de doclens.
#
# NUNCA espera más de DOCLENS_HOOK_WAIT (90 s), por debajo del timeout de 120 s: un hook
# PreToolUse que vence su timeout NO bloquea (Claude Code lo trata como error no
# bloqueante y el Read sigue: el PDF crudo llegaría al modelo). Lo que no termina a
# tiempo (venv la primera vez, PDF enorme) sigue en segundo plano y el hook bloquea
# con "reintenta en un momento".

INPUT=$(cat)
if command -v jq >/dev/null 2>&1; then
  TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')
  FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')
elif command -v python3 >/dev/null 2>&1 \
     && TOOL=$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_name",""))' 2>/dev/null); then
  # Sin jq: decodificar el JSON de verdad (los escapes \" o \u0070 no engañan a un regex).
  # Si python3 existe pero falla, se cae al bloqueo de abajo (nunca fail-open).
  FILE_PATH=$(printf '%s' "$INPUT" | python3 -c 'import json,sys; sys.stdout.write((json.load(sys.stdin).get("tool_input") or {}).get("file_path",""))' 2>/dev/null)
else
  # Sin jq ni python3: fail-closed para todo lo que pueda ser un PDF (también escapado)
  if printf '%s' "$INPUT" | grep -qiE 'pdf|\\u00'; then
    echo "doc-guard: faltan jq y python3 — no se puede leer la petición. Instala jq (brew install jq / apt install jq) y reintenta." >&2
    exit 2
  fi
  exit 0
fi

[ "$TOOL" != "Read" ] && exit 0
[ -z "$FILE_PATH" ] && exit 0

# Extensión sobre la cadena COMPLETA (no por línea: un nombre con salto de línea
# "a.txt<NL>b.pdf" no puede esconder el .pdf)
ext_of() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    *.pdf) echo .pdf ;; *.png) echo .png ;; *.jpg) echo .jpg ;; *.jpeg) echo .jpeg ;;
    *.webp) echo .webp ;; *.tif) echo .tif ;; *.tiff) echo .tiff ;; *) echo "" ;;
  esac
}
# shellcheck disable=SC2088  # el "~" literal es intencional: se expande a mano
case "$FILE_PATH" in "~"|"~/"*) FILE_PATH="$HOME${FILE_PATH#\~}" ;; esac
EXT=$(ext_of "$FILE_PATH")
# Un PDF sin extensión .pdf (renombrado, sin extensión) se detecta por contenido
if [ -z "$EXT" ] && [ -f "$FILE_PATH" ] && [ "$(head -c 5 "$FILE_PATH" 2>/dev/null)" = "%PDF-" ]; then EXT=.pdf; fi
[ -n "$EXT" ] || exit 0

CACHE="${DOCLENS_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/doclens}"
# Decisiones sobre la ruta CANÓNICA (.. resuelto, symlinks seguidos): una exención léxica
# como "x.inspect/../huge.png" o un symlink "notas.txt -> factura.pdf" no evaden el hook
CACHE_REAL="$CACHE"
if command -v python3 >/dev/null 2>&1; then
  # 3er campo: la ruta para mensajes, solo con caracteres imprimibles (quita también U+2028,
  # NEL y marcas bidi, que tr -d '\000-\037' deja pasar)
  { IFS= read -r -d '' C1; IFS= read -r -d '' C2; IFS= read -r -d '' C3; } < <(python3 -c 'import os,sys; r=os.path.realpath(sys.argv[1]); sys.stdout.write(r + "\0" + os.path.realpath(sys.argv[2]) + "\0" + "".join(c if c.isprintable() else "?" for c in r) + "\0")' "$FILE_PATH" "$CACHE" 2>/dev/null)
  [ -n "${C1:-}" ] && FILE_PATH="$C1"; [ -n "${C2:-}" ] && CACHE_REAL="$C2"
  [ -n "$(ext_of "$FILE_PATH")" ] && EXT=$(ext_of "$FILE_PATH")
fi
# Un archivo con extensión de imagen que en realidad es un PDF se trata como PDF
case "$EXT" in
  .png|.jpg|.jpeg|.webp|.tif|.tiff)
    if [ -f "$FILE_PATH" ] && [ "$(head -c 5 "$FILE_PATH" 2>/dev/null)" = "%PDF-" ]; then EXT=.pdf; fi ;;
esac
# Los artifacts de doclens (md/png de un digest) se leen libremente, pero SOLO dentro del
# caché; un PDF nunca está exento
case "$FILE_PATH" in
  "$CACHE_REAL"/*.inspect/*|"$CACHE"/*.inspect/*) [ "$EXT" = ".pdf" ] || exit 0 ;;
esac
case "$EXT" in
  .pdf) ;;
  .png|.jpg|.jpeg|.webp|.tif|.tiff)
    # Sin leer: un FIFO o un symlink a /dev/zero con nombre .png colgaba el hook en wc
    [ -f "$FILE_PATH" ] || exit 0
    [ -n "$(find "$FILE_PATH" -prune -size +16384 2>/dev/null)" ] || exit 0   # > 8 MB (bloques de 512)
    SIZE=$(ls -ln "$FILE_PATH" 2>/dev/null | awk '{print $5}'); SIZE=${SIZE:-8388609} ;;
  *) exit 0 ;;
esac
# Para mensajes: sin caracteres de control. Para comandos sugeridos: escapada para shell
# Sin python3: solo ASCII imprimible (sin locale, tr/cut trabajan por bytes: nada de
# U+2028/NEL/bidi ni UTF-8 cortado a la mitad)
SAFE_PATH=${C3:-$(printf '%s' "$FILE_PATH" | LC_ALL=C tr -c '\040-\176' '?')}
# Ruta en los comandos sugeridos: solo si es ASCII simple, escapada con %q. Si no (Unicode,
# controles), %q de bash 3.2 deja bytes crudos a medio escapar: placeholder "$F" y el modelo
# asigna la ruta — los comandos nunca llevan bytes elegidos por quien nombró el archivo.
F_NOTE=""
case "$FILE_PATH" in
  *[!A-Za-z0-9._/@+=,:%\ -]*) Q='"$F"'; F_NOTE="(F = la ruta del archivo que intentaste leer, entre comillas simples) " ;;
  *) Q=$(printf '%q' "$FILE_PATH") ;;
esac

# Raíz del proyecto, no el cwd: el cwd del hook sigue al `cd` del Bash tool y a los
# worktrees (ADR-0073), donde el venv (gitignorado) no existe. CLAUDE_PROJECT_DIR
# siempre apunta a la raíz principal.
ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
DOCLENS="$ROOT/doclens/doclens.py"; VENV_PY="$ROOT/doclens/.venv/bin/python"; SYNC="$ROOT/doclens/sync.sh"
# Transición: consumer aún con la estructura vieja (sync_doclens:false o sin migrar)
if [ ! -f "$DOCLENS" ] && [ -f "$ROOT/scripts/doclens.py" ]; then
  DOCLENS="$ROOT/scripts/doclens.py"; VENV_PY="$ROOT/scripts/.venv-doc-tools/bin/python"; SYNC=""
fi
OVERRIDE=""; OVERRIDE_BROKEN=false
[ -n "${DOCLENS_PYTHON:-}" ] && OVERRIDE=$(command -v "$DOCLENS_PYTHON" 2>/dev/null || true)
PY="$VENV_PY"

# Un solo plazo para TODO el hook (venv + procesamiento), no uno por paso
# Solo dígitos y ≤ 100 (por debajo del timeout de 120 s): nunca se evalúa texto arbitrario
WAIT="${DOCLENS_HOOK_WAIT:-90}"
case "$WAIT" in ''|*[!0-9]*) WAIT=90 ;; esac
[ "${#WAIT}" -gt 3 ] && WAIT=90
WAIT=$((10#$WAIT))   # "08" no es octal
[ "$WAIT" -gt 100 ] && WAIT=100
DEADLINE=$((SECONDS + WAIT))
MAX_JOBS="${DOCLENS_MAX_JOBS:-3}"; case "$MAX_JOBS" in ''|*[!0-9]*) MAX_JOBS=3 ;; esac
[ "${#MAX_JOBS}" -gt 2 ] && MAX_JOBS=3; MAX_JOBS=$((10#$MAX_JOBS)); [ "$MAX_JOBS" -lt 1 ] && MAX_JOBS=1
MAX_MB="${DOCLENS_MAX_MB:-200}"; case "$MAX_MB" in ''|*[!0-9]*) MAX_MB=200 ;; esac
[ "${#MAX_MB}" -gt 5 ] && MAX_MB=200; MAX_MB=$((10#$MAX_MB))
# Estado privado del hook: directorio real (no symlink), del usuario y 0700. Candidatos: el
# de TMPDIR y, si no sirve (otro usuario lo pre-creó, permisos abiertos), uno bajo ~/.cache.
# Si ninguno sirve se bloquea SIN lanzar nada: sin estado no hay deduplicación ni tope.
private_dir() { [ -d "$1" ] && [ ! -L "$1" ] && [ -O "$1" ] && [ "$(ls -ld "$1" | cut -c1-10)" = drwx------ ]; }
prepare_state() {
  [ -L "$1" ] && return 1
  mkdir -p "$(dirname "$1")" 2>/dev/null; mkdir -m 700 "$1" 2>/dev/null
  # Versiones previas lo creaban 755: si es nuestro, real y NADIE más pudo escribir en él
  # (sin w de grupo/otros), endurecerlo a 700 es seguro. Si otros podían escribir, no se usa.
  if [ -d "$1" ] && [ ! -L "$1" ] && [ -O "$1" ]; then
    case "$(ls -ld "$1" | cut -c1-10)" in ?????w????|????????w?) ;; *) chmod 700 "$1" 2>/dev/null ;; esac
  fi
  private_dir "$1"
}
STATE="${TMPDIR:-/tmp}"; STATE="${STATE%/}/doclens-hook-$(id -u)"
if ! prepare_state "$STATE"; then
  STATE="${XDG_CACHE_HOME:-$HOME/.cache}/doclens-hook"
  prepare_state "$STATE" || {
    echo "doc-guard: no hay un directorio de estado privado utilizable; no se procesa $SAFE_PATH (revisa los permisos de ${TMPDIR:-/tmp} y ~/.cache)." >&2
    exit 2
  }
fi
find "$STATE" -type f -mmin +1440 -delete 2>/dev/null   # estado de más de 1 día
# Clave por documento para deduplicar (pidfile): sha256 de la ruta — un CRC32 (cksum) se
# colisiona a propósito y dos Reads cruzaban sus resultados
DOC_KEY=$(printf '%s' "$FILE_PATH" | { sha256sum 2>/dev/null || shasum -a 256; } | cut -c1-40)
INSPECT_DIR=""; LAST_ERR=""; VENV_ERR=""; AGE=0; BUSY=0
# Última línea, solo imprimibles y ≤ 200 caracteres (no bytes: sin UTF-8 cortado a la mitad)
sanitize() {
  local in r=""; in=$(cat)
  if command -v python3 >/dev/null 2>&1; then
    r=$(printf '%s' "$in" | python3 -c 'import sys; l=(sys.stdin.read().strip().splitlines() or [""])[-1]; sys.stdout.write("".join(c if c.isprintable() else " " for c in l)[:200])' 2>/dev/null)
  fi
  # python3 ausente o roto (sin salida): el método simple, nunca un mensaje vacío
  [ -n "$r" ] || r=$(printf '%s\n' "$in" | tail -n 1 | LC_ALL=C tr -c '\040-\176\n' ' ' | cut -c1-200)
  printf '%s' "$r"
}
# Versión de formato que produce este doclens.py: el atajo de caché no sirve digests viejos
CUR_FMT=$(sed -n 's/^SIDECAR_FORMAT_VERSION = \([0-9][0-9]*\).*/\1/p' "$DOCLENS" 2>/dev/null | head -n 1)
# INSPECT_DIR válido = <caché>/<sha256 de 64 hex>.inspect (o, en la estructura vieja de
# scripts/, el sidecar junto al PDF). Cualquier otra cosa no se le sirve al modelo.
valid_inspect() {
  local b
  case "$INSPECT_DIR" in
    "$CACHE"/*.inspect|"$CACHE_REAL"/*.inspect)
      b=${INSPECT_DIR##*/}; b=${b%.inspect}
      [ "${#b}" -eq 64 ] || return 1
      case "$b" in *[!0-9a-f]*) return 1 ;; esac
      [ ! -L "$INSPECT_DIR" ] ;;
    *) [ -z "$SYNC" ] && [ "$INSPECT_DIR" = "$FILE_PATH.inspect" ] ;;
  esac
}

# Proceso en su propio grupo: sobrevive a que el hook termine o lo maten. Se llama solo
# como `detach … &`: el exec reemplaza ese subshell, así $! es el PID del proceso real
# (sin exec, $! sería un bash intermedio y no se reconocería como nuestro en ps).
detach() {
  if command -v setsid >/dev/null 2>&1; then exec setsid "$@"
  elif command -v perl >/dev/null 2>&1; then exec perl -e 'setpgrp(0,0); exec @ARGV' "$@"
  else exec nohup "$@"; fi
}
bounded() {  # bounded <out> <pidfile> cmd… → exit del cmd, o 124 si sigue corriendo al vencer DEADLINE
  # (124 como timeout(1): no choca con los códigos del cmd — doclens usa 1..4)
  local out="$1" pidf="$2" pid; shift 2
  local n=0 rc=0
  # El envoltorio borra el pidfile al terminar (también si sigue en segundo plano)
  # ": doclens-bg" al inicio: marca visible en ps aunque recorte la línea de comando
  # DL_LIMITS: CPU (y memoria en Linux) acotadas para documentos hostiles; no para crear el venv
  # stdout y stderr por separado: lo que doclens loguea (p. ej. un nombre de archivo con
  # saltos de línea) no puede parecer una línea INSPECT_DIR= de stdout
  detach sh -c ': doclens-bg; if [ -n "$DL_LIMITS" ]; then ulimit -H -t 960 2>/dev/null; ulimit -S -t 900 2>/dev/null; ulimit -f 4194304 2>/dev/null; [ "$(uname)" = Linux ] && ulimit -v 4194304 2>/dev/null; fi; pf=$1; shift; "$@"; rc=$?; rm -f "$pf"; exit $rc' doclens-bg "$pidf" "$@" > "$out" 2> "$out.err" < /dev/null &
  pid=$!; echo "$pid $(date +%s)" > "$pidf"   # PID + inicio (para decir cuánto lleva)
  while kill -0 "$pid" 2>/dev/null; do
    [ "$SECONDS" -ge "$DEADLINE" ] && return 124
    if [ "$n" -lt 20 ]; then sleep 0.05 2>/dev/null || sleep 1; else sleep 0.2 2>/dev/null || sleep 1; fi
    n=$((n + 1))
  done
  wait "$pid" || rc=$?
  rm -f "$pidf"
  return "$rc"
}
# Proceso vivo con este documento: el PID existe Y es nuestro envoltorio (doclens-bg). Un PID
# reutilizado por el sistema no cuenta, y un proceso legítimo largo no se duplica.
proc_args() { ps -ww -o args= -p "$1" 2>/dev/null || ps -o pid,args 2>/dev/null | awk -v p="$1" '$1 == p { $1 = ""; print }'; }
alive() {
  local line p t; line=$(cat "$1" 2>/dev/null) || return 1
  p=${line%% *}; t=${line#* }
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null || return 1
  if command -v ps >/dev/null 2>&1; then
    case "$(proc_args "$p")" in *doclens-bg*) ;; *) return 1 ;; esac
  fi
  case "$t" in ''|*[!0-9]*) AGE=0 ;; *) AGE=$(( $(date +%s) - t )) ;; esac
}
run_doclens() {  # 0 digest en $INSPECT_DIR · 4 sigue procesando · otro: doclens falló
  local out pidf="$STATE/$DOC_KEY.pid" rc=0
  if alive "$pidf"; then              # ya hay un proceso con este documento: no lanzar otro
    [ "$AGE" -ge 3600 ] && return 6   # > 1 h: probablemente colgado
    return 4
  fi
  # Tope de procesos doclens simultáneos por usuario (N documentos hostiles ≠ N núcleos)
  local f n=0
  # Solo cuentan trabajos de < 1 h (doclens se corta solo a los 30 min: DOCLENS_MAX_SECONDS)
  for f in "$STATE"/*.pid; do alive "$f" && [ "$AGE" -lt 3600 ] && n=$((n + 1)); done
  AGE=0
  [ "$n" -ge "$MAX_JOBS" ] && { BUSY=$n; return 8; }
  out=$(mktemp "$STATE/out.XXXXXX") || return 1   # salida propia de esta invocación
  DL_LIMITS=1 bounded "$out" "$pidf" "$PY" "$DOCLENS" "$FILE_PATH" || rc=$?
  [ "$rc" = 124 ] && { AGE=0; return 4; }
  INSPECT_DIR=$(grep '^INSPECT_DIR=' "$out" 2>/dev/null | tail -n 1 | cut -d= -f2-)
  valid_inspect || INSPECT_DIR=""
  # Al modelo solo llega una línea corta y sin caracteres de control
  LAST_ERR=$(sanitize < "$out.err")
  rm -f "$out" "$out.err"
  [ -n "$INSPECT_DIR" ]
}
venv_lock_busy() {  # mismo criterio que doclens/sync.sh: PID vivo, o sin PID y < 10 min
  local lock="$ROOT/doclens/.venv.lock" pid
  [ -d "$lock" ] || return 1
  pid=$(cat "$lock/pid" 2>/dev/null || true)
  if [ -n "$pid" ]; then kill -0 "$pid" 2>/dev/null; else [ -z "$(find "$lock" -maxdepth 0 -mmin +10 2>/dev/null)" ]; fi
}
bootstrap() {  # 0 venv listo · 1 no disponible · 4 preparándose
  [ -n "$SYNC" ] && [ -f "$SYNC" ] || return 1
  venv_lock_busy && return 4
  [ -n "$(find "$ROOT/doclens/.venv.failed" -mmin -60 2>/dev/null)" ] && return 1
  local rc=0
  local VOUT; VOUT=$(mktemp "$STATE/venv.XXXXXX") || return 1
  bounded "$VOUT" "$VOUT.pid" env -u DOCLENS_PYTHON bash "$SYNC" "$ROOT" --venv || rc=$?
  VENV_ERR=$(cat "$VOUT" "$VOUT.err" 2>/dev/null | grep 'ERROR' | sanitize)
  rm -f "$VOUT" "$VOUT.err"
  case "$rc" in
    0) [ -x "$VENV_PY" ] ;;
    3|124) return 4 ;;
    *) return 1 ;;
  esac
}
process() {  # 0 digest · 1 no disponible · 4 en curso · 5 falló con ESTE archivo · 6 colgado (> 1 h) · 8 tope de trabajos
  local r=0
  if [ -n "$OVERRIDE" ] && [ -f "$DOCLENS" ]; then
    PY="$OVERRIDE"; run_doclens || r=$?
    [ "$r" = 0 ] && return 0
    case "$r" in 4|6|8) return "$r" ;; esac
    "$OVERRIDE" -c 'import fitz' >/dev/null 2>&1 && return 5
    OVERRIDE_BROKEN=true            # DOCLENS_PYTHON sin dependencias: seguir con doclens/.venv
    PY="$VENV_PY"; r=0
  fi
  if [ -x "$VENV_PY" ] && [ -f "$DOCLENS" ]; then
    run_doclens || r=$?
    [ "$r" = 0 ] && return 0
    case "$r" in 4|6|8) return "$r" ;; esac
    # Falló: si el intérprete está sano, el problema es el archivo (no reinstalar)
    "$VENV_PY" -c 'import fitz' >/dev/null 2>&1 && return 5
  fi
  [ -f "$DOCLENS" ] || return 1
  r=0; bootstrap || r=$?            # venv ausente o roto: sync --venv lo crea/repara
  [ "$r" = 0 ] || return "$r"
  r=0; run_doclens || r=$?
  [ "$r" = 0 ] && return 0
  case "$r" in 4|6|8) return "$r" ;; esac
  return 5
}
busy() {
  echo "doclens ya está procesando $BUSY documentos (máximo DOCLENS_MAX_JOBS=$MAX_JOBS); $SAFE_PATH no se empezó. Reintenta el Read en un momento." >&2
  exit 2
}
in_progress() {
  local lleva=""; [ "$AGE" -gt 0 ] && lleva=" (lleva $((AGE / 60)) min $((AGE % 60)) s)"
  echo "doclens está preparando o procesando $SAFE_PATH$lleva (la primera vez crea doclens/.venv; un documento grande o escaneado tarda). Sigue en segundo plano: reintenta el Read en ~30 s." >&2
  exit 2
}
hash_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" 2>/dev/null | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1; fi
}

# ── PDF: pre-procesar y redirigir ─────────────────────────────────────────
if [ "$EXT" = ".pdf" ]; then
  PRC=0
  # Camino rápido: digest del MISMO contenido en el caché por usuario (ADR-0077). Solo si
  # el caché es privado y propio (como valida doclens.py) y el archivo es regular: un
  # symlink a /dev/zero no se hashea.
  H=""
  # Tamaño antes de leer (find -size: solo stat); el hash corre acotado por el plazo — un
  # archivo enorme o en un FS lento no puede llevar el hook más allá de su timeout
  HF=$(mktemp "$STATE/h.XXXXXX") || HF=""
  if [ -n "$HF" ] && [ -f "$FILE_PATH" ] && private_dir "$CACHE" \
     && [ -z "$(find "$FILE_PATH" -prune -size +$((MAX_MB * 2048)) 2>/dev/null)" ] \
     && bounded "$HF" "$HF.pid" sh -c 'sha256sum "$1" 2>/dev/null || shasum -a 256 "$1"' doclens-hash "$FILE_PATH"; then
    H=$(cut -d' ' -f1 "$HF" 2>/dev/null)
    case "$H" in *[!0-9a-f]*) H="" ;; esac
  fi
  [ -n "$HF" ] && rm -f "$HF" "$HF.err"
  # Solo digests del formato que produce el doclens.py actual: uno viejo (p. ej. previo al
  # saneo) se regenera en vez de servirse tal cual
  if [ -n "$H" ] && [ -f "$CACHE/$H.inspect/index.md" ] && [ ! -L "$CACHE/$H.inspect" ] \
     && { [ -z "$CUR_FMT" ] || [ "$(cat "$CACHE/$H.inspect/.format" 2>/dev/null)" = "$CUR_FMT" ]; }; then
    INSPECT_DIR="$CACHE/$H.inspect"
  else
    process || PRC=$?
  fi
  [ "$PRC" = 4 ] && in_progress
  [ "$PRC" = 8 ] && busy
  if [ "$PRC" = 0 ] && [ -f "$INSPECT_DIR/as-image.png" ]; then
    echo "Este archivo .pdf es en realidad una imagen: lee $INSPECT_DIR/as-image.png (metadata en $INSPECT_DIR/index.md). No leas $SAFE_PATH directo." >&2
    exit 2
  fi
  if [ "$PRC" = 0 ] && [ -f "$INSPECT_DIR/index.md" ]; then
    cat >&2 <<EOF
PDF pre-procesado por doclens — NO leas el PDF original (el backend Kimi
lo rechaza con 400 y la sesión muere). El documento ya está digerido en:

  $INSPECT_DIR/

Ruta de consumo:
1. Lee $INSPECT_DIR/index.md — metadata, texto completo, topología con
   coordenadas, tablas en markdown, inventario de figuras.
2. Si necesitas revisión visual: lee $INSPECT_DIR/pages/page-N.png
3. Figuras embebidas (y su texto vía OCR): $INSPECT_DIR/figures/
EOF
    exit 2
  fi

  # Fallback manual: el motivo exacto, para no mandar a reinstalar lo que está sano
  if [ "$PRC" = 6 ]; then
    WHY="doclens lleva más de $((AGE / 60)) min con este archivo y probablemente está colgado: usa la ruta manual, o termina ese proceso (ps | grep doclens-bg) y reintenta."
  elif [ "$PRC" = 5 ]; then
    case "$LAST_ERR" in
      *contraseña*) WHY="doclens: $LAST_ERR. Pide la contraseña al usuario o una versión sin protección." ;;
      *) WHY="doclens no pudo procesar este archivo: $LAST_ERR. ¿PDF dañado?" ;;
    esac
  elif $OVERRIDE_BROKEN; then
    WHY="DOCLENS_PYTHON=$DOCLENS_PYTHON no tiene las dependencias de doclens y doclens/.venv no está disponible: bash doclens/sync.sh --force (o instala las dependencias en ese intérprete)."
  elif [ -n "$SYNC" ] && [ -f "$SYNC" ]; then
    WHY="El processor doclens no está disponible${VENV_ERR:+ ($VENV_ERR)}. Prepáralo (crea doclens/.venv): bash doclens/sync.sh --force"
    [ -z "$VENV_ERR" ] && [ -f "$ROOT/doclens/.venv.failed" ] && WHY="$WHY (el último intento falló el $(cat "$ROOT/doclens/.venv.failed" 2>/dev/null); reintento automático en 1 h)"
  elif [ -f "$DOCLENS" ]; then
    WHY="El processor doclens no está disponible (estructura vieja en scripts/): ejecuta el sync de ai-tooling (doclens/sync.sh <repo>) para migrar a doclens/."
  else
    WHY="Este proyecto no tiene doclens: ejecuta el sync de ai-tooling (doclens/sync.sh <repo>)."
  fi
  cat >&2 <<EOF
BLOCKED: Read directo de PDF ($SAFE_PATH).

El backend Kimi de este repo NO acepta bloques PDF: HTTP 400 y sesión muerta.
$WHY

Ruta manual mientras tanto (con las herramientas que haya instaladas; usa un
directorio propio: D=\$(mktemp -d)) ${F_NOTE}:
1. Visual (macOS): sips -s format png $Q --out "\$D/pagina.png"
   y lee el PNG (multi-página, con poppler: pdftoppm -png $Q "\$D/pagina")
2. Texto (con poppler): pdftotext $Q -
EOF
  exit 2
fi

# ── Imágenes: solo interceptar si son demasiado grandes ───────────────────
case "$EXT" in
  .png|.jpg|.jpeg|.webp|.tif|.tiff)
    if [ "$SIZE" -gt 8388608 ]; then
      PRC=0; process || PRC=$?
      case "$PRC" in 4|6) in_progress ;; 8) busy ;; esac
      if [ "$PRC" = 5 ]; then
        echo "Imagen de $((SIZE / 1048576)) MB que doclens no pudo abrir ($LAST_ERR): probablemente está dañada; no la leas directo." >&2
        exit 2
      fi
      SMALL=""; for f in downscaled.png as-image.png; do [ -f "$INSPECT_DIR/$f" ] && { SMALL="$INSPECT_DIR/$f"; break; }; done
      if [ "$PRC" = 0 ] && [ -n "$SMALL" ]; then
        echo "Imagen de $((SIZE / 1048576)) MB — demasiado grande para el API. Usa la copia reducida: $SMALL (metadata en $INSPECT_DIR/index.md)" >&2
        exit 2
      fi
    fi
    exit 0
    ;;
esac

exit 0
