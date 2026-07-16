#!/usr/bin/env bash
# intent-bootstrap.sh — Creates task-scope.json on the first prompt of each session.
# Pure bash: no Skill tool, no workflow-coordinator dependency.
# Works in --print, interactive, and VS Code modes.
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
[ -f "$SCOPE_FILE" ] && exit 0

SESSION_ID=$(echo "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
BOOTSTRAP_MARKER="$CWD/.claude/sessions/${SESSION_ID}-bootstrap"
mkdir -p "$CWD/.claude/sessions"
[ -f "$BOOTSTRAP_MARKER" ] && exit 0

# ── Language detection ────────────────────────────────────────────────────────
DATE=$(date +%Y-%m-%d)
MODE="full"
SLUG="task"
LANG_SUFFIX=""

if [ -f "$CWD/tsconfig.json" ] || [ -f "$CWD/package.json" ]; then LANG_SUFFIX=":ts"; fi
if [ -f "$CWD/pyproject.toml" ] || [ -f "$CWD/setup.py" ]; then LANG_SUFFIX=":py"; fi
if [ -f "$CWD/go.mod" ]; then LANG_SUFFIX=":go"; fi
if [ -f "$CWD/Cargo.toml" ]; then LANG_SUFFIX=":rs"; fi
if [ -f "$CWD/pom.xml" ] || [ -f "$CWD/build.gradle" ] || [ -f "$CWD/build.gradle.kts" ]; then LANG_SUFFIX=":java"; fi
# :sql — primarily SQL repos (migrations/, sql/, queries/) with no other lang marker
if [ -z "$LANG_SUFFIX" ]; then
  if [ -d "$CWD/migrations" ] || [ -d "$CWD/sql" ] || [ -d "$CWD/queries" ]; then LANG_SUFFIX=":sql"; fi
fi

# ── Intent detection ──────────────────────────────────────────────────────────
if echo "$PROMPT" | grep -qiE \
  "analiza|cuántos|cuantos|qué hace|que hace|cómo funciona|como funciona|explica|describe|investiga|mapea|audit|coverage|cobertura|review (el|la|los)|revisa (el|la|los)"; then
  MODE="analysis${LANG_SUFFIX}"; SLUG="analysis"
elif echo "$PROMPT" | grep -qiE \
  "documenta|escribe (el |la |un |una )?(doc|readme|guía|guia|reporte|informe)|crea (la |una )?(guía|guia|documentación|documentacion)|sintetiza"; then
  MODE="synthesize"; SLUG="synthesize"
elif echo "$PROMPT" | grep -qiE \
  "^(verifica|valida|corre|ejecuta) (los |las |los )?tests?|solo (verifica|valida|revisa)|asegúrate de que|asegurate de que"; then
  MODE="validate"; SLUG="validate"
elif echo "$PROMPT" | grep -qiE \
  "implementa|implement|crea (un |una |el |la )|create|añade|agrega|fix (el|la|los|un)|arregla|build|desarrolla|codifica"; then
  MODE="build${LANG_SUFFIX}"; SLUG="build"
elif echo "$PROMPT" | grep -qiE \
  "planea|diseña|propón|approach|arquitectura|qué harías|como abordarías"; then
  MODE="full"; SLUG="plan"
fi

TASK_ID="${SLUG}-${DATE}"
BASE_MODE=$(echo "$MODE" | cut -d: -f1)

# ── Generic project structure discovery ──────────────────────────────────────
# Detect which docs directories the project actually uses — none assumed upfront.
DOCS_DIRS=()
for d in ai-notes docs notes documentation wiki; do
  [ -d "$CWD/$d" ] && DOCS_DIRS+=("$d")
done

# ── analysis_write_paths — passed to scope-gate.sh (analysis mode) ───────────
# Always includes .claude/plans. Adds findings/ and analysis/ under each docs dir.
WRITE_PATHS=(".claude/plans")
for d in "${DOCS_DIRS[@]}"; do
  WRITE_PATHS+=("${d}/findings")
  WRITE_PATHS+=("${d}/analysis")
done

# ── completion_checklist (analysis mode only) ─────────────────────────────────
# Items are convention-free: structure + file counts only.
# No naming patterns (no 'use-*.ts', no 'test_*.py') — agent discovers conventions
# from the structure and decides what to search for based on the actual task.
ITEMS=()

if [ "$BASE_MODE" = "analysis" ]; then

  # Universal item 1: directory structure — agent orients from this
  ITEMS+=("find . -mindepth 1 -maxdepth 3 -type d | grep -v node_modules | grep -v .git | grep -v __pycache__ | grep -v .venv | grep -v vendor | grep -v target | sort  # project structure — explore ALL dirs before reporting")

  # Universal item 2 (language-specific): full file listing — agent sees every file,
  # decides what is relevant to the task without naming-convention assumptions.
  # Listing (sort) instead of counting (wc -l): agent needs to SEE filenames, not just totals.
  if [ "$LANG_SUFFIX" = ":ts" ]; then
    ITEMS+=("find . \( -name '*.ts' -o -name '*.tsx' \) | grep -v node_modules | sort  # all TypeScript files :ts")
  fi
  if [ "$LANG_SUFFIX" = ":py" ]; then
    ITEMS+=("find . -name '*.py' | grep -v __pycache__ | grep -v .venv | sort  # all Python files :py")
  fi
  if [ "$LANG_SUFFIX" = ":go" ]; then
    ITEMS+=("find . -name '*.go' | grep -v vendor | sort  # all Go files :go")
  fi
  if [ "$LANG_SUFFIX" = ":rs" ]; then
    ITEMS+=("find . -name '*.rs' | grep -v target | sort  # all Rust files :rs")
  fi
  if [ "$LANG_SUFFIX" = ":java" ]; then
    ITEMS+=("find . -name '*.java' | grep -v target | sort  # all Java files :java")
  fi
  if [ "$LANG_SUFFIX" = ":sql" ]; then
    ITEMS+=("find . -name '*.sql' | grep -v node_modules | sort  # all SQL files :sql")
  fi

  # Generic: docs structure for each detected docs directory
  for d in "${DOCS_DIRS[@]}"; do
    ITEMS+=("find ${d} -mindepth 1 -maxdepth 2 -type d | sort  # subdirs in ${d}/ — explore before reporting")
    ITEMS+=("find ${d} -name '*.md' | wc -l  # markdown files in ${d}/")
  done

fi

# ── Write task-scope.json via temp files ──────────────────────────────────────
# Temp files avoid jq --argjson shell-quoting issues with single quotes inside strings
CHECKLIST_TMP=$(mktemp)
PATHS_TMP=$(mktemp)
DOCS_TMP=$(mktemp)

if [ ${#ITEMS[@]} -gt 0 ]; then
  printf '%s\n' "${ITEMS[@]}" | jq -R . | jq -s . > "$CHECKLIST_TMP"
else
  printf '[]' > "$CHECKLIST_TMP"
fi

if [ ${#WRITE_PATHS[@]} -gt 0 ]; then
  printf '%s\n' "${WRITE_PATHS[@]}" | jq -R . | jq -s . > "$PATHS_TMP"
else
  printf '[".claude/plans"]' > "$PATHS_TMP"
fi

# docs_dirs: base directories only (consumed by scope-gate synthesize mode)
if [ ${#DOCS_DIRS[@]} -gt 0 ]; then
  printf '%s\n' "${DOCS_DIRS[@]}" | jq -R . | jq -s . > "$DOCS_TMP"
else
  printf '[]' > "$DOCS_TMP"
fi

jq -n \
  --arg task_id "$TASK_ID" \
  --arg mode "$MODE" \
  --slurpfile checklist "$CHECKLIST_TMP" \
  --slurpfile write_paths "$PATHS_TMP" \
  --slurpfile docs_dirs "$DOCS_TMP" \
  '{
    "task_id":              $task_id,
    "mode":                 $mode,
    "allowed_patterns":     [],
    "docs_dirs":            $docs_dirs[0],
    "analysis_write_paths": $write_paths[0],
    "completion_checklist": $checklist[0]
  }' > "$SCOPE_FILE"

rm -f "$CHECKLIST_TMP" "$PATHS_TMP" "$DOCS_TMP"
touch "$BOOTSTRAP_MARKER"

echo "intent-bootstrap: task-scope.json → mode=${MODE} task=${TASK_ID}"
[ ${#ITEMS[@]} -gt 0 ] && echo "  checklist: ${#ITEMS[@]} checks"
[ ${#WRITE_PATHS[@]} -gt 1 ] && echo "  analysis_write_paths: ${WRITE_PATHS[*]}"

# ── Quality instructions for analysis mode ────────────────────────────────────
if [ "$BASE_MODE" = "analysis" ]; then
  ALLOWED_PATHS="${WRITE_PATHS[*]}"
  echo ""
  echo "ANALYSIS MODE active. Mandatory quality requirements:"
  echo "  • Run './scripts/task-verify.sh' BEFORE starting to see required checks"
  echo "  • Count exactly with find/wc -l — NEVER use estimates (~X)"
  echo "  • Explore ALL subdirectories before reporting structure"
  echo "  • Per item: exact path, line count, status"
  echo "  • At end: list explicitly what was NOT reviewed and why"
  echo "  • Writes allowed only in: ${ALLOWED_PATHS}"
fi

exit 0
