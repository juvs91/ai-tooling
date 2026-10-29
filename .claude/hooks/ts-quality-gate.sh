#!/bin/bash
# distributable: true
# event: PostToolUse
# matcher: Edit|Write
# timeout: 30
# async: true
# ts-quality-gate.sh — PostToolUse hook (Edit|Write on *.ts/*.tsx)
# Corre tsc, guarda error count en quality-state/, appendea audit log.
# Patrón two-hook: este guarda estado → quality-enforce.sh (PreToolUse) bloquea el siguiente edit.

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
case "$FILE" in *.ts|*.tsx) ;; *) exit 0 ;; esac

CWD=$(echo "$INPUT" | jq -r '.cwd // "."')
case "$FILE" in /*) ;; *) FILE="$CWD/$FILE" ;; esac

# ADR-0073: tres raíces distintas, cada una para lo suyo:
#   - estado/audit → raíz canónica de la sesión (state_root), compartida con
#     quality-enforce.sh; clave = archivo relativo a ella (absoluto si cae fuera)
#   - tsc → tsconfig.json más cercano al ARCHIVO (no al cwd), tope = su repo
#   - binario → tsc local del proyecto o del PATH; nunca npx (sin typescript
#     instalado, npx resuelve otro paquete y reporta 0 errores)
LIB="$(dirname "${BASH_SOURCE[0]}")/task-scope-lib.sh"
ROOT=""; FTOP=""
if [ -f "$LIB" ] && . "$LIB" && declare -F state_root >/dev/null; then
  ROOT=$(state_root "$CWD")
  repo_of_file "$FILE" "$CWD"
  [ -n "$RF_FILE" ] && FILE="$RF_FILE"
  FTOP="$RF_TOP"
fi
ROOT=${ROOT:-$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)}
ROOT=${ROOT:-$CWD}

# Sube desde $1 buscando $2 (test -e); tope en el toplevel del repo del archivo
_find_up() {
  local d="$1"
  while :; do
    [ -e "$d/$2" ] && { printf '%s' "$d"; return 0; }
    { [ -n "$FTOP" ] && [ "$d" = "$FTOP" ]; } && return 0
    [ "$d" = "/" ] && return 0
    d=$(dirname -- "$d")
  done
}
TSC_DIR=$(_find_up "$(dirname -- "$FILE")" tsconfig.json)
[ -n "$TSC_DIR" ] || exit 0
TSC_BIN_DIR=$(_find_up "$TSC_DIR" node_modules/.bin/tsc)
if [ -n "$TSC_BIN_DIR" ]; then
  TSC="$TSC_BIN_DIR/node_modules/.bin/tsc"
else
  TSC=$(command -v tsc 2>/dev/null)
fi
# Sin tsc (ej. worktree sin node_modules): no tocar el estado.
[ -n "$TSC" ] || exit 0

STATE_DIR="$ROOT/.claude/quality-state"
mkdir -p "$STATE_DIR" 2>/dev/null || true
AUDIT_LOG="$ROOT/.claude/quality-events.log"

RELATIVE="${FILE#"$ROOT"/}"
# Clave de estado por ARCHIVO (no por proyecto) — así un error en un archivo no
# bloquea ediciones a otros archivos sin relación. Antes usaba solo PROJECT_HASH,
# lo que dejaba TODO el proyecto bloqueado por un error en cualquier archivo.
FILE_HASH=$(echo "$RELATIVE" | cksum | cut -d' ' -f1)
ERROR_FILE="$STATE_DIR/ts-$FILE_HASH"

# tsc imprime paths relativos a su cwd (TSC_DIR); match anclado al inicio de
# línea y literal (src/x.ts no debe matchear src/x.tsx ni a/src/x.ts).
REL_TSC="${FILE#"$TSC_DIR"/}"
TS_OUTPUT=$(cd "$TSC_DIR" && "$TSC" --noEmit --pretty false 2>&1)
TSC_RC=$?
FILE_LINES=$(printf '%s\n' "$TS_OUTPUT" | awk -v p="$REL_TSC(" 'index($0, p) == 1 && /error TS/')
FILE_ERRORS=0
[ -n "$FILE_LINES" ] && FILE_ERRORS=$(printf '%s\n' "$FILE_LINES" | wc -l | tr -d ' ')
TS_CODES=$(printf '%s\n' "$FILE_LINES" | grep -o "TS[0-9]*" | sort -u | tr '\n' ',' | sed 's/,$//')

TIMESTAMP=$(date -u +%FT%TZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)

if [ "$FILE_ERRORS" -gt 0 ]; then
    echo "$FILE_ERRORS" > "$ERROR_FILE"
    SAMPLE=$(printf '%s\n' "$FILE_LINES" | head -3)
    echo "$TIMESTAMP | ts | $FILE_ERRORS errors | $RELATIVE | $TS_CODES" >> "$AUDIT_LOG" 2>/dev/null || true
    echo "MANDATORY ACTION REQUIRED: $FILE_ERRORS TypeScript errors in $(basename "$FILE")." >&2
    echo "$SAMPLE" >&2
    echo "You MUST fix these errors before proceeding to the next task. Run: (cd $TSC_DIR && tsc --noEmit)" >&2
elif [ "$TSC_RC" -eq 0 ] || printf '%s\n' "$TS_OUTPUT" | grep -q "error TS"; then
    # tsc corrió de verdad (limpio, o con errores solo en otros archivos)
    rm -f "$ERROR_FILE" 2>/dev/null || true
    echo "$TIMESTAMP | ts | 0 errors | $RELATIVE | FIXED" >> "$AUDIT_LOG" 2>/dev/null || true
else
    # tsc falló sin reportar diagnósticos (crash, config rota): no borrar estado
    echo "$TIMESTAMP | ts | tsc rc=$TSC_RC sin diagnósticos | $RELATIVE | SKIPPED" >> "$AUDIT_LOG" 2>/dev/null || true
fi

# React hooks ordering heuristic — tsx files only, warning only (never blocks)
# Kimi K2 K2-005: systematically writes useEffect before the const fn it calls.
# tsc/eslint don't catch this (closure executes after render, not a true TDZ).
case "$FILE" in *.tsx)
    FIRST_USEF_LINE=$(grep -n "useEffect(" "$FILE" 2>/dev/null | head -1 | cut -d: -f1)
    if [ -n "$FIRST_USEF_LINE" ]; then
        # Any `const <identifier> = (async )?` pattern on a line AFTER the first useEffect
        LATE_CONSTS=$(awk "NR>$FIRST_USEF_LINE && /const [a-zA-Z_][a-zA-Z0-9_]* = (async )?/" "$FILE" \
            2>/dev/null | head -3)
        if [ -n "$LATE_CONSTS" ]; then
            echo "⚠️  [react-order] $(basename "$FILE"): functions declared AFTER useEffect — move them ABOVE." >&2
            echo "$LATE_CONSTS" >&2
            echo "   Pattern: declare 'const fn = () => {}' BEFORE any useEffect that calls fn." >&2
            echo "$TIMESTAMP | react-order | late-const-after-usef | $RELATIVE" >> "$AUDIT_LOG" 2>/dev/null || true
        fi
    fi
    ;;
esac

exit 0  # PostToolUse siempre exit 0
