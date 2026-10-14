#!/usr/bin/env bash
# distributable: true
# ci-acr.sh — operaciones de Azure Container Registry para el monorepo GitOps.
# Ref: ADR-0060. Complementa scripts/ci-image.sh (que decide QUÉ imagen va a cada
# ambiente); este script la publica, re-taggea y bloquea.
#
# Requiere: az (sesión ya autenticada — AzureCLI@2 con workload identity) y docker.
# La identidad necesita AcrPush sobre el registry (push + locks de tag/manifest).
#
# Comandos:
#   digest <acr> <repo>:<tag>
#       Imprime el digest (sha256:…). Exit 1 si el tag no existe.
#   ensure-ctx <acr> <repo> <ctx-tag> <dockerfile> <context> [--label k=v]...
#       Si <repo>:<ctx-tag> ya existe → no construye. Si no → docker build + push.
#       En ambos casos deja bloqueados tag y manifest e imprime el digest.
#   promote <acr> <repo> <tag-origen> <tag-nuevo>
#       Re-taggea el digest de <tag-origen> como <tag-nuevo> sin rebuild y bloquea.
#       Idempotente: si <tag-nuevo> ya apunta al mismo digest, OK. Si apunta a otro
#       digest → falla (los tags publicados son inmutables).
#
# Variables: ACR_LOGIN_SERVER (default <acr>.azurecr.io), AZ y DOCKER (binarios —
# los tests los reemplazan por fakes).
#
# Exit: 0 OK, 1 error/validación, 2 uso incorrecto.
# Internamente, _digest usa 3 = "el tag no existe" para no confundirlo con un
# error real (auth/red), que nunca debe disparar un rebuild.

set -euo pipefail

AZ="${AZ:-az}"
DOCKER="${DOCKER:-docker}"

die()   { printf 'ci-acr: %b\n' "$*" >&2; exit 1; }
usage() { printf 'ci-acr: %b\n' "$*" >&2; exit 2; }
log()   { printf 'ci-acr: %s\n' "$*" >&2; }

_server() { printf '%s' "${ACR_LOGIN_SERVER:-$1.azurecr.io}"; }

_login() {
  local acr="$1"
  "$AZ" acr login --name "$acr" >/dev/null || die "az acr login falló para $acr"
}

# _tag_exists <acr> <repo> <tag>
#   Decide existencia listando repos/tags en vez de interpretar el mensaje de
#   error de `repository show` — así un error de auth/red no se confunde con
#   "no existe" (y no dispara un rebuild que pisaría lo publicado).
_tag_exists() {
  local acr="$1" repo="$2" tag="$3" repos tags
  repos=$("$AZ" acr repository list --name "$acr" -o tsv) || die "no se pudo listar repositorios de $acr"
  printf '%s\n' "$repos" | grep -qxF "$repo" || return 3
  tags=$("$AZ" acr repository show-tags --name "$acr" --repository "$repo" -o tsv) \
    || die "no se pudo listar tags de $acr/$repo"
  printf '%s\n' "$tags" | grep -qxF "$tag" || return 3
}

_digest() {
  local acr="$1" repo="$2" tag="$3" d rc=0
  _tag_exists "$acr" "$repo" "$tag" || rc=$?
  [[ $rc -eq 0 ]] || return "$rc"
  d=$("$AZ" acr repository show --name "$acr" --image "$repo:$tag" --query digest -o tsv) \
    || die "no se pudo leer el digest de $repo:$tag"
  [[ "$d" == sha256:* ]] || die "digest inesperado para $repo:$tag: '$d'"
  printf '%s' "$d"
}

# Tag y manifest se bloquean por separado (Learn: locks independientes).
_lock() {
  local acr="$1" repo="$2" tag="$3" digest="$4"
  "$AZ" acr repository update --name "$acr" --image "$repo:$tag" \
    --write-enabled false --delete-enabled false -o none \
    || die "no se pudo bloquear $repo:$tag"
  "$AZ" acr repository update --name "$acr" --image "$repo@$digest" \
    --write-enabled false --delete-enabled false -o none \
    || die "no se pudo bloquear $repo@$digest"
}

cmd_digest() {
  [[ $# -eq 2 && "$2" == *:* ]] || usage "uso: digest <acr> <repo>:<tag>"
  local acr="$1" repo="${2%%:*}" tag="${2#*:}" rc=0
  _digest "$acr" "$repo" "$tag" || rc=$?
  [[ $rc -eq 3 ]] && die "$repo:$tag no existe en $acr"
  [[ $rc -eq 0 ]] || exit "$rc"
  echo
}

cmd_ensure_ctx() {
  [[ $# -ge 5 ]] || usage "uso: ensure-ctx <acr> <repo> <ctx-tag> <dockerfile> <context> [--label k=v]..."
  local acr="$1" repo="$2" ctx="$3" dockerfile="$4" context="$5"; shift 5
  [[ "$ctx" =~ ^ctx-[0-9a-f]{16}$ ]] || die "ctx-tag inválido: $ctx (usa ci-image.sh context-hash)"
  local labels=()
  while [[ $# -gt 0 ]]; do
    [[ "$1" == "--label" && -n "${2:-}" ]] || usage "argumento inesperado: $1"
    labels+=(--label "$2"); shift 2
  done

  local server ref digest rc=0
  server=$(_server "$acr"); ref="$server/$repo:$ctx"

  digest=$(_digest "$acr" "$repo" "$ctx") || rc=$?
  if [[ $rc -eq 0 ]]; then
    log "$repo:$ctx ya existe ($digest) — sin rebuild"
  elif [[ $rc -ne 3 ]]; then
    exit "$rc"
  else
    [[ -f "$dockerfile" ]] || die "no existe el Dockerfile: $dockerfile"
    _login "$acr"
    log "construyendo $ref"
    "$DOCKER" build -f "$dockerfile" "${labels[@]}" -t "$ref" "$context" >&2 || die "docker build falló"
    "$DOCKER" push "$ref" >&2 || die "docker push falló"
    digest=$(_digest "$acr" "$repo" "$ctx") || die "$repo:$ctx no aparece en $acr tras el push"
  fi

  _lock "$acr" "$repo" "$ctx" "$digest"
  printf '%s\n' "$digest"
}

cmd_promote() {
  [[ $# -eq 4 ]] || usage "uso: promote <acr> <repo> <tag-origen> <tag-nuevo>"
  local acr="$1" repo="$2" src="$3" dst="$4"
  local src_digest dst_digest server rc=0
  src_digest=$(_digest "$acr" "$repo" "$src") || rc=$?
  [[ $rc -eq 3 ]] && die "$repo:$src no existe en $acr — no hay imagen que promover"
  [[ $rc -eq 0 ]] || exit "$rc"

  rc=0
  dst_digest=$(_digest "$acr" "$repo" "$dst") || rc=$?
  [[ $rc -eq 0 || $rc -eq 3 ]] || exit "$rc"
  if [[ $rc -eq 0 ]]; then
    [[ "$dst_digest" == "$src_digest" ]] \
      || die "$repo:$dst ya existe con otro digest ($dst_digest ≠ $src_digest) — los tags publicados son inmutables"
    log "$repo:$dst ya apunta a $src_digest — nada que hacer"
  else
    server=$(_server "$acr")
    _login "$acr"
    log "re-tag $repo:$src → $repo:$dst ($src_digest)"
    "$DOCKER" buildx imagetools create --tag "$server/$repo:$dst" "$server/$repo@$src_digest" >&2 \
      || die "imagetools create falló"
    dst_digest=$(_digest "$acr" "$repo" "$dst") || die "$repo:$dst no aparece en $acr tras el re-tag"
    [[ "$dst_digest" == "$src_digest" ]] || die "el re-tag produjo otro digest ($dst_digest ≠ $src_digest)"
  fi

  _lock "$acr" "$repo" "$dst" "$src_digest"
  printf '%s\n' "$src_digest"
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    digest)     cmd_digest "$@" ;;
    ensure-ctx) cmd_ensure_ctx "$@" ;;
    promote)    cmd_promote "$@" ;;
    ""|-h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' ;;
    *) usage "comando desconocido: $cmd (ver --help)" ;;
  esac
}

main "$@"
