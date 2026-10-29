#!/usr/bin/env bash
# intent-bootstrap.sh — Creates task-scope.json on the first prompt of each session.
# Pure bash: no Skill tool, no workflow-coordinator dependency.
# Works in --print, interactive, and VS Code modes.
# distributable: true
# event: UserPromptSubmit
# matcher: ""
# timeout: 5

INPUT=$(cat)
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty')
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // "."')
PROMPT=$(printf '%s' "$INPUT" | jq -r '.prompt // empty')

[ -z "$SESSION_ID" ] && exit 0
[ -z "$PROMPT" ] && exit 0

# The lib lives next to this hook — resolve it from here, not from the session
# cwd, which may be a subdirectory after a `cd` in Bash.
LIB="$(dirname "${BASH_SOURCE[0]}")/task-scope-lib.sh"
[ -f "$LIB" ] || exit 0
source "$LIB"

ROOT=$(project_root "$CWD")
[ -f "$ROOT/.claude/no-skill-gate" ] && exit 0

SCOPE_FILE=$(scope_file_for_session "$ROOT" "$SESSION_ID")
[ -f "$SCOPE_FILE" ] && exit 0

SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
BOOTSTRAP_MARKER="$ROOT/.claude/sessions/${SESSION_ID}-bootstrap"
mkdir -p "$ROOT/.claude/sessions"
[ -f "$BOOTSTRAP_MARKER" ] && exit 0

# ── Language detection ────────────────────────────────────────────────────────
# Root markers first; in a monorepo with no root manifest, look up to depth 3.
# More than one language detected → no suffix (task-verify picks no test runner).
_lang_markers() {
  local dir="$1" depth="$2"
  find "$dir" -maxdepth "$depth" \
    \( -name node_modules -o -name .git -o -name .venv -o -name vendor -o -name target -o -path "$dir/.claude/worktrees" \) -prune -o \
    -type f \( -name tsconfig.json -o -name package.json -o -name pyproject.toml -o -name setup.py \
               -o -name go.mod -o -name Cargo.toml -o -name pom.xml -o -name build.gradle -o -name build.gradle.kts \) \
    -print 2>/dev/null \
  | while IFS= read -r f; do
      case "$(basename "$f")" in
        tsconfig.json|package.json) echo ts ;;
        pyproject.toml|setup.py)    echo py ;;
        go.mod)                     echo go ;;
        Cargo.toml)                 echo rs ;;
        pom.xml|build.gradle*)      echo java ;;
      esac
    done | sort -u
}

LANGS=$(_lang_markers "$ROOT" 1)
[ -z "$LANGS" ] && LANGS=$(_lang_markers "$ROOT" 3)
LANG_SUFFIX=""
if [ -n "$LANGS" ] && [ "$(printf '%s\n' "$LANGS" | wc -l | tr -d ' ')" = "1" ]; then
  LANG_SUFFIX=":$LANGS"
fi
# :sql — primarily SQL repos (migrations/, sql/, queries/) with no other lang marker
if [ -z "$LANGS" ]; then
  if [ -d "$ROOT/migrations" ] || [ -d "$ROOT/sql" ] || [ -d "$ROOT/queries" ]; then LANG_SUFFIX=":sql"; fi
fi

# ── Intent detection (shared with task-scope-updater via task-scope-lib) ──────
DATE=$(date +%Y-%m-%d)
INTENT=$(intent_classify "$PROMPT")
case "$INTENT" in
  analysis)   MODE="analysis${LANG_SUFFIX}"; SLUG="analysis" ;;
  synthesize) MODE="synthesize";             SLUG="synthesize" ;;
  validate)   MODE="validate";               SLUG="validate" ;;
  build)      MODE="build${LANG_SUFFIX}";    SLUG="build" ;;
  plan)       MODE="full";                   SLUG="plan" ;;
  *)          MODE="full";                   SLUG="task" ;;
esac

TASK_ID="${SLUG}-${DATE}"
BASE_MODE=$(printf '%s' "$MODE" | cut -d: -f1)

# ── Generic project structure discovery ──────────────────────────────────────
# Detect which docs directories the project actually uses — none assumed upfront.
DOCS_DIRS=()
for d in ai-notes docs notes documentation wiki; do
  [ -d "$ROOT/$d" ] && DOCS_DIRS+=("$d")
done

# ── analysis_write_paths — passed to scope-gate.sh (analysis mode) ───────────
# Always includes .claude/plans (legacy). Adds plans/, findings/ and analysis/ under each docs dir.
WRITE_PATHS=(".claude/plans")
for d in "${DOCS_DIRS[@]}"; do
  WRITE_PATHS+=("${d}/plans")
  WRITE_PATHS+=("${d}/findings")
  WRITE_PATHS+=("${d}/analysis")
done

# ── completion_checklist (analysis mode only) ─────────────────────────────────
# Items are convention-free: structure + file listings only. Commands run from
# the project root (task-verify.sh). Excluded dirs are pruned by name — a
# `grep -v .git` would also drop any path containing e.g. "digital".
PRUNE="\( -name node_modules -o -name .git -o -name __pycache__ -o -name .venv -o -name vendor -o -name target -o -path ./.claude/worktrees \) -prune -o"  # worktrees: ADR-0073
ITEMS=()

if [ "$BASE_MODE" = "analysis" ]; then

  # Universal item 1: directory structure — agent orients from this
  ITEMS+=("find . -mindepth 1 -maxdepth 3 $PRUNE -type d -print | sort  # project structure — explore ALL dirs before reporting")

  # Universal item 2 (language-specific): full file listing — agent sees every file,
  # decides what is relevant to the task without naming-convention assumptions.
  case "$LANG_SUFFIX" in
    :ts)   ITEMS+=("find . $PRUNE -type f \( -name '*.ts' -o -name '*.tsx' \) -print | sort  # all TypeScript files :ts") ;;
    :py)   ITEMS+=("find . $PRUNE -type f -name '*.py' -print | sort  # all Python files :py") ;;
    :go)   ITEMS+=("find . $PRUNE -type f -name '*.go' -print | sort  # all Go files :go") ;;
    :rs)   ITEMS+=("find . $PRUNE -type f -name '*.rs' -print | sort  # all Rust files :rs") ;;
    :java) ITEMS+=("find . $PRUNE -type f -name '*.java' -print | sort  # all Java files :java") ;;
    :sql)  ITEMS+=("find . $PRUNE -type f -name '*.sql' -print | sort  # all SQL files :sql") ;;
  esac

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
OUT_TMP=$(mktemp)
trap 'rm -f "$CHECKLIST_TMP" "$PATHS_TMP" "$DOCS_TMP" "$OUT_TMP"' EXIT

if [ ${#ITEMS[@]} -gt 0 ]; then
  printf '%s\n' "${ITEMS[@]}" | jq -R . | jq -s . > "$CHECKLIST_TMP"
else
  printf '[]' > "$CHECKLIST_TMP"
fi

printf '%s\n' "${WRITE_PATHS[@]}" | jq -R . | jq -s . > "$PATHS_TMP"

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
  }' > "$OUT_TMP" || exit 0
mv "$OUT_TMP" "$SCOPE_FILE"
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
  echo "  • Prompt asks to change code? Say so — the next prompt with a build verb switches to build mode."
fi

exit 0
