#!/usr/bin/env bash
# task-scope-lib.sh — resuelve la ruta del task-scope.json de la sesión actual
# y clasifica la intención de un prompt.
# NO es un hook (sin "# event:", no se registra en settings.json) — se FUENTEA
# desde intent-bootstrap.sh, task-scope-updater.sh, scope-gate.sh y
# scripts/task-verify.sh. Único lugar donde vive esta lógica; los
# consumidores no deben reimplementarla para evitar que diverjan (ADR-0041).
# distributable: true

# project_root <cwd>
#   Raíz del proyecto, independiente de un `cd` previo en Bash. El `.cwd` del
#   stdin de un hook es el cwd ACTUAL de la sesión, no la raíz: usarlo directo
#   escribe/lee el scope file en un subdirectorio y scope-gate falla abierto.
#   Orden: $CLAUDE_PROJECT_DIR → toplevel de git → cwd tal cual.
project_root() {
  local cwd="${1:-.}"
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -d "$CLAUDE_PROJECT_DIR" ]; then
    printf '%s' "$CLAUDE_PROJECT_DIR"
    return
  fi
  git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$cwd"
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
