#!/bin/bash
# distributable: true
# task-verify.sh — portable macOS/Linux
# Corre el completion_checklist de .claude/task-scope.json y reporta pass/fail.
# Usage: ./scripts/task-verify.sh
# Exit 0: task completa. Exit 1: ítems pendientes.

if [ -n "$TASK_VERIFY_RUNNING" ]; then
  echo "task-verify.sh: invocación recursiva detectada (el propio completion_checklist" >&2
  echo "invoca a task-verify.sh) — abortando para evitar loop infinito." >&2
  echo "Quita esa línea del completion_checklist; task-verify.sh no debe verificarse a sí mismo." >&2
  exit 1
fi
export TASK_VERIFY_RUNNING=1

# Correr desde la raíz del repo para que los comandos relativos del
# completion_checklist funcionen desde cualquier cwd. Raíz por git y no por
# dirname "$0": scripts/ suele estar symlinkeado en ~/.local/bin.
ORIG_CWD="$PWD"
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 1

# ADR-0073: desde Bash no existe $CLAUDE_PROJECT_DIR — el scope se ubica por
# session id (scope_locate) partiendo del cwd ORIGINAL, así se encuentra aunque
# estemos en un worktree o la sesión sea de un subproyecto.
LIB=".claude/hooks/task-scope-lib.sh"
HAS_LOCATE=0
if [ -f "$LIB" ] && . "$LIB" && declare -F scope_locate >/dev/null; then
  HAS_LOCATE=1
  SCOPE_FILE=$(scope_locate "$ORIG_CWD" "${CLAUDE_CODE_SESSION_ID:-}")
elif declare -F scope_file_for_session >/dev/null; then
  SCOPE_FILE=$(scope_file_for_session "." "${CLAUDE_CODE_SESSION_ID:-}")
else
  SCOPE_FILE=".claude/task-scope.json"
fi
[ ! -f "$SCOPE_FILE" ] && { echo "No $SCOPE_FILE found. Nothing to verify."; exit 0; }

MODE=$(jq -r '.mode // "full"' "$SCOPE_FILE")
LANG_SUFFIX=$(echo "$MODE" | cut -d: -f2 -s)
BASE_MODE=$(echo "$MODE" | cut -d: -f1)
TASK_ID=$(jq -r '.task_id // "unnamed"' "$SCOPE_FILE")
CHECKLIST=$(jq -r '.completion_checklist[]? // empty' "$SCOPE_FILE")

echo "=== Task Verify: $TASK_ID (mode: $MODE) ==="
echo ""

PASS=0
FAIL=0
INCOMPLETE=()

while IFS= read -r check; do
  [ -z "$check" ] && continue
  # Solo recortar espacios — `xargs` quitaba comillas y backslashes y rompía
  # cualquier ítem con \( … \) o patrones '*.ext' entre comillas.
  CMD=$(printf '%s' "$check" | sed -e 's/[[:space:]]*#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  LABEL=$(echo "$check" | grep -o '#.*' | sed 's/^# *//' || true)
  [ -z "$LABEL" ] && LABEL="$CMD"

  OUTPUT=$(eval "$CMD" 2>&1)
  STATUS=$?

  if [ $STATUS -eq 0 ] && [ -n "$OUTPUT" ]; then
    echo "✅ $LABEL"
    echo "   → $OUTPUT"
    PASS=$((PASS + 1))
  else
    echo "❌ $LABEL"
    echo "   CMD: $CMD"
    [ -n "$OUTPUT" ] && echo "   → $OUTPUT"
    FAIL=$((FAIL + 1))
    INCOMPLETE+=("$LABEL")
  fi
done <<< "$CHECKLIST"

echo ""
echo "--- $PASS passed, $FAIL failed ---"

if [ $FAIL -eq 0 ]; then
  echo ""
  echo "✅ Task appears COMPLETE."
  case "$BASE_MODE" in
    build|validate)
      case "$LANG_SUFFIX" in
        ts)   echo "→ Run: npx tsc --noEmit && pnpm test:run && pnpm test:e2e" ;;
        py)   echo "→ Run: ruff check . && pytest" ;;
        go)   echo "→ Run: go vet ./... && go test ./..." ;;
        rs)   echo "→ Run: cargo check && cargo test" ;;
        java) echo "→ Run: mvn test  (or: ./gradlew test)" ;;
        sql)  echo "→ Run migrations against a test DB and verify with your SQL linter" ;;
        *)    echo "→ Run your project's test suite before merging" ;;
      esac
      ;;
    analysis|synthesize)
      echo "→ No code changed. No tests required."
      ;;
  esac
  # ADR-0073: un subagente con isolation:'worktree' comparte el session id del
  # padre — borrar aquí cerraría el scope del padre a mitad de su tarea. Solo
  # se borra si este toplevel es el del hilo principal (registro main-top que
  # escribe track-skill-load.sh); sin registro, solo fuera de un worktree enlazado.
  CAN_DELETE=1
  if [ "$HAS_LOCATE" = "1" ]; then
    SID=$(printf '%s' "${CLAUDE_CODE_SESSION_ID:-}" | tr -cd 'a-zA-Z0-9_-')
    MAIN_TOP_FILE="$(dirname "$SCOPE_FILE")/${SID}-main-top"
    HERE=$(canon_path "$PWD")
    if [ -n "$SID" ] && [ -f "$MAIN_TOP_FILE" ]; then
      MAIN_TOP=$(canon_path "$(head -1 "$MAIN_TOP_FILE")")
      [ "$HERE" = "$MAIN_TOP" ] || CAN_DELETE=0
    elif is_linked_worktree "$HERE"; then
      CAN_DELETE=0
    fi
  fi
  if [ "$CAN_DELETE" = "1" ]; then
    rm -f "$SCOPE_FILE"
    echo "→ $SCOPE_FILE borrado — scope abierto para la siguiente tarea."
  else
    echo "→ $SCOPE_FILE conservado: este no es el worktree del hilo principal (subagente aislado)."
  fi

  # ADR-0074: aviso (informativo) si el ADR gate git falta o requiere migración.
  # Toolbox resuelto inline (no se puede fuentear el doctor); solo instaladores
  # que declaren `# supports: --check` — uno viejo instalaría en vez de chequear.
  TB="${AI_TOOLING_HOME:-}"
  if [ -z "$TB" ] && [ -f .ai-tooling ] && command -v jq >/dev/null 2>&1; then
    TB=$(jq -r '.local_path // empty' .ai-tooling 2>/dev/null)
    TB="${TB/#\~/$HOME}"; TB="${TB//\$HOME/$HOME}"
  fi
  [ -n "$TB" ] && [ ! -d "$TB" ] && TB=""
  GH_INSTALLER=""
  for cand in "${TB:+$TB/tools/install_hooks.sh}" "$PWD/tools/install_hooks.sh"; do
    [ -n "$cand" ] && [ -f "$cand" ] && grep -qxF '# supports: --check' "$cand" && { GH_INSTALLER="$cand"; break; }
  done
  if [ -n "$GH_INSTALLER" ]; then
    GH_OUT=$(bash "$GH_INSTALLER" --check 2>&1); GH_RC=$?
    case "$GH_RC" in 11|12) echo "⚠️  ADR gate git: ${GH_OUT#* }" ;; esac
  fi
  exit 0
else
  echo ""
  echo "⚠️  Task INCOMPLETE. Pending items:"
  for item in "${INCOMPLETE[@]}"; do echo "   - $item"; done
  exit 1
fi
