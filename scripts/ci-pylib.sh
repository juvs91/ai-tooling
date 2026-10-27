#!/usr/bin/env bash
# distributable: true
# ci-pylib.sh — release de una lib Python de _shared/libs a Azure Artifacts.
# Ref: ADR-0060 §10 (modelo), ADR-0061 (lógica fuera del YAML).
# Lo llama templates/stages/python-lib-release.yml. Se ejecuta desde la raíz del repo.
#
# Entradas (env:):
#   CI_LIB_NAME        prefijo del tag git, ej: auth
#   CI_LIB_PATH        ej: _shared/libs/auth
#   CI_ARTIFACT_FEED   <proyecto>/<feed> o <feed>
#   CI_PEP440, CI_TAG_ENV            salidas del step `tag`
#   CI_VIEW_RC, CI_VIEW_PROD         views de promoción (Prerelease / Release)
#   SYSTEM_ACCESSTOKEN               mapeado explícito: $(System.AccessToken)
#   CI_PKGS_BASE_URL   default https://pkgs.dev.azure.com (el simulador lo redirige)
# Predefinidas: BUILD_SOURCEBRANCH, SYSTEM_COLLECTIONURI, AGENT_TEMPDIRECTORY,
#   PYPIRC_PATH (lo deja TwineAuthenticate@1).
#
# Comandos:
#   validate      parse-tag + manifest == versión del tag → name, version, env, …, pep440
#   build         reescribe la versión del pyproject (solo en el agente) y uv build
#   upload        twine upload; un 409/"already exists" es re-run y se omite
#   promote-view  rc → view CI_VIEW_RC, prod → CI_VIEW_PROD, dev → queda en @Local
#
# Exit: 0 OK, 1 error, 2 uso incorrecto.

set -euo pipefail
CI_SCRIPT=ci-pylib
source "$(dirname "${BASH_SOURCE[0]}")/ci-lib.sh"

IMAGE="$CI_DIR/ci-image.sh"
PYPROJECT="$CI_DIR/ci_pyproject.py"

cmd_validate() {
  ci_require CI_LIB_NAME CI_LIB_PATH BUILD_SOURCEBRANCH
  local pep440 manifest version
  bash "$IMAGE" parse-tag --azure --expect "$CI_LIB_NAME" "$BUILD_SOURCEBRANCH"
  pep440=$(bash "$IMAGE" pep440 "$BUILD_SOURCEBRANCH")
  manifest=$(bash "$IMAGE" manifest-version "$CI_LIB_PATH")
  version="${BUILD_SOURCEBRANCH#refs/tags/*@}"; version="${version%%-*}"
  [[ "$manifest" == "$version" ]] \
    || ci_die "el tag dice $version pero el manifest de $CI_LIB_PATH dice $manifest (ADR-0034: bumpea por PR antes de taggear)"
  echo "versión a publicar: $pep440"
  ci_output pep440 "$pep440"
}

cmd_build() {
  ci_require CI_LIB_PATH CI_PEP440
  python -m pip install --disable-pip-version-check uv twine
  cd "$CI_LIB_PATH"
  # La versión publicada sale del tag; el manifest en git conserva X.Y.Z.
  python "$PYPROJECT" set-version pyproject.toml "$CI_PEP440"
  uv build --out-dir dist
  ls -l dist
}

cmd_upload() {
  ci_require CI_LIB_PATH CI_ARTIFACT_FEED CI_PEP440 PYPIRC_PATH
  local feed="${CI_ARTIFACT_FEED##*/}" out
  cd "$CI_LIB_PATH"
  if out=$(python -m twine upload -r "$feed" --config-file "$PYPIRC_PATH" --non-interactive dist/* 2>&1); then
    echo "$out"; return 0
  fi
  echo "$out"
  if grep -qiE 'already exists|409' <<<"$out"; then
    ci_warn "$CI_PEP440 ya estaba publicada — re-run, se omite la subida"
    return 0
  fi
  return 1
}

cmd_promote_view() {
  ci_require CI_LIB_PATH CI_ARTIFACT_FEED CI_PEP440 CI_TAG_ENV
  local view feed project pkg org url body http out
  case "$CI_TAG_ENV" in
    qa)   view="${CI_VIEW_RC:?CI_VIEW_RC vacío}" ;;
    prod) view="${CI_VIEW_PROD:?CI_VIEW_PROD vacío}" ;;
    *)    echo "$CI_TAG_ENV: queda en @Local, sin promoción"; return 0 ;;
  esac
  ci_require SYSTEM_ACCESSTOKEN SYSTEM_COLLECTIONURI
  feed="$CI_ARTIFACT_FEED"
  case "$feed" in */*) project="${feed%%/*}/"; feed="${feed##*/}" ;; *) project="" ;; esac
  pkg=$(python "$PYPROJECT" name "$CI_LIB_PATH/pyproject.toml")
  org="${SYSTEM_COLLECTIONURI%/}"; org="${org##*/}"
  url="${CI_PKGS_BASE_URL:-https://pkgs.dev.azure.com}/${org}/${project}_apis/packaging/feeds/${feed}/pypi/packages/${pkg}/versions/${CI_PEP440}?api-version=7.1-preview.1"
  body=$(printf '{"views":{"op":"add","path":"/views/-","value":"%s"}}' "$view")
  out="${AGENT_TEMPDIRECTORY:-${TMPDIR:-/tmp}}/promote.out"
  http=$(curl -sS -o "$out" -w '%{http_code}' -X PATCH \
    -H "Authorization: Bearer $SYSTEM_ACCESSTOKEN" -H "Content-Type: application/json" \
    -d "$body" "$url")
  cat "$out"; echo
  [[ "$http" -ge 200 && "$http" -lt 300 ]] || ci_die "promoción a @$view falló (HTTP $http)"
  echo "$pkg $CI_PEP440 → @$view"
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    validate)     cmd_validate "$@" ;;
    build)        cmd_build "$@" ;;
    upload)       cmd_upload "$@" ;;
    promote-view) cmd_promote_view "$@" ;;
    *) ci_usage "uso: ci-pylib.sh <validate|build|upload|promote-view>" ;;
  esac
}

main "$@"
