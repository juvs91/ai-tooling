#!/usr/bin/env bash
# distributable: true
# ci-python.sh — CI de un servicio/lib Python con uv. Ref: ADR-0061.
# Lo llama templates/jobs/python-ci.yml. Se ejecuta desde la raíz del repo; los
# comandos que trabajan sobre el proyecto entran solos a CI_WORKING_DIRECTORY.
#
# Entradas (env:):
#   CI_WORKING_DIRECTORY  ej: ventas/api
#   CI_PIPELINE_FILE      YAML del pipeline (check-paths compara trigger.paths)
#   CI_DEPENDS_ON         libs compartidas separadas por espacio
#   CI_UV_VERSION         vacío = última
# Predefinida: AGENT_TEMPDIRECTORY (destino del JUnit).
#
# Comandos: check-paths | install-uv | lint | test
# Exit: 0 OK, 1 error, 2 uso incorrecto.

set -euo pipefail
CI_SCRIPT=ci-python
source "$(dirname "${BASH_SOURCE[0]}")/ci-lib.sh"

cmd_check_paths() {
  ci_require CI_PIPELINE_FILE CI_WORKING_DIRECTORY
  local deps; mapfile -t deps < <(ci_words CI_DEPENDS_ON)
  # ci-image.sh check-paths lee el YAML con PyYAML; el python del toolcache no lo trae
  python3 -c 'import yaml' 2>/dev/null \
    || python3 -m pip install --disable-pip-version-check -q pyyaml
  bash "$CI_DIR/ci-image.sh" check-paths "$CI_PIPELINE_FILE" "$CI_WORKING_DIRECTORY" "${deps[@]}"
}

cmd_install_uv() {
  local spec=uv
  [[ -n "${CI_UV_VERSION:-}" ]] && spec="uv==$CI_UV_VERSION"
  python -m pip install --disable-pip-version-check "$spec"
  uv --version
}

cmd_lint() {
  ci_require CI_WORKING_DIRECTORY
  cd "$CI_WORKING_DIRECTORY"
  if [[ -f uv.lock ]]; then uv sync --locked; else uv sync; fi
  uv run --with ruff ruff check .
  uv run --with ruff ruff format --check .
}

cmd_test() {
  ci_require CI_WORKING_DIRECTORY AGENT_TEMPDIRECTORY
  cd "$CI_WORKING_DIRECTORY"
  uv run --with pytest pytest --junitxml="$AGENT_TEMPDIRECTORY/test-results.xml"
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    check-paths) cmd_check_paths "$@" ;;
    install-uv)  cmd_install_uv "$@" ;;
    lint)        cmd_lint "$@" ;;
    test)        cmd_test "$@" ;;
    *) ci_usage "uso: ci-python.sh <check-paths|install-uv|lint|test>" ;;
  esac
}

main "$@"
