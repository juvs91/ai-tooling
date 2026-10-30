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

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

[ "$TOOL" != "Read" ] && exit 0
[ -z "$FILE_PATH" ] && exit 0

# Los artifacts generados por doclens se leen libremente (evita loops)
case "$FILE_PATH" in
  *.inspect/*) exit 0 ;;
esac

EXT=$(echo "$FILE_PATH" | grep -oiE '\.[a-z0-9]+$' | tr '[:upper:]' '[:lower:]')
# Raíz del proyecto, no el cwd: el cwd del hook sigue al `cd` del Bash tool y a los
# worktrees (ADR-0073), donde el venv (gitignorado) no existe. CLAUDE_PROJECT_DIR
# siempre apunta a la raíz principal.
ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
DOCLENS="$ROOT/doclens/doclens.py"; VENV_PY="$ROOT/doclens/.venv/bin/python"; SYNC="$ROOT/doclens/sync.sh"
# Transición: consumer aún con la estructura vieja (sync_doclens:false o sin migrar)
if [ ! -f "$DOCLENS" ] && [ -f "$ROOT/scripts/doclens.py" ]; then
  DOCLENS="$ROOT/scripts/doclens.py"; VENV_PY="$ROOT/scripts/.venv-doc-tools/bin/python"; SYNC=""
fi
[ -n "${DOCLENS_PYTHON:-}" ] && [ -x "$DOCLENS_PYTHON" ] && VENV_PY="$DOCLENS_PYTHON"

INSPECT_DIR=""
run_doclens() {
  local out; out=$("$VENV_PY" "$DOCLENS" "$FILE_PATH" 2>/dev/null)
  INSPECT_DIR=$(echo "$out" | grep '^INSPECT_DIR=' | cut -d= -f2-)
}
bootstrap() {  # 0 venv listo · 1 no disponible · 3 otro proceso lo está preparando
  [ -z "${DOCLENS_PYTHON:-}" ] && [ -n "$SYNC" ] && [ -f "$SYNC" ] || return 1
  local lock="$ROOT/doclens/.venv.lock" rc=0
  [ -d "$lock" ] && [ -z "$(find "$lock" -maxdepth 0 -mmin +10 2>/dev/null)" ] && return 3
  [ -n "$(find "$ROOT/doclens/.venv.failed" -mmin -60 2>/dev/null)" ] && return 1
  bash "$SYNC" "$ROOT" --venv >/dev/null 2>&1 || rc=$?
  [ "$rc" = 3 ] && return 3
  [ "$rc" = 0 ] && [ -x "$VENV_PY" ]
}
process() {  # 0 digest en $INSPECT_DIR · 1 no disponible · 3 preparándose
  if [ -x "$VENV_PY" ] && [ -f "$DOCLENS" ]; then
    run_doclens; [ -n "$INSPECT_DIR" ] && return 0
  fi
  local b=0; bootstrap || b=$?   # venv ausente o roto: sync --venv lo crea/repara
  [ "$b" = 0 ] || return "$b"
  run_doclens; [ -n "$INSPECT_DIR" ]
}
preparing() {
  echo "doclens se está preparando (primera vez, ~30 s: crea doclens/.venv). Reintenta el Read de $FILE_PATH en unos segundos." >&2
  exit 2
}

# ── PDF: pre-procesar y redirigir ─────────────────────────────────────────
if [ "$EXT" = ".pdf" ]; then
  PRC=0; process || PRC=$?
  [ "$PRC" = 3 ] && preparing
  if [ "$PRC" = 0 ]; then
    if [ -f "$INSPECT_DIR/index.md" ]; then
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
  fi

  # Fallback manual (sin red para el venv, o el processor falló)
  if [ -n "$SYNC" ] && [ -f "$SYNC" ]; then
    SETUP_MSG="Prepáralo (crea doclens/.venv): bash doclens/sync.sh --force"
  else
    SETUP_MSG="Estructura vieja en scripts/: ejecuta el sync de ai-tooling (doclens/sync.sh <repo>) para migrar a doclens/."
  fi
  cat >&2 <<EOF
BLOCKED: Read directo de PDF ($FILE_PATH).

El backend Kimi de este repo NO acepta bloques PDF: HTTP 400 y sesión muerta.
El processor doclens no está disponible. $SETUP_MSG

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
      [ "$PRC" = 3 ] && preparing
      if [ "$PRC" = 0 ] && [ -f "$INSPECT_DIR/downscaled.png" ]; then
        echo "Imagen de $((SIZE / 1048576)) MB — demasiado grande para el API. Usa la copia downscaled: $INSPECT_DIR/downscaled.png (metadata en $INSPECT_DIR/index.md)" >&2
        exit 2
      fi
    fi
    exit 0
    ;;
esac

exit 0
