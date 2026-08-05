#!/usr/bin/env bash
# task-scope-lib.sh — resuelve la ruta del task-scope.json de la sesión actual.
# NO es un hook (sin "# event:", no se registra en settings.json) — se FUENTEA
# desde intent-bootstrap.sh, task-scope-updater.sh, scope-gate.sh y
# scripts/task-verify.sh. Único lugar donde vive esta lógica; los 4
# consumidores no deben reimplementarla para evitar que diverjan (ADR-0041).
# distributable: true

# scope_file_for_session <cwd> <raw_session_id>
#   raw_session_id viene de:
#     - stdin JSON de un hook (.session_id)            → hooks
#     - $CLAUDE_CODE_SESSION_ID del entorno del proceso → scripts/task-verify.sh
#   Sin session id disponible: cae al legacy path fijo .claude/task-scope.json
#   — fallback deliberado, no un bug residual.
scope_file_for_session() {
  local cwd="${1:-.}"
  local raw_sid="${2:-}"
  local sid
  sid=$(printf '%s' "$raw_sid" | tr -cd 'a-zA-Z0-9_-')
  if [ -n "$sid" ]; then
    printf '%s/.claude/sessions/%s-task-scope.json' "$cwd" "$sid"
  else
    printf '%s/.claude/task-scope.json' "$cwd"
  fi
}
