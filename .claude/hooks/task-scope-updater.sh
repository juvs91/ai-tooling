#!/usr/bin/env bash
# task-scope-updater.sh — Detecta cambios de modo mid-session y actualiza task-scope.json
# Dispara en CADA mensaje. Solo actúa si hay un modo restrictivo activo y el prompt
# señala claramente un cambio de intent (analysis → build, build → synthesize, etc.)
# distributable: true
# event: UserPromptSubmit
# matcher: ""
# timeout: 5

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')
SCOPE_FILE="$CWD/.claude/task-scope.json"

[ -z "$PROMPT" ] && exit 0
[ ! -f "$SCOPE_FILE" ] && exit 0

CURRENT_MODE=$(jq -r '.mode // "full"' "$SCOPE_FILE")
BASE_MODE=$(echo "$CURRENT_MODE" | cut -d: -f1)
LANG_SUFFIX=$(echo "$CURRENT_MODE" | cut -d: -f2 -s)

# Sin modo restrictivo activo → no hay nada que actualizar
[ "$BASE_MODE" = "full" ] && exit 0

# ── Detección de señales de cambio de modo ────────────────────────────────────
# Keywords conservadores: requieren señal explícita de cambio, no solo presencia
# de palabras de acción. Evita false positives en preguntas normales.

NEW_BASE=""

# analysis/validate → build
if echo "$PROMPT" | grep -qiE \
  "ahora (implementa|hazlo|codifica|crea|arréglalo|build|desarrolla)|ok (implementa|hazlo|procede|arréglalo)|(ya analicé|terminé el análisis).*(ahora|procede)|(implementa|codifica|construye) (esto|eso|lo que)"; then
  NEW_BASE="build"

# analysis/build → synthesize
elif echo "$PROMPT" | grep -qiE \
  "ahora (documenta|escribe (el )?(doc|readme|guía))|ya (analicé|implementé).*(documenta|escribe)|sintetiza (esto|eso|el análisis)"; then
  NEW_BASE="synthesize"

# build/synthesize → analysis
elif echo "$PROMPT" | grep -qiE \
  "antes de (implementar|continuar).*(analiza|revisa|checa)|primero (analiza|entiende|explora|mapea)"; then
  NEW_BASE="analysis"

# cualquier modo → validate
elif echo "$PROMPT" | grep -qiE \
  "ahora (verifica|valida|corre (los )?tests|revisa (que|si))|(solo |solamente )?(verifica|valida|revisa) (que|si|los)"; then
  NEW_BASE="validate"
fi

[ -z "$NEW_BASE" ] && exit 0
[ "$NEW_BASE" = "$BASE_MODE" ] && exit 0

# ── Actualizar task-scope.json (sufijo de lenguaje solo aplica a build) ───────
if [ "$NEW_BASE" = "build" ]; then
  NEW_MODE="${NEW_BASE}${LANG_SUFFIX:+:$LANG_SUFFIX}"
else
  NEW_MODE="$NEW_BASE"
fi
TASK_ID=$(jq -r '.task_id // "unnamed"' "$SCOPE_FILE")

TMP=$(mktemp)
if jq --arg mode "$NEW_MODE" '.mode = $mode' "$SCOPE_FILE" > "$TMP"; then
  mv "$TMP" "$SCOPE_FILE"
  echo "task-scope-updater: modo actualizado $CURRENT_MODE → $NEW_MODE (task: $TASK_ID)"
else
  rm -f "$TMP"
fi

exit 0
