#!/usr/bin/env bash
# distributable: true
# ci-lib.sh — helpers compartidos por los scripts de pipeline (ci-service.sh,
# ci-python.sh, ci-pylib.sh). Se FUENTEA, no se ejecuta. Ref: ADR-0061.
#
# Contrato con el agente: los resultados se publican como logging commands de
# Azure Pipelines en stdout (##vso[...]). El agente real y tools/azure-sim/run.py
# los leen igual — por eso no hay un modo "local" distinto.

ci_die()  { printf '##vso[task.logissue type=error]%s\n' "$*"; printf '%s: %s\n' "${CI_SCRIPT:-ci}" "$*" >&2; exit 1; }
ci_usage() { printf '%s: %s\n' "${CI_SCRIPT:-ci}" "$*" >&2; exit 2; }
ci_warn() { printf '##vso[task.logissue type=warning]%s\n' "$*"; }
ci_log()  { printf '%s: %s\n' "${CI_SCRIPT:-ci}" "$*" >&2; }

# ci_output <nombre> <valor> — variable de salida del step (isOutput=true).
# El step debe tener `name:` en el YAML para que otros la lean como $(<step>.<nombre>).
ci_output() { printf '##vso[task.setvariable variable=%s;isOutput=true]%s\n' "$1" "$2"; }

# ci_require VAR... — falla si alguna variable de entorno falta o está vacía.
# Las de entrada se declaran en el `env:` del step; las predefinidas
# (BUILD_SOURCEBRANCH, BUILD_BUILDID, …) las pone el agente.
ci_require() {
  local v missing=()
  for v in "$@"; do [[ -n "${!v:-}" ]] || missing+=("$v"); done
  [[ ${#missing[@]} -eq 0 ]] || ci_usage "faltan variables de entorno: ${missing[*]}"
}

# ci_words <VAR> — imprime una lista separada por espacios, un elemento por línea
# (CI_DEPENDS_ON llega como "a b c" desde ${{ join(' ', parameters.dependsOn) }}).
ci_words() { local w; for w in ${!1:-}; do printf '%s\n' "$w"; done; }

CI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
