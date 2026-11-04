#!/usr/bin/env bash
# distributable: true
# ci-service.sh — pasos de pipeline de un servicio con imagen por contenido.
# Ref: ADR-0060 (modelo), ADR-0061 (lógica fuera del YAML).
# Lo llaman templates/jobs/acr-build.yml y templates/stages/service-release.yml;
# las decisiones viven en ci-image.sh (qué imagen) y ci-acr.sh (publicar/promover).
#
# Entradas por variables de entorno (el `env:` de cada step del YAML):
#   CI_SERVICE_NAME      prefijo del tag git, ej: ventas-api
#   CI_SERVICE_PATH      ej: ventas/api
#   CI_DEPENDS_ON        opcional: libs separadas por espacio (templates anteriores a ADR-0080).
#                        Vacío = se leen del manifiesto del servicio con deps.py.
#   CI_ACR_NAME, CI_IMAGE_REPOSITORY, CI_BUILD_CONTEXT (default .)
#   CI_DOCKERFILE        vacío = <CI_SERVICE_PATH>/Dockerfile
#   CI_CTX, CI_TAG_*, CI_SOURCE, CI_NEEDS_BUILD, CI_IMAGE_TAG, CI_ENV, CI_IMAGE_REF:
#                        salidas de steps anteriores ($(ctx.ctx), $(tag.env), …)
# Predefinidas del agente: BUILD_SOURCEBRANCH, BUILD_SOURCEBRANCHNAME, BUILD_BUILDID,
#   BUILD_BUILDNUMBER, BUILD_SOURCEVERSION, BUILD_REPOSITORY_URI.
#
# Comandos (salidas como ##vso[task.setvariable …;isOutput=true]):
#   validate-tag        parse-tag del tag que disparó el run → name, version, env, image_tag, …
#   context             hash de contenido del servicio + libs → ctx
#   validate-build      docker build sin push (PR / hotfix)
#   publish-ctx         ensure-ctx en ACR → digest
#   tag-dev             tag git <svc>@<versión>-dev.<BuildId> (salta si la versión ya está en prod)
#   tag-hotfix          tag git <svc>@<versión>-hotfix.<N+1>
#   plan-release        origen de la imagen del tag → commit, source, needsBuild
#   publish-release     ensure-ctx (si hace falta) + promote → digest, imageRef
#   deploy-placeholder  imprime la imagen aprobada (sin runtime configurado)
#
# Exit: 0 OK, 1 error, 2 uso incorrecto.

set -euo pipefail
CI_SCRIPT=ci-service
source "$(dirname "${BASH_SOURCE[0]}")/ci-lib.sh"

IMAGE="$CI_DIR/ci-image.sh"
ACR="$CI_DIR/ci-acr.sh"

_dockerfile() { printf '%s' "${CI_DOCKERFILE:-$CI_SERVICE_PATH/Dockerfile}"; }

# Rutas que definen el contenido de la imagen: servicio + libs, tal como estaban en
# <ref> (default HEAD). Fuente: el manifiesto del servicio vía deps.py (ADR-0080);
# CI_DEPENDS_ON, si viene, gana (pipelines con templates anteriores).
_paths() { # [ref]
  if [[ -n "${CI_DEPENDS_ON:-}" ]]; then
    printf '%s\n' "$CI_SERVICE_PATH"; ci_words CI_DEPENDS_ON; return
  fi
  python3 "$CI_DIR/deps.py" paths "$CI_SERVICE_PATH" --ref "${1:-HEAD}"
}

# _paths_into <ref>... — llena el arreglo global `paths` con la unión de _paths en
# cada ref. Falla el step si deps.py falla (mapfile < <(...) se tragaría el error).
_paths_into() {
  local out="" ref one
  for ref in "$@"; do
    one=$(_paths "$ref") || ci_die "no se pudieron leer las dependencias de $CI_SERVICE_PATH en $ref"
    out+="$one"$'\n'
  done
  mapfile -t paths < <(printf '%s' "$out" | grep -v '^$' | sort -u)
}

_git_tag_push() { # <tag> <mensaje>
  git -c user.name="Azure Pipelines" -c user.email="pipelines@noreply" tag -a "$1" -m "$2"
  git push origin "refs/tags/$1"
}

_labels() { # <ctx>
  printf '%s\0' --label "org.opencontainers.image.revision=${BUILD_SOURCEVERSION:-unknown}" \
    --label "org.opencontainers.image.source=${BUILD_REPOSITORY_URI:-unknown}" \
    --label "gitops.context=$1"
}

cmd_validate_tag() {
  ci_require CI_SERVICE_NAME BUILD_SOURCEBRANCH
  bash "$IMAGE" parse-tag --azure --expect "$CI_SERVICE_NAME" "$BUILD_SOURCEBRANCH"
}

cmd_context() {
  ci_require CI_SERVICE_PATH
  local ctx; _paths_into HEAD
  ctx=$(bash "$IMAGE" context-hash "${paths[@]}")
  echo "contexto: $ctx"
  ci_output ctx "$ctx"
}

cmd_validate_build() {
  ci_require CI_SERVICE_PATH CI_IMAGE_REPOSITORY CI_CTX
  "${DOCKER:-docker}" build -f "$(_dockerfile)" -t "validate/$CI_IMAGE_REPOSITORY:$CI_CTX" "${CI_BUILD_CONTEXT:-.}"
}

cmd_publish_ctx() {
  ci_require CI_SERVICE_PATH CI_ACR_NAME CI_IMAGE_REPOSITORY CI_CTX
  local digest labels
  mapfile -d '' -t labels < <(_labels "$CI_CTX")
  digest=$(bash "$ACR" ensure-ctx "$CI_ACR_NAME" "$CI_IMAGE_REPOSITORY" "$CI_CTX" \
    "$(_dockerfile)" "${CI_BUILD_CONTEXT:-.}" "${labels[@]}")
  echo "digest: $digest"
  ci_output digest "$digest"
}

cmd_tag_dev() {
  ci_require CI_SERVICE_NAME CI_SERVICE_PATH BUILD_BUILDID
  local version tag
  version=$(bash "$IMAGE" manifest-version "$CI_SERVICE_PATH")
  if git rev-parse -q --verify "refs/tags/${CI_SERVICE_NAME}@${version}" >/dev/null; then
    ci_warn "${CI_SERVICE_NAME}@${version} ya está en prod — bumpea la versión (release.sh bump) para generar builds de dev nuevos"
    return 0
  fi
  tag="${CI_SERVICE_NAME}@${version}-dev.${BUILD_BUILDID}"
  _git_tag_push "$tag" "CI dev build ${BUILD_BUILDNUMBER:-$BUILD_BUILDID}"
  echo "tag creado: $tag → dispara el pipeline de release (Environment dev)"
}

cmd_tag_hotfix() {
  ci_require CI_SERVICE_NAME CI_SERVICE_PATH
  local version tag
  version=$(bash "$IMAGE" manifest-version "$CI_SERVICE_PATH")
  tag=$(bash "$IMAGE" next-hotfix "$CI_SERVICE_NAME" "$version")
  _git_tag_push "$tag" "Hotfix ${BUILD_SOURCEBRANCHNAME:-} build ${BUILD_BUILDNUMBER:-}"
  echo "tag creado: $tag → dispara el release (Environment prod, con approval)"
  ci_warn "Después del deploy: ./scripts/release.sh cherry $CI_SERVICE_NAME $version (obligatorio)"
}

cmd_plan_release() {
  ci_require CI_SERVICE_PATH BUILD_SOURCEBRANCH CI_TAG_ENV CI_TAG_NAME CI_TAG_VERSION
  local tag="${BUILD_SOURCEBRANCH#refs/tags/}" commit source needs_build rc
  commit=$(git rev-parse "${tag}^{commit}")
  if [[ "$CI_TAG_ENV" == "prod" ]]; then
    rc=$(bash "$IMAGE" prod-source "$CI_TAG_NAME" "$CI_TAG_VERSION")
    echo "prod se promueve desde $rc"
    _paths_into "$rc" "$tag"   # unión: si cambió una dependencia, el contenido cambia
    bash "$IMAGE" same-content "$rc" "$tag" "${paths[@]}"
    source="${rc#*@}"
    needs_build=false
  else
    _paths_into "$tag"
    source=$(bash "$IMAGE" context-hash --ref "$tag" "${paths[@]}")
    needs_build=true
  fi
  echo "commit=$commit source=$source needs_build=$needs_build"
  ci_output commit "$commit"
  ci_output source "$source"
  ci_output needsBuild "$needs_build"
}

cmd_publish_release() {
  ci_require CI_SERVICE_PATH CI_ACR_NAME CI_IMAGE_REPOSITORY CI_SOURCE CI_IMAGE_TAG CI_ENV CI_NEEDS_BUILD
  local digest server labels
  if [[ "$CI_NEEDS_BUILD" == "true" ]]; then
    mapfile -d '' -t labels < <(_labels "$CI_SOURCE")
    bash "$ACR" ensure-ctx "$CI_ACR_NAME" "$CI_IMAGE_REPOSITORY" "$CI_SOURCE" \
      "$(_dockerfile)" "${CI_BUILD_CONTEXT:-.}" "${labels[@]}" >/dev/null
  fi
  digest=$(bash "$ACR" promote "$CI_ACR_NAME" "$CI_IMAGE_REPOSITORY" "$CI_SOURCE" "$CI_IMAGE_TAG")
  server="${ACR_LOGIN_SERVER:-$CI_ACR_NAME.azurecr.io}"
  echo "$CI_ENV: $server/$CI_IMAGE_REPOSITORY:$CI_IMAGE_TAG → $digest"
  ci_output digest "$digest"
  ci_output imageRef "$server/$CI_IMAGE_REPOSITORY@$digest"
}

cmd_deploy_placeholder() {
  ci_require CI_ENVIRONMENT CI_IMAGE_REF
  echo "Placeholder: sin runtime configurado todavía."
  echo "Imagen aprobada para $CI_ENVIRONMENT: $CI_IMAGE_REF"
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    validate-tag)       cmd_validate_tag "$@" ;;
    context)            cmd_context "$@" ;;
    validate-build)     cmd_validate_build "$@" ;;
    publish-ctx)        cmd_publish_ctx "$@" ;;
    tag-dev)            cmd_tag_dev "$@" ;;
    tag-hotfix)         cmd_tag_hotfix "$@" ;;
    plan-release)       cmd_plan_release "$@" ;;
    publish-release)    cmd_publish_release "$@" ;;
    deploy-placeholder) cmd_deploy_placeholder "$@" ;;
    *) ci_usage "uso: ci-service.sh <validate-tag|context|validate-build|publish-ctx|tag-dev|tag-hotfix|plan-release|publish-release|deploy-placeholder>" ;;
  esac
}

main "$@"
