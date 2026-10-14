#!/usr/bin/env bash
# task-scope-updater.sh — Detecta cambios de modo mid-session y actualiza task-scope.json
# Dispara en CADA mensaje. Solo actúa si hay un modo restrictivo activo y el prompt
# señala un cambio de intent (analysis → build, build → synthesize, etc.)
# distributable: true
# event: UserPromptSubmit
# matcher: ""
# timeout: 5

INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // "."')
PROMPT=$(printf '%s' "$INPUT" | jq -r '.prompt // empty')
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty')

[ -z "$PROMPT" ] && exit 0

# La lib vive junto a este hook — no depender del cwd de la sesión (puede ser
# un subdirectorio tras un `cd`).
LIB="$(dirname "${BASH_SOURCE[0]}")/task-scope-lib.sh"
[ -f "$LIB" ] || exit 0
source "$LIB"

ROOT=$(project_root "$CWD")
SCOPE_FILE=$(scope_file_for_session "$ROOT" "$SESSION_ID")
[ ! -f "$SCOPE_FILE" ] && exit 0

CURRENT_MODE=$(jq -r '.mode // "full"' "$SCOPE_FILE")
BASE_MODE=$(printf '%s' "$CURRENT_MODE" | cut -d: -f1)
LANG_SUFFIX=$(printf '%s' "$CURRENT_MODE" | cut -d: -f2 -s)

# Sin modo restrictivo activo → no hay nada que actualizar
[ "$BASE_MODE" = "full" ] && exit 0

# ── Detección de señales de cambio de modo ────────────────────────────────────
# Asimetría deliberada (ver task-scope-lib.sh):
#   - Hacia build (menos restrictivo): basta CUALQUIER verbo de construcción.
#     Antes se exigía "ahora implementa" y un "arregla X" dejaba la sesión
#     atascada en analysis, bloqueando el edit.
#   - Hacia modos restrictivos: solo con un marcador explícito de transición
#     ("ahora", "primero", "now", "first"…) y sin verbo de construcción en el
#     prompt — un "revisa que compile y arréglalo" ya no cae en validate.

NEW_BASE=""
INTENT=$(intent_classify "$PROMPT")

if [ "$INTENT" = "build" ]; then
  NEW_BASE="build"

# analysis/build → synthesize
elif printf '%s' "$PROMPT" | grep -qiE \
  "ahora (documenta|escribe (el )?(doc|readme|guía|guia))|ya (analicé|analice|implementé|implemente).*(documenta|escribe)|sintetiza (esto|eso|el análisis|el analisis)|now (document|write (the )?(docs?|readme|guide))"; then
  NEW_BASE="synthesize"

# build/synthesize → analysis
elif printf '%s' "$PROMPT" | grep -qiE \
  "antes de (implementar|continuar).*(analiza|revisa|checa)|primero (analiza|entiende|explora|mapea)|first (analy[sz]e|understand|explore|map)|before (implementing|continuing).*(analy[sz]e|review|check)"; then
  NEW_BASE="analysis"

# cualquier modo → validate
elif printf '%s' "$PROMPT" | grep -qiE \
  "ahora (verifica|valida|corre (los )?tests)|now (verify|validate|run (the )?tests)"; then
  NEW_BASE="validate"
fi

[ -z "$NEW_BASE" ] && exit 0
[ "$NEW_BASE" = "$BASE_MODE" ] && exit 0

# ── Actualizar task-scope.json (sufijo de lenguaje solo aplica a build/analysis) ─
case "$NEW_BASE" in
  build|analysis) NEW_MODE="${NEW_BASE}${LANG_SUFFIX:+:$LANG_SUFFIX}" ;;
  *)              NEW_MODE="$NEW_BASE" ;;
esac
TASK_ID=$(jq -r '.task_id // "unnamed"' "$SCOPE_FILE")

TMP=$(mktemp)
if jq --arg mode "$NEW_MODE" '.mode = $mode' "$SCOPE_FILE" > "$TMP"; then
  mv "$TMP" "$SCOPE_FILE"
  echo "task-scope-updater: modo actualizado $CURRENT_MODE → $NEW_MODE (task: $TASK_ID)"
else
  rm -f "$TMP"
fi

exit 0
