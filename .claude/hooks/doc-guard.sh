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
if ! command -v jq >/dev/null 2>&1; then
  # Sin jq no se puede leer el JSON: los PDFs se bloquean igual (el resto pasa)
  if printf '%s' "$INPUT" | grep -qiE '"file_path"[[:space:]]*:[[:space:]]*"[^"]*\.pdf"'; then
    echo "doc-guard: falta jq — no se puede pre-procesar el PDF. Instala jq (brew install jq / apt install jq) y reintenta." >&2
    exit 2
  fi
  exit 0
fi
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

[ "$TOOL" != "Read" ] && exit 0
[ -z "$FILE_PATH" ] && exit 0

EXT=$(echo "$FILE_PATH" | grep -oiE '\.[a-z0-9]+$' | tr '[:upper:]' '[:lower:]')
# Los artifacts de doclens (md/png dentro de un .inspect/) se leen libremente; un PDF
# real que esté dentro de una carpeta *.inspect/ sí se procesa
case "$FILE_PATH" in
  *.inspect/*) [ "$EXT" = ".pdf" ] || exit 0 ;;
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
[ -n "${DOCLENS_PYTHON:-}" ] && VENV_PY="$DOCLENS_PYTHON"

# Un solo plazo para TODO el hook (venv + procesamiento), no uno por paso
DEADLINE=$((SECONDS + ${DOCLENS_HOOK_WAIT:-90}))
STATE="${TMPDIR:-/tmp}/doclens-hook-$(id -u)"; mkdir -p "$STATE" 2>/dev/null || STATE="/tmp"
DOC_KEY=$(printf '%s' "$FILE_PATH" | cksum | cut -d' ' -f1)
INSPECT_DIR=""

# Proceso en su propio grupo: sobrevive a que el hook termine o lo maten
detach() {
  if command -v setsid >/dev/null 2>&1; then setsid "$@"
  elif command -v perl >/dev/null 2>&1; then perl -e 'setpgrp(0,0); exec @ARGV' "$@"
  else nohup "$@"; fi
}
bounded() {  # bounded <out> <pidfile> cmd… → exit del cmd, o 4 si sigue corriendo al vencer DEADLINE
  local out="$1" pidf="$2" pid; shift 2
  detach "$@" > "$out" 2>&1 < /dev/null &
  pid=$!; echo "$pid" > "$pidf"
  while kill -0 "$pid" 2>/dev/null; do
    [ "$SECONDS" -ge "$DEADLINE" ] && return 4
    sleep 0.2 2>/dev/null || sleep 1
  done
  wait "$pid"
}
alive() { local p; p=$(cat "$1" 2>/dev/null) && [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
run_doclens() {  # 0 digest en $INSPECT_DIR · 4 sigue procesando · otro: doclens falló
  local out="$STATE/$DOC_KEY.out" pidf="$STATE/$DOC_KEY.pid" rc=0
  alive "$pidf" && return 4          # ya hay un proceso con este documento: no lanzar otro
  bounded "$out" "$pidf" "$VENV_PY" "$DOCLENS" "$FILE_PATH" || rc=$?
  [ "$rc" = 4 ] && return 4
  INSPECT_DIR=$(grep '^INSPECT_DIR=' "$out" 2>/dev/null | cut -d= -f2-)
  [ -n "$INSPECT_DIR" ]
}
venv_lock_busy() {  # mismo criterio que doclens/sync.sh: PID vivo, o sin PID y < 10 min
  local lock="$ROOT/doclens/.venv.lock" pid
  [ -d "$lock" ] || return 1
  pid=$(cat "$lock/pid" 2>/dev/null || true)
  if [ -n "$pid" ]; then kill -0 "$pid" 2>/dev/null; else [ -z "$(find "$lock" -maxdepth 0 -mmin +10 2>/dev/null)" ]; fi
}
bootstrap() {  # 0 venv listo · 1 no disponible · 4 preparándose
  [ -z "${DOCLENS_PYTHON:-}" ] && [ -n "$SYNC" ] && [ -f "$SYNC" ] || return 1
  venv_lock_busy && return 4
  [ -n "$(find "$ROOT/doclens/.venv.failed" -mmin -60 2>/dev/null)" ] && return 1
  local rc=0
  bounded "$STATE/venv.out" "$STATE/venv.pid" bash "$SYNC" "$ROOT" --venv || rc=$?
  case "$rc" in
    0) [ -x "$VENV_PY" ] ;;
    3|4) return 4 ;;
    *) return 1 ;;
  esac
}
process() {  # 0 digest · 1 doclens no disponible · 4 en curso · 5 doclens falló con ESTE archivo
  local r=0
  if [ -x "$VENV_PY" ] && [ -f "$DOCLENS" ]; then
    run_doclens || r=$?
    [ "$r" = 0 ] && return 0
    [ "$r" = 4 ] && return 4
    # Falló: si el intérprete está sano, el problema es el archivo (no reinstalar)
    "$VENV_PY" -c 'import fitz' >/dev/null 2>&1 && return 5
  fi
  [ -f "$DOCLENS" ] || return 1
  r=0; bootstrap || r=$?            # venv ausente o roto: sync --venv lo crea/repara
  [ "$r" = 0 ] || return "$r"
  r=0; run_doclens || r=$?
  [ "$r" = 0 ] && return 0
  [ "$r" = 4 ] && return 4
  return 5
}
in_progress() {
  echo "doclens está preparando o procesando $FILE_PATH (la primera vez crea doclens/.venv; un documento grande tarda). Sigue en segundo plano: reintenta el Read en un momento." >&2
  exit 2
}

# ── PDF: pre-procesar y redirigir ─────────────────────────────────────────
if [ "$EXT" = ".pdf" ]; then
  PRC=0; process || PRC=$?
  [ "$PRC" = 4 ] && in_progress
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
  if [ "$PRC" = 5 ]; then
    WHY="doclens no pudo procesar este archivo: $(grep -v '^INSPECT_DIR=' "$STATE/$DOC_KEY.out" 2>/dev/null | tail -n 1). ¿PDF dañado o protegido con contraseña?"
  elif [ -n "${DOCLENS_PYTHON:-}" ]; then
    WHY="DOCLENS_PYTHON=$DOCLENS_PYTHON no puede ejecutar doclens (¿faltan dependencias? pip install -r doclens/requirements.txt), o quita la variable."
  elif [ -n "$SYNC" ] && [ -f "$SYNC" ]; then
    WHY="El processor doclens no está disponible. Prepáralo (crea doclens/.venv): bash doclens/sync.sh --force"
  elif [ -f "$DOCLENS" ]; then
    WHY="El processor doclens no está disponible (estructura vieja en scripts/): ejecuta el sync de ai-tooling (doclens/sync.sh <repo>) para migrar a doclens/."
  else
    WHY="Este proyecto no tiene doclens: ejecuta el sync de ai-tooling (doclens/sync.sh <repo>)."
  fi
  cat >&2 <<EOF
BLOCKED: Read directo de PDF ($FILE_PATH).

El backend Kimi de este repo NO acepta bloques PDF: HTTP 400 y sesión muerta.
$WHY

Ruta manual mientras tanto:
1. Visual: sips -s format png -z 1684 1190 "$FILE_PATH" --out /tmp/pdf-page.png
   y lee el PNG (multi-página: pdftoppm -png "$FILE_PATH" /tmp/pdf-page)
2. Texto: pdftotext "$FILE_PATH" -
EOF
  exit 2
fi

# ── Imágenes: solo interceptar si son demasiado grandes ───────────────────
case "$EXT" in
  .png|.jpg|.jpeg|.webp|.tif|.tiff)
    SIZE=$(wc -c < "$FILE_PATH" 2>/dev/null | tr -d ' '); SIZE=${SIZE:-0}   # stat -f%z es solo macOS
    if [ "$SIZE" -gt 8388608 ]; then
      PRC=0; process || PRC=$?
      [ "$PRC" = 4 ] && in_progress
      if [ "$PRC" = 0 ] && [ -f "$INSPECT_DIR/downscaled.png" ]; then
        echo "Imagen de $((SIZE / 1048576)) MB — demasiado grande para el API. Usa la copia downscaled: $INSPECT_DIR/downscaled.png (metadata en $INSPECT_DIR/index.md)" >&2
        exit 2
      fi
    fi
    exit 0
    ;;
esac

exit 0
