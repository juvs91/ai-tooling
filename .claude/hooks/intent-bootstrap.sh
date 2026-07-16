#!/usr/bin/env bash
# intent-bootstrap.sh — Crea task-scope.json en el primer mensaje de cada sesión
# usando detección de intent en bash puro. No depende del Skill tool ni de
# workflow-coordinator. Funciona en --print, interactivo y VS Code.
# distributable: true
# event: UserPromptSubmit
# matcher: ""
# timeout: 5

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')

[ -z "$SESSION_ID" ] && exit 0
[ -z "$PROMPT" ] && exit 0
[ -f "$CWD/.claude/no-skill-gate" ] && exit 0

SCOPE_FILE="$CWD/.claude/task-scope.json"
[ -f "$SCOPE_FILE" ] && exit 0  # Ya existe — no sobreescribir

SESSION_ID=$(echo "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
BOOTSTRAP_MARKER="$CWD/.claude/sessions/${SESSION_ID}-bootstrap"
mkdir -p "$CWD/.claude/sessions"

# Solo actuar una vez por sesión
[ -f "$BOOTSTRAP_MARKER" ] && exit 0

# ── Detección de intent ────────────────────────────────────────────────────────
DATE=$(date +%Y-%m-%d)
MODE="full"
SLUG="task"

# Detectar lenguaje del proyecto
LANG_SUFFIX=""
[ -f "$CWD/tsconfig.json" ] || [ -f "$CWD/package.json" ] && LANG_SUFFIX=":ts"
[ -f "$CWD/pyproject.toml" ] || [ -f "$CWD/setup.py" ] && LANG_SUFFIX=":py"
[ -f "$CWD/go.mod" ] && LANG_SUFFIX=":go"

# analysis
if echo "$PROMPT" | grep -qiE \
  "analiza|cuántos|cuantos|qué hace|que hace|cómo funciona|como funciona|explica|describe|investiga|mapea|audit|coverage|cobertura|review (el|la|los)|revisa (el|la|los)"; then
  MODE="analysis${LANG_SUFFIX}"
  SLUG="analysis"

# synthesize
elif echo "$PROMPT" | grep -qiE \
  "documenta|escribe (el |la |un |una )?(doc|readme|guía|guia|reporte|informe)|crea (la |una )?(guía|guia|documentación|documentacion)|sintetiza"; then
  MODE="synthesize"
  SLUG="synthesize"

# validate
elif echo "$PROMPT" | grep -qiE \
  "^(verifica|valida|corre|ejecuta) (los |las |los )?tests?|solo (verifica|valida|revisa)|asegúrate de que|asegurate de que"; then
  MODE="validate"
  SLUG="validate"

# build
elif echo "$PROMPT" | grep -qiE \
  "implementa|implement|crea (un |una |el |la )|create|añade|agrega|fix (el|la|los|un)|arregla|build|desarrolla|codifica"; then
  MODE="build${LANG_SUFFIX}"
  SLUG="build"

# full (plan/design — no restricciones)
elif echo "$PROMPT" | grep -qiE \
  "planea|diseña|propón|propón|approach|arquitectura|qué harías|como abordarías"; then
  MODE="full"
  SLUG="plan"
fi

TASK_ID="${SLUG}-${DATE}"
BASE_MODE_CHECK=$(echo "$MODE" | cut -d: -f1)

# ── Completion checklist project-aware (solo en analysis) ─────────────────────
CHECKLIST_JSON="[]"

if [ "$BASE_MODE_CHECK" = "analysis" ]; then
  ITEMS=()

  # TypeScript con hooks/ → fuerza descubrimiento exhaustivo incluyendo components/ui
  if [ "$LANG_SUFFIX" = ":ts" ] && [ -d "$CWD/hooks" ]; then
    ITEMS+=("find hooks lib components/ui -name 'use-*.ts' -o -name 'use-*.tsx' 2>/dev/null | grep -v __tests__ | grep -v node_modules | sort  # todos los hooks (incluyendo duplicados en components/ui)")
    ITEMS+=("find . -path '*/__tests__/use-*.test.t*' 2>/dev/null | grep -v node_modules | sort  # tests de hooks")
  fi

  # Python con tests/ o pytest
  if [ "$LANG_SUFFIX" = ":py" ]; then
    ITEMS+=("find . -name '*.py' | grep -v __pycache__ | grep -v node_modules | grep -v '.venv' | wc -l  # archivos Python total")
    ITEMS+=("find . -name 'test_*.py' -o -name '*_test.py' | grep -v node_modules | wc -l  # tests Python total")
  fi

  # ai-notes/frontend presente → cobertura de docs
  if [ -d "$CWD/ai-notes/frontend" ]; then
    ITEMS+=("find ai-notes/frontend -name '*.md' | wc -l  # docs frontend total (recursivo, incluyendo beta/)")
    ITEMS+=("find ai-notes/frontend -maxdepth 1 -name '*.md' | wc -l  # docs frontend activos (solo raíz)")
  fi

  # ai-notes general → estructura completa
  if [ -d "$CWD/ai-notes" ]; then
    ITEMS+=("find ai-notes -mindepth 1 -maxdepth 2 -type d | sort  # subdirectorios de ai-notes (explorar antes de reportar)")
  fi

  if [ ${#ITEMS[@]} -gt 0 ]; then
    CHECKLIST_JSON=$(printf '%s\n' "${ITEMS[@]}" | jq -R . | jq -s .)
  fi
fi

# ── Escribir task-scope.json ───────────────────────────────────────────────────
# Usamos temp file para el checklist — evita problemas con comillas simples en --argjson
CHECKLIST_TMP=$(mktemp)
printf '%s' "$CHECKLIST_JSON" > "$CHECKLIST_TMP"
jq -n \
  --arg task_id "$TASK_ID" \
  --arg mode "$MODE" \
  --slurpfile checklist "$CHECKLIST_TMP" \
  '{"task_id": $task_id, "mode": $mode, "allowed_patterns": [], "completion_checklist": $checklist[0]}' \
  > "$SCOPE_FILE"
rm -f "$CHECKLIST_TMP"

touch "$BOOTSTRAP_MARKER"

echo "intent-bootstrap: task-scope.json creado → mode=${MODE} (task: ${TASK_ID})"
[ "$CHECKLIST_JSON" != "[]" ] && echo "  checklist: $(printf '%s\n' "${ITEMS[@]}" | wc -l | tr -d ' ') verificaciones project-aware cargadas"

# ── Instrucción de calidad según modo ─────────────────────────────────────────
if [ "$BASE_MODE_CHECK" = "analysis" ]; then
  echo ""
  echo "📊 ANALYSIS MODE activo. Requisitos de calidad OBLIGATORIOS:"
  echo "  • Corre './scripts/task-verify.sh' para ver los checks requeridos ANTES de empezar"
  echo "  • Verifica TODOS los conteos con find/wc -l — NUNCA uses estimaciones (~X)"
  echo "  • Explora TODOS los subdirectorios antes de reportar estructura"
  echo "  • Por cada ítem: incluye ubicación exacta (path:línea), líneas de código, y estado"
  echo "  • Al final: lista explícitamente qué NO revisaste y por qué"
  echo "  • Escribe en ai-notes/findings/ únicamente (scope-gate bloqueará cualquier otro path)"
fi

exit 0
