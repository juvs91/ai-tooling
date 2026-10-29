#!/usr/bin/env bash
# task-scope-lib.sh — resuelve la ruta del task-scope.json de la sesión actual
# y clasifica la intención de un prompt.
# NO es un hook (sin "# event:", no se registra en settings.json) — se FUENTEA
# desde intent-bootstrap.sh, task-scope-updater.sh, scope-gate.sh,
# scripts/task-verify.sh y (ADR-0073) los hooks de estado de sesión y los que
# actúan sobre el repo del archivo editado. Único lugar donde vive esta lógica; los
# consumidores no deben reimplementarla para evitar que diverjan (ADR-0041).
# distributable: true

# project_root <cwd>
#   Raíz del proyecto, independiente de un `cd` previo en Bash. El `.cwd` del
#   stdin de un hook es el cwd ACTUAL de la sesión, no la raíz: usarlo directo
#   escribe/lee el scope file en un subdirectorio y scope-gate falla abierto.
#   Orden: $CLAUDE_PROJECT_DIR → toplevel de git → cwd tal cual.
#   Salida SIN canonizar (igual que antes de ADR-0073): un scope-gate viejo en
#   un consumer compara contra el file_path crudo. Única excepción: si esa base
#   es un worktree enlazado, se mapea al checkout principal (map_to_main).
project_root() {
  local cwd="${1:-.}" base
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -d "$CLAUDE_PROJECT_DIR" ]; then
    base="$CLAUDE_PROJECT_DIR"
  else
    base=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || base=""
    [ -z "$base" ] && base="$cwd"
  fi
  if is_linked_worktree "$base"; then
    map_to_main "$base"
  else
    printf '%s' "$base"
  fi
}

# ── Worktrees y paths canónicos (ADR-0073) ───────────────────────────────────
# Todas las funciones imprimen vacío (o el input) si algo falla y retornan 0,
# salvo is_linked_worktree, que es un predicado (úsala dentro de un `if`).
# /case-sensitive → /Volumes/case-sensitive (y /tmp → /private/tmp en macOS):
# git imprime siempre paths físicos, así que todo lo que se compare contra una
# salida de git se canoniza primero.

# canon_path <path>
#   Path físico (pwd -P) del ancestro existente más cercano + el resto del
#   path (un Write puede apuntar a directorios que aún no existen).
canon_path() {
  local p="${1:-}" d rest="" parent out
  [ -z "$p" ] && return 0
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  d="$p"
  while [ ! -d "$d" ]; do
    rest="/$(basename -- "$d")$rest"
    parent=$(dirname -- "$d")
    [ "$parent" = "$d" ] && break
    d="$parent"
  done
  out=$(CDPATH= cd -P -- "$d" 2>/dev/null && pwd -P) || { printf '%s' "$1"; return 0; }
  [ "$out" = "/" ] && out=""
  out="$out$rest"
  printf '%s' "${out:-/}"
}

# git_info <dir>
#   Un solo fork. Deja en GI_TOP / GI_GITDIR / GI_COMMON (absolutos) el
#   toplevel, el git-dir y el common-dir; vacíos fuera de un work tree
#   (incluido dentro de .git/). git < 2.31 no conoce --path-format y lo
#   devuelve como eco en vez de fallar: se detecta y se canoniza a mano.
git_info() {
  GI_TOP=""; GI_GITDIR=""; GI_COMMON=""
  local dir="${1:-.}" out l1 l2 l3 l4
  out=$(git -C "$dir" rev-parse --show-toplevel --path-format=absolute \
        --git-dir --git-common-dir 2>/dev/null) || return 0
  # l4 solo existe con git viejo: su read llega a EOF (rc≠0) — `|| true` para
  # no matar a un llamador con `set -e` (adr-gate.sh)
  { IFS= read -r l1; IFS= read -r l2; IFS= read -r l3; IFS= read -r l4; } <<< "$out" || true
  case "$l2" in
    --*)
      case "$l3" in /*) ;; *) l3="$dir/$l3" ;; esac
      case "$l4" in /*) ;; *) l4="$dir/$l4" ;; esac
      l2=$(canon_path "$l3"); l3=$(canon_path "$l4")
      ;;
  esac
  GI_TOP="$l1"; GI_GITDIR="$l2"; GI_COMMON="$l3"
  return 0
}

# is_linked_worktree <dir>
#   Verdadero ⇔ git-dir ≠ common-dir. Clasifica bien submódulos y repos con
#   --separate-git-dir (ambos: git-dir == common-dir → NO enlazados).
is_linked_worktree() {
  git_info "${1:-.}"
  [ -n "$GI_GITDIR" ] && [ "$GI_GITDIR" != "$GI_COMMON" ]
}

# main_worktree_of <dir>
#   Checkout principal de un worktree enlazado: primera entrada de
#   `worktree list --porcelain`, solo si no es bare y tiene .git (si no,
#   vacío → no se mapea).
main_worktree_of() {
  local first="" bare=0 n=0 line
  while IFS= read -r line; do
    n=$((n + 1))
    if [ "$n" -eq 1 ]; then
      case "$line" in "worktree "*) first="${line#worktree }" ;; esac
      continue
    fi
    [ -z "$line" ] && break
    [ "$line" = "bare" ] && bare=1
  done < <(git -C "${1:-.}" worktree list --porcelain 2>/dev/null)
  if [ -n "$first" ] && [ "$bare" -eq 0 ] && [ -e "$first/.git" ]; then
    canon_path "$first"
  fi
  return 0
}

# map_to_main <path>
#   Path canónico; si cae dentro de un worktree enlazado W con un principal M
#   válido → M + (path relativo a W). Conserva el offset de un subproyecto
#   (W/apps/web → M/apps/web).
map_to_main() {
  local p d m top
  p=$(canon_path "${1:-.}")
  d="$p"
  while [ ! -d "$d" ] && [ "$d" != "/" ]; do d=$(dirname -- "$d"); done
  git_info "$d"
  if [ -n "$GI_GITDIR" ] && [ "$GI_GITDIR" != "$GI_COMMON" ]; then
    m=$(main_worktree_of "$d")
    if [ -n "$m" ]; then
      top=$(canon_path "$GI_TOP")
      case "$p" in
        "$top")   printf '%s' "$m"; return 0 ;;
        "$top"/*) printf '%s%s' "$m" "${p#"$top"}"; return 0 ;;
      esac
    fi
  fi
  printf '%s' "$p"
}

# state_root <cwd>
#   Raíz CANÓNICA del estado de sesión (.claude/sessions, quality-state,
#   drift-state, escape hatches). Misma base que project_root, mapeada fuera
#   de un worktree enlazado y canonizada.
state_root() {
  local cwd="${1:-.}" base
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -d "$CLAUDE_PROJECT_DIR" ]; then
    base="$CLAUDE_PROJECT_DIR"
  else
    base=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || base=""
    [ -z "$base" ] && base="$cwd"
  fi
  map_to_main "$base"
}

# repo_of_file <file> <cwd>
#   Repo al que pertenece el archivo editado (no el de la sesión). Deja en
#   RF_TOP / RF_COMMON / RF_FILE (canónicos); RF_TOP vacío si está fuera de un
#   repo. Un file_path relativo es relativo al .cwd de la sesión.
repo_of_file() {
  RF_TOP=""; RF_COMMON=""; RF_FILE=""
  local f="${1:-}" cwd="${2:-.}" d
  [ -z "$f" ] && return 0
  case "$f" in /*) ;; *) f="$cwd/$f" ;; esac
  RF_FILE=$(canon_path "$f")
  d=$(dirname -- "$RF_FILE")
  while [ ! -d "$d" ] && [ "$d" != "/" ]; do d=$(dirname -- "$d"); done
  git_info "$d"
  [ -z "$GI_TOP" ] && return 0
  RF_TOP=$(canon_path "$GI_TOP")
  RF_COMMON="$GI_COMMON"
  return 0
}

# scope_locate <cwd> <raw_session_id>
#   Para scripts lanzados desde Bash, donde no existe $CLAUDE_PROJECT_DIR
#   (solo $CLAUDE_CODE_SESSION_ID). Solo lectura:
#   1. con CLAUDE_PROJECT_DIR → scope_file_for_session(project_root)
#   2. subir desde map_to_main(cwd) buscando .claude/sessions/<sid>-task-scope.json
#   3. find acotado (maxdepth 5: M/apps/web/.claude/sessions/x) bajo el
#      checkout principal; varios → el más reciente
#   4. legacy .claude/task-scope.json
scope_locate() {
  local cwd="${1:-.}" sid name d m hit
  sid=$(printf '%s' "${2:-}" | tr -cd 'a-zA-Z0-9_-')
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -d "$CLAUDE_PROJECT_DIR" ]; then
    scope_file_for_session "$(project_root "$cwd")" "$sid"
    return 0
  fi
  if [ -n "$sid" ]; then
    name=".claude/sessions/$sid-task-scope.json"
    d=$(map_to_main "$cwd")
    while [ -n "$d" ]; do
      [ -f "$d/$name" ] && { printf '%s' "$d/$name"; return 0; }
      [ "$d" = "/" ] && break
      d=$(dirname -- "$d")
    done
  fi
  git_info "$cwd"
  m="$GI_TOP"
  if [ -n "$GI_GITDIR" ] && [ "$GI_GITDIR" != "$GI_COMMON" ]; then
    d=$(main_worktree_of "$cwd")
    [ -n "$d" ] && m="$d"
  fi
  if [ -n "$sid" ] && [ -n "$m" ]; then
    hit=$(find "$m" -maxdepth 5 \( -name node_modules -o -name .git -o -path "$m/.claude/worktrees" \) -prune \
          -o -type f -path "*/.claude/sessions/$sid-task-scope.json" -print 2>/dev/null)
    if [ -n "$hit" ]; then
      printf '%s\n' "$hit" | tr '\n' '\0' | xargs -0 ls -t 2>/dev/null | head -1 | tr -d '\n'
      return 0
    fi
  fi
  printf '%s/.claude/task-scope.json' "${m:-$cwd}"
}

# scope_file_for_session <root> <raw_session_id>
#   raw_session_id viene de:
#     - stdin JSON de un hook (.session_id)            → hooks
#     - $CLAUDE_CODE_SESSION_ID del entorno del proceso → scripts/task-verify.sh
#   Sin session id disponible: cae al legacy path fijo .claude/task-scope.json
#   — fallback deliberado, no un bug residual.
scope_file_for_session() {
  local root="${1:-.}"
  local raw_sid="${2:-}"
  local sid
  sid=$(printf '%s' "$raw_sid" | tr -cd 'a-zA-Z0-9_-')
  if [ -n "$sid" ]; then
    printf '%s/.claude/sessions/%s-task-scope.json' "$root" "$sid"
  else
    printf '%s/.claude/task-scope.json' "$root"
  fi
}

# ── Clasificación de intención (ES + EN) ─────────────────────────────────────
# Regla de diseño: equivocarse hacia un modo RESTRICTIVO (analysis, validate,
# synthesize) bloquea trabajo legítimo; equivocarse hacia build/full solo
# pierde una restricción opcional. Por eso:
#   - Cualquier verbo de construcción gana sobre análisis/validación
#     ("valida X y arregla Y" → build, no validate).
#   - Los modos restrictivos solo aplican si NO hay señal de construcción.
# Límites de palabra con clases POSIX (no \b) para que GNU grep y BSD grep
# (macOS) se comporten igual.
_TS_B='(^|[^[:alnum:]_])'
_TS_E='([^[:alnum:]_]|$)'

INTENT_PLAN_RE="${_TS_B}(plan(ea|ear|eemos|ifica|ificar)|diseña|diseñar|disena|disenar|propón|propon|proponer|qué harías|que harias|cómo abordarías|como abordarias|dame un approach|(arma|ármame|armame|haz|hazme) (un|el) plan|make a plan|plan (this|it) out|how would you|propose)${_TS_E}"
INTENT_BUILD_RE="${_TS_B}(implementa(r|lo)?|impleméntalo|implement|arregla(r|lo)?|arréglalo|corrige|corregir|fix|crea(r|lo)?|créalo|create|añade|añadir|anade|agrega(r|lo)?|add|desarrolla(r)?|codifica(r)?|refactoriza(r)?|refactor|modifica(r)?|modify|cambia(r)?|change|actualiza(r)?|update|migra(r)?|migrate|build|hazlo|procede|proceed|go ahead|do it)${_TS_E}"
INTENT_SYNTH_RE="${_TS_B}(documenta(r)?|document|sintetiza(r)?|synthesize|escribe (el |la |un |una )?(doc|readme|guía|guia|reporte|informe)|write (the |a |an )?(docs?|readme|guide|report))${_TS_E}"
INTENT_VALIDATE_RE="${_TS_B}((verifica|valida|corre|ejecuta|verify|validate|run) (los |las |the )?tests?|solo (verifica|valida|revisa)|only (verify|validate|check)|asegúrate de que|asegurate de que|make sure that)"
INTENT_ANALYSIS_RE="${_TS_B}(analiza(r|me)?|analízame|analyze|analyse|cuántos|cuantos|how many|qué hace|que hace|what does|cómo funciona|como funciona|how does|explica(me)?|explain|describe|investiga(r)?|investigate|mapea(r)?|map out|audit|audita(r)?|coverage|cobertura|review (el|la|los|the)|revisa (el|la|los))${_TS_E}"

# intent_classify <prompt>
#   Imprime: plan | build | synthesize | validate | analysis | (nada)
#   Precedencia: plan > build > synthesize > validate > analysis.
intent_classify() {
  local p="$1" re kind
  for kind in plan build synthesize validate analysis; do
    case "$kind" in
      plan)       re="$INTENT_PLAN_RE" ;;
      build)      re="$INTENT_BUILD_RE" ;;
      synthesize) re="$INTENT_SYNTH_RE" ;;
      validate)   re="$INTENT_VALIDATE_RE" ;;
      analysis)   re="$INTENT_ANALYSIS_RE" ;;
    esac
    if printf '%s' "$p" | grep -qiE "$re"; then
      echo "$kind"
      return
    fi
  done
}
