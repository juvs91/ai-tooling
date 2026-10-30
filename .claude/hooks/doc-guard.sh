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
# - .pdf  -> corre scripts/doclens.py, que genera <archivo>.inspect/ con
#   index.md (metadata + texto + topología con coordenadas + tablas en
#   markdown + OCR spa si el PDF es scan), pages/page-N.png (renders) y
#   figures/ (imágenes embebidas + su texto vía OCR). Bloquea el Read
#   original y redirige al agente al index.md.
# - imagen > 8 MB -> genera copia downscaled y redirige a ella.
# - imagen normal -> permite (el modelo de visión la lee directo).
#
# Setup del processor (si falta el venv, el hook cae a modo manual):
#   /usr/bin/python3 -m venv scripts/.venv-doc-tools
#   scripts/.venv-doc-tools/bin/pip install -r scripts/requirements-doc-tools.txt
#   brew install poppler tesseract tesseract-lang   # OCR multi-página (opcional)

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
VENV_PY="scripts/.venv-doc-tools/bin/python"

# ── PDF: pre-procesar y redirigir ─────────────────────────────────────────
if [ "$EXT" = ".pdf" ]; then
  if [ -x "$VENV_PY" ] && [ -f "scripts/doclens.py" ]; then
    OUT=$("$VENV_PY" scripts/doclens.py "$FILE_PATH" 2>/dev/null)
    INSPECT_DIR=$(echo "$OUT" | grep '^INSPECT_DIR=' | cut -d= -f2-)
    if [ -n "$INSPECT_DIR" ] && [ -f "$INSPECT_DIR/index.md" ]; then
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

  # Fallback manual (venv no instalado o processor falló)
  cat >&2 <<EOF
BLOCKED: Read directo de PDF ($FILE_PATH).

El backend Kimi de este repo NO acepta bloques PDF: HTTP 400 y sesión muerta.
El processor doclens no está disponible. Instálalo:
  /usr/bin/python3 -m venv scripts/.venv-doc-tools
  scripts/.venv-doc-tools/bin/pip install -r scripts/requirements-doc-tools.txt

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
    SIZE=$(stat -f%z "$FILE_PATH" 2>/dev/null || echo 0)
    if [ "$SIZE" -gt 8388608 ] && [ -x "$VENV_PY" ]; then
      OUT=$("$VENV_PY" scripts/doclens.py "$FILE_PATH" 2>/dev/null)
      INSPECT_DIR=$(echo "$OUT" | grep '^INSPECT_DIR=' | cut -d= -f2-)
      if [ -f "$INSPECT_DIR/downscaled.png" ]; then
        echo "Imagen de $((SIZE / 1048576)) MB — demasiado grande para el API. Usa la copia downscaled: $INSPECT_DIR/downscaled.png (metadata en $INSPECT_DIR/index.md)" >&2
        exit 2
      fi
    fi
    exit 0
    ;;
esac

exit 0
