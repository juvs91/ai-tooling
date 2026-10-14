#!/usr/bin/env bash
# distributable: true
# ci-image.sh — lógica git del pipeline de imágenes (ACR) para el monorepo GitOps.
# Ref: ADR-0007 (tags por proyecto), ADR-0034 (manifest = versión), ADR-0060.
#
# Solo hace cálculos sobre git — no habla con ACR ni Docker. Los templates de
# Azure Pipelines (templates/gitops/azure-pipelines/) lo llaman y hacen las
# operaciones de registry con su salida. Así la parte que decide QUÉ imagen va
# a cada ambiente es testeable sin nube.
#
# Comandos:
#   context-hash [--ref R] <ruta> [<ruta>...]
#       Imprime ctx-<16hex>: sha256 de los tree hashes git de las rutas (servicio
#       + libs compartidas + lo que entre al build) en el ref R (default HEAD).
#       Commits que no tocan esas rutas NO cambian el hash → la imagen se
#       identifica por contenido, no por commit.
#   parse-tag [--expect <nombre>] [--azure] <tag>
#       Valida <nombre>@X.Y.Z[-(dev|rc|hotfix).N] (acepta refs/tags/…) e imprime
#       name, version, suffix, n, env (dev|qa|prod|hotfix), image_tag.
#       --azure además emite ##vso[task.setvariable …;isOutput=true].
#   prod-source <nombre> <X.Y.Z>
#       Imprime el último tag <nombre>@X.Y.Z-rc.N (N numérico, no léxico).
#       Prod se promueve desde el digest de ese rc — lo que QA probó.
#   next-hotfix <nombre> <X.Y.Z>
#       Imprime <nombre>@X.Y.Z-hotfix.N con N = mayor existente + 1 (numérico).
#       Exige que exista el tag de prod <nombre>@X.Y.Z (el hotfix parte de él).
#   same-content <refA> <refB> <ruta> [<ruta>...]
#       Exit 0 si las rutas tienen el mismo contenido en ambos refs. Prod
#       taggea HEAD de trunk (release.sh tag), no el SHA del rc: lo que importa
#       es que el contenido del servicio no haya cambiado desde QA.
#   manifest-version <ruta>
#       Versión del servicio/lib según su manifest (ADR-0034): pyproject.toml
#       ([project].version) → package.json → VERSION.
#   pep440 <tag>
#       Versión PEP 440 para publicar una lib en Azure Artifacts:
#       X.Y.Z-dev.N → X.Y.Z.devN · X.Y.Z-rc.N → X.Y.ZrcN · X.Y.Z → X.Y.Z.
#       -hotfix.N se rechaza: una lib corrige con un patch nuevo, no con hotfix.
#   check-paths <pipeline.yml> <ruta> [<ruta>...]
#       Exit 0 si trigger.paths.include del pipeline cubre todas las rutas.
#       Los triggers de Azure son estáticos (no aceptan parámetros), así que la
#       lista de dependencias vive dos veces en el YAML — esto evita que diverjan.
#
# Exit: 0 OK, 1 validación fallida, 2 uso incorrecto.

set -euo pipefail

die()   { printf 'ci-image: %b\n' "$*" >&2; exit 1; }
usage() { printf 'ci-image: %b\n' "$*" >&2; exit 2; }

TAG_RE='^([a-z0-9][a-z0-9-]*)@([0-9]+\.[0-9]+\.[0-9]+)(-(dev|rc|hotfix)\.([0-9]+))?$'

cmd_context_hash() {
  local ref="HEAD"
  if [[ "${1:-}" == "--ref" ]]; then ref="${2:-}"; shift 2 || usage "--ref requiere valor"; fi
  [[ $# -gt 0 ]] || usage "uso: context-hash [--ref R] <ruta> [<ruta>...]"

  local commit; commit=$(git rev-parse --verify --quiet "${ref}^{commit}") \
    || die "ref inválido: $ref"

  local p tree manifest=""
  # Orden estable e independiente de cómo se pasaron las rutas
  while IFS= read -r p; do
    [[ -z "$p" ]] && continue
    p="${p%/}"
    tree=$(git rev-parse --verify --quiet "${commit}:${p}") \
      || die "la ruta '$p' no existe en $ref"
    manifest+="${p}=${tree}"$'\n'
  done < <(printf '%s\n' "$@" | sort -u)

  printf 'ctx-%s\n' "$(printf '%s' "$manifest" | sha256sum | cut -c1-16)"
}

cmd_parse_tag() {
  local expect="" azure=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --expect) expect="${2:-}"; shift 2 ;;
      --azure)  azure=true; shift ;;
      --) shift; break ;;
      -*) usage "opción desconocida: $1" ;;
      *) break ;;
    esac
  done
  local tag="${1:-}"
  [[ -n "$tag" ]] || usage "uso: parse-tag [--expect <nombre>] [--azure] <tag>"
  tag="${tag#refs/tags/}"

  [[ "$tag" =~ $TAG_RE ]] || die "tag fuera de convención: '$tag'\n  esperado: <nombre>@X.Y.Z[-(dev|rc|hotfix).N]"
  local name="${BASH_REMATCH[1]}" version="${BASH_REMATCH[2]}"
  local suffix="${BASH_REMATCH[4]}" n="${BASH_REMATCH[5]}"

  [[ -z "$expect" || "$name" == "$expect" ]] \
    || die "el tag '$tag' es de '$name', este pipeline es de '$expect'"

  local env image_tag
  case "$suffix" in
    dev)    env=dev ;;
    rc)     env=qa ;;
    hotfix) env=hotfix ;;
    "")     env=prod ;;
  esac
  image_tag="$version${suffix:+-$suffix.$n}"

  local k v
  for k in name version suffix n env image_tag; do
    v="${!k}"
    printf '%s=%s\n' "$k" "$v"
    $azure && printf '##vso[task.setvariable variable=%s;isOutput=true]%s\n' "$k" "$v"
  done
  return 0
}

cmd_prod_source() {
  local name="${1:-}" version="${2:-}"
  [[ -n "$name" && -n "$version" ]] || usage "uso: prod-source <nombre> <X.Y.Z>"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "versión inválida: $version"

  local best="" best_n=-1 t n
  while IFS= read -r t; do
    [[ -z "$t" ]] && continue
    n="${t##*-rc.}"
    [[ "$n" =~ ^[0-9]+$ ]] || continue
    (( n > best_n )) && { best_n=$n; best="$t"; }
  done < <(git tag -l "${name}@${version}-rc.*")

  [[ -n "$best" ]] || die "no existe ningún ${name}@${version}-rc.N — prod solo se promueve desde un rc aprobado en QA"
  printf '%s\n' "$best"
}

cmd_next_hotfix() {
  local name="${1:-}" version="${2:-}"
  [[ -n "$name" && -n "$version" ]] || usage "uso: next-hotfix <nombre> <X.Y.Z>"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "versión inválida: $version"
  git rev-parse -q --verify "refs/tags/${name}@${version}" >/dev/null \
    || die "no existe ${name}@${version} — un hotfix parte de un tag de prod (release.sh hotfix)"
  local max=0 t n
  while IFS= read -r t; do
    n="${t##*-hotfix.}"
    [[ "$n" =~ ^[0-9]+$ ]] && (( n > max )) && max=$n
  done < <(git tag -l "${name}@${version}-hotfix.*")
  printf '%s@%s-hotfix.%s\n' "$name" "$version" "$((max + 1))"
}

cmd_same_content() {
  [[ $# -ge 3 ]] || usage "uso: same-content <refA> <refB> <ruta> [<ruta>...]"
  local a="$1" b="$2"; shift 2
  git rev-parse --verify --quiet "${a}^{commit}" >/dev/null || die "ref inválido: $a"
  git rev-parse --verify --quiet "${b}^{commit}" >/dev/null || die "ref inválido: $b"
  if git diff --quiet "${a}^{commit}" "${b}^{commit}" -- "$@"; then
    return 0
  fi
  printf 'ci-image: el contenido cambió entre %s y %s en:\n' "$a" "$b" >&2
  git diff --stat "${a}^{commit}" "${b}^{commit}" -- "$@" >&2
  return 1
}

cmd_check_paths() {
  [[ $# -ge 2 ]] || usage "uso: check-paths <pipeline.yml> <ruta> [<ruta>...]"
  local yml="$1"; shift
  [[ -f "$yml" ]] || die "no existe: $yml"
  command -v python3 >/dev/null || die "python3 requerido para leer YAML"

  python3 - "$yml" "$@" <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit("ci-image: PyYAML requerido (pip install pyyaml)")
yml, required = sys.argv[1], [p.rstrip("/") for p in sys.argv[2:]]
with open(yml) as f:
    doc = yaml.safe_load(f) or {}
trigger = doc.get("trigger") or {}
include = [p.rstrip("/") for p in ((trigger.get("paths") or {}).get("include") or [])]
def covered(p):
    return any(p == i or p.startswith(i + "/") for i in include)
missing = [p for p in required if not covered(p)]
if missing:
    print(f"ci-image: {yml}: trigger.paths.include no cubre: {', '.join(missing)}", file=sys.stderr)
    print(f"  include actual: {include}", file=sys.stderr)
    sys.exit(1)
PY
}

cmd_manifest_version() {
  local dir="${1:-}"
  [[ -n "$dir" ]] || usage "uso: manifest-version <ruta>"
  dir="${dir%/}"
  local v=""
  if [[ -f "$dir/pyproject.toml" ]]; then
    v=$(python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb")).get("project", {}).get("version", ""))' "$dir/pyproject.toml") \
      || die "no se pudo leer $dir/pyproject.toml (python ≥ 3.11)"
  elif [[ -f "$dir/package.json" ]]; then
    v=$(python3 -c 'import sys, json; print(json.load(open(sys.argv[1])).get("version", ""))' "$dir/package.json") \
      || die "no se pudo leer $dir/package.json"
  elif [[ -f "$dir/VERSION" ]]; then
    v=$(tr -d '[:space:]' < "$dir/VERSION")
  else
    die "sin manifest en $dir (pyproject.toml, package.json o VERSION)"
  fi
  [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "versión no semver en el manifest de $dir: '$v'"
  printf '%s\n' "$v"
}

cmd_pep440() {
  local tag="${1:-}"
  [[ -n "$tag" ]] || usage "uso: pep440 <tag>"
  tag="${tag#refs/tags/}"
  [[ "$tag" =~ $TAG_RE ]] || die "tag fuera de convención: '$tag'"
  local version="${BASH_REMATCH[2]}" suffix="${BASH_REMATCH[4]}" n="${BASH_REMATCH[5]}"
  case "$suffix" in
    "")     printf '%s\n' "$version" ;;
    dev)    printf '%s.dev%s\n' "$version" "$n" ;;
    rc)     printf '%src%s\n' "$version" "$n" ;;
    hotfix) die "las libs no usan -hotfix: publica un patch nuevo (X.Y.Z+1)" ;;
  esac
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    context-hash) cmd_context_hash "$@" ;;
    parse-tag)    cmd_parse_tag "$@" ;;
    prod-source)  cmd_prod_source "$@" ;;
    same-content) cmd_same_content "$@" ;;
    next-hotfix)  cmd_next_hotfix "$@" ;;
    check-paths)  cmd_check_paths "$@" ;;
    manifest-version) cmd_manifest_version "$@" ;;
    pep440)       cmd_pep440 "$@" ;;
    ""|-h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' ;;
    *) usage "comando desconocido: $cmd (ver --help)" ;;
  esac
}

main "$@"
