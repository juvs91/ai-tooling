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
LOWER=$(printf '%s' "$FILE_PATH" | tr '[:upper:]' '[:lower:]')
case "$LOWER" in
  *.pdf) EXT=.pdf ;; *.png) EXT=.png ;; *.jpg) EXT=.jpg ;; *.jpeg) EXT=.jpeg ;;
  *.webp) EXT=.webp ;; *.tif) EXT=.tif ;; *.tiff) EXT=.tiff ;; *) EXT="" ;;
esac
# Los artifacts de doclens (md/png dentro de un .inspect/) se leen libremente; un PDF
# real que esté dentro de una carpeta *.inspect/ sí se procesa
case "$FILE_PATH" in
  *.inspect/*) [ "$EXT" = ".pdf" ] || exit 0 ;;
esac
case "$EXT" in
  .pdf) ;;
  .png|.jpg|.jpeg|.webp|.tif|.tiff)
    SIZE=$(wc -c < "$FILE_PATH" 2>/dev/null | tr -d ' '); SIZE=${SIZE:-0}   # stat -f%z es solo macOS
    [ "$SIZE" -gt 8388608 ] || exit 0 ;;
  *) exit 0 ;;
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
[ "$WAIT" -gt 100 ] && WAIT=100
DEADLINE=$((SECONDS + WAIT))
# Estado privado del hook: directorio real (no symlink), del usuario y 0700. Si no se puede
# verificar, un mktemp -d propio (también 0700). Solo se purga el directorio verificado.
private_dir() { [ -d "$1" ] && [ ! -L "$1" ] && [ -O "$1" ] && [ "$(ls -ld "$1" | cut -c1-10)" = drwx------ ]; }
STATE="${TMPDIR:-/tmp}"; STATE="${STATE%/}/doclens-hook-$(id -u)"
[ -L "$STATE" ] || mkdir -m 700 "$STATE" 2>/dev/null
# Versiones previas lo creaban 755: si es nuestro, real y NADIE más pudo escribir en él
# (sin w de grupo/otros), endurecerlo a 700 es seguro. Si otros podían escribir, no se usa.
if [ -d "$STATE" ] && [ ! -L "$STATE" ] && [ -O "$STATE" ]; then
  case "$(ls -ld "$STATE" | cut -c1-10)" in ?????w????|????????w?) ;; *) chmod 700 "$STATE" 2>/dev/null ;; esac
fi
TMP_STATE=""; KEEP_STATE=""
if private_dir "$STATE"; then
  find "$STATE" -type f -mmin +1440 -delete 2>/dev/null   # estado de más de 1 día
else
  STATE=$(mktemp -d 2>/dev/null) && private_dir "$STATE" || {
    echo "doc-guard: no hay un directorio temporal privado utilizable (TMPDIR=${TMPDIR:-}); no se puede procesar $FILE_PATH de forma segura." >&2
    exit 2
  }
  TMP_STATE=1
  # Temporal de un solo uso: se borra al salir, salvo que quede trabajo en segundo plano
  trap '[ -n "$TMP_STATE" ] && [ -z "$KEEP_STATE" ] && rm -rf "$STATE"' EXIT
fi
# Caché de digests por usuario (ADR-0077): mismo criterio que doclens.py
CACHE="${DOCLENS_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/doclens}"
DOC_KEY=$(printf '%s' "$FILE_PATH" | cksum | cut -d' ' -f1)
INSPECT_DIR=""; LAST_ERR=""; VENV_ERR=""; AGE=0

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
  detach sh -c ': doclens-bg; if [ -n "$DL_LIMITS" ]; then ulimit -t 900 2>/dev/null; [ "$(uname)" = Linux ] && ulimit -v 4194304 2>/dev/null; fi; pf=$1; shift; "$@"; rc=$?; rm -f "$pf"; exit $rc' doclens-bg "$pidf" "$@" > "$out" 2>&1 < /dev/null &
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
  local out="$STATE/$DOC_KEY.out" pidf="$STATE/$DOC_KEY.pid" rc=0
  if alive "$pidf"; then              # ya hay un proceso con este documento: no lanzar otro
    [ "$AGE" -ge 3600 ] && return 6   # > 1 h: probablemente colgado
    return 4
  fi
  # Tope de procesos doclens simultáneos por usuario (N documentos hostiles ≠ N núcleos)
  local f n=0
  for f in "$STATE"/*.pid; do alive "$f" && n=$((n + 1)); done
  AGE=0
  [ "$n" -ge "${DOCLENS_MAX_JOBS:-3}" ] && return 4
  DL_LIMITS=1 bounded "$out" "$pidf" "$PY" "$DOCLENS" "$FILE_PATH" || rc=$?
  [ "$rc" = 124 ] && { AGE=0; return 4; }
  INSPECT_DIR=$(grep '^INSPECT_DIR=' "$out" 2>/dev/null | cut -d= -f2-)
  # Al modelo solo llega una línea corta y sin caracteres de control
  LAST_ERR=$(grep -v '^INSPECT_DIR=' "$out" 2>/dev/null | tail -n 1 | tr -d '\000-\037' | cut -c1-200)
  rm -f "$out"
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
  bounded "$STATE/venv.out" "$STATE/venv.pid" env -u DOCLENS_PYTHON bash "$SYNC" "$ROOT" --venv || rc=$?
  VENV_ERR=$(grep 'ERROR' "$STATE/venv.out" 2>/dev/null | tail -n 1)
  rm -f "$STATE/venv.out"
  case "$rc" in
    0) [ -x "$VENV_PY" ] ;;
    3|124) return 4 ;;
    *) return 1 ;;
  esac
}
process() {  # 0 digest · 1 no disponible · 4 en curso · 5 falló con ESTE archivo · 6 colgado (> 1 h)
  local r=0
  if [ -n "$OVERRIDE" ] && [ -f "$DOCLENS" ]; then
    PY="$OVERRIDE"; run_doclens || r=$?
    [ "$r" = 0 ] && return 0
    case "$r" in 4|6) return "$r" ;; esac
    "$OVERRIDE" -c 'import fitz' >/dev/null 2>&1 && return 5
    OVERRIDE_BROKEN=true            # DOCLENS_PYTHON sin dependencias: seguir con doclens/.venv
    PY="$VENV_PY"; r=0
  fi
  if [ -x "$VENV_PY" ] && [ -f "$DOCLENS" ]; then
    run_doclens || r=$?
    [ "$r" = 0 ] && return 0
    case "$r" in 4|6) return "$r" ;; esac
    # Falló: si el intérprete está sano, el problema es el archivo (no reinstalar)
    "$VENV_PY" -c 'import fitz' >/dev/null 2>&1 && return 5
  fi
  [ -f "$DOCLENS" ] || return 1
  r=0; bootstrap || r=$?            # venv ausente o roto: sync --venv lo crea/repara
  [ "$r" = 0 ] || return "$r"
  r=0; run_doclens || r=$?
  [ "$r" = 0 ] && return 0
  case "$r" in 4|6) return "$r" ;; esac
  return 5
}
in_progress() {
  KEEP_STATE=1   # hay un proceso en segundo plano usando el estado
  local lleva=""; [ "$AGE" -gt 0 ] && lleva=" (lleva $((AGE / 60)) min $((AGE % 60)) s)"
  echo "doclens está preparando o procesando $FILE_PATH$lleva (la primera vez crea doclens/.venv; un documento grande o escaneado tarda). Sigue en segundo plano: reintenta el Read en ~30 s." >&2
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
  if [ -f "$FILE_PATH" ] && private_dir "$CACHE"; then H=$(hash_of "$FILE_PATH"); fi
  if [ -n "$H" ] && [ -f "$CACHE/$H.inspect/index.md" ] && [ ! -L "$CACHE/$H.inspect" ]; then
    INSPECT_DIR="$CACHE/$H.inspect"
  else
    process || PRC=$?
  fi
  [ "$PRC" = 4 ] && in_progress
  if [ "$PRC" = 0 ] && [ -f "$INSPECT_DIR/as-image.png" ]; then
    echo "Este archivo .pdf es en realidad una imagen: lee $INSPECT_DIR/as-image.png (metadata en $INSPECT_DIR/index.md). No leas $FILE_PATH directo." >&2
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
BLOCKED: Read directo de PDF ($FILE_PATH).

El backend Kimi de este repo NO acepta bloques PDF: HTTP 400 y sesión muerta.
$WHY

Ruta manual mientras tanto (con las herramientas que haya instaladas; usa un
directorio propio: D=\$(mktemp -d)):
1. Visual (macOS): sips -s format png "$FILE_PATH" --out "\$D/pagina.png"
   y lee el PNG (multi-página, con poppler: pdftoppm -png "$FILE_PATH" "\$D/pagina")
2. Texto (con poppler): pdftotext "$FILE_PATH" -
EOF
  exit 2
fi

# ── Imágenes: solo interceptar si son demasiado grandes ───────────────────
case "$EXT" in
  .png|.jpg|.jpeg|.webp|.tif|.tiff)
    if [ "$SIZE" -gt 8388608 ]; then
      PRC=0; process || PRC=$?
      case "$PRC" in 4|6) in_progress ;; esac
      if [ "$PRC" = 5 ]; then
        echo "Imagen de $((SIZE / 1048576)) MB que doclens no pudo abrir ($LAST_ERR): probablemente está dañada; no la leas directo." >&2
        exit 2
      fi
      if [ "$PRC" = 0 ] && [ -f "$INSPECT_DIR/downscaled.png" ]; then
        echo "Imagen de $((SIZE / 1048576)) MB — demasiado grande para el API. Usa la copia downscaled: $INSPECT_DIR/downscaled.png (metadata en $INSPECT_DIR/index.md)" >&2
        exit 2
      fi
    fi
    exit 0
    ;;
esac

exit 0
