#!/usr/bin/env bash
# scripts/release.sh
#
# GitOps Monorepo — Deacero
# Ref: docs/adr/ADR-0007-gitops-monorepo-trunk-based.md
#
# Uso:
#   ./scripts/release.sh work    <proy> [proy-b...]   sparse checkout
#   ./scripts/release.sh expand                       checkout completo
#   ./scripts/release.sh sync                         rebase diario
#   ./scripts/release.sh status                       estado del entorno
#   ./scripts/release.sh add     <path>               agregar al sparse set
#   ./scripts/release.sh drop    <path>               quitar del sparse set
#   ./scripts/release.sh init    <proy>               alias de work (un proyecto)
#   ./scripts/release.sh init-multi <proy...>         alias de work (multi)
#   ./scripts/release.sh tag     <proyecto> <1.4.2>   crear tag de release
#   ./scripts/release.sh hotfix  <proyecto> <1.4.2> [nombre]  branch desde prod
#   ./scripts/release.sh cherry  <proyecto> <1.4.2>   cherry-pick a trunk
#   ./scripts/release.sh check   [proyecto]            hotfixes pendientes
#   ./scripts/release.sh promote <proyecto> <1.4.2> <dev|rc>  re-promote/rollback
#
# Variables de entorno:
#   GITOPS_REMOTE         remote autoritativo (auto-detect si no se setea)
#   GITOPS_TRUNK_BRANCH   rama trunk (default: main)
#   GITOPS_SCOPE          scope de paquetes internos (default: @deacero)

set -euo pipefail

CMD="${1:-help}"

die()  { printf "ERROR: %b\n" "$*" >&2; exit 1; }
info() { echo "→ $*"; }
ok()   { echo "✓ $*"; }
warn() { echo "⚠ $*"; }

# ── configuración con defaults ─────────────────────────────────────────────

# Trunk branch configurable — soporta repos que aún usan master
trunk_branch() {
  echo "${GITOPS_TRUNK_BRANCH:-main}"
}

# Detecta el remote autoritativo sin asumir "origin"
# Prioridad: GITOPS_REMOTE env > deacero > origin > upstream > primero disponible
resolve_remote() {
  if [[ -n "${GITOPS_REMOTE:-}" ]]; then
    git remote get-url "$GITOPS_REMOTE" &>/dev/null \
      || die "GITOPS_REMOTE='$GITOPS_REMOTE' no existe en este repo"
    echo "$GITOPS_REMOTE"
    return
  fi
  for r in deacero origin upstream; do
    if git remote get-url "$r" &>/dev/null; then
      echo "$r"
      return
    fi
  done
  # fallback: primer remote disponible
  local first; first=$(git remote | head -1)
  [[ -n "$first" ]] && echo "$first" && return
  die "no se encontró ningún remote. Setea GITOPS_REMOTE=<nombre>"
}

# ── helpers de validación ──────────────────────────────────────────────────

# Detecta si un commit SHA (o su patch equivalente) ya está aplicado a una rama.
# Cubre dos flujos: merge directo (merge-base ancestry) y cherry-pick (patch-id).
is_applied_to() {
  local sha="$1" target="$2"
  # 1. Ancestro directo (merge workflow)
  git merge-base --is-ancestor "$sha" "$target" 2>/dev/null && return 0
  # 2. Patch-id equivalente (cherry-pick workflow)
  #    git cherry <upstream> <head> [<limit>]
  #    Usar sha^ como límite para restringir al commit exacto, no toda su rama
  local parent; parent=$(git rev-parse "${sha}^" 2>/dev/null) || return 1
  [[ "$(git cherry "$target" "$sha" "$parent" 2>/dev/null)" == "- "* ]] && return 0
  return 1
}

require_arg() {
  [[ -n "${1:-}" ]] || die "$2"
}

require_clean() {
  [[ -z "$(git status --porcelain)" ]] \
    || die "hay cambios sin commitear — haz commit o stash primero"
}

require_trunk() {
  local branch; branch=$(git rev-parse --abbrev-ref HEAD)
  local trunk; trunk=$(trunk_branch)
  [[ "$branch" == "$trunk" ]] \
    || die "este comando requiere estar en $(trunk_branch) (estás en: $branch)"
}

_project_path() {
  local proj="$1"
  if [[ -n "${GITOPS_PROJECT_MAP:-}" ]]; then
    local entry
    entry=$(echo "$GITOPS_PROJECT_MAP" | tr ',' '\n' | grep "^${proj}:" | head -1)
    [[ -n "$entry" ]] && echo "${entry#*:}" && return
  fi
  local base="${GITOPS_PROJECTS_DIR:-projects}"
  [[ -z "$base" || "$base" == "." ]] && echo "$proj" || echo "$base/$proj"
}

require_project_dir() {
  local proj="$1"
  local proj_dir; proj_dir=$(_project_path "$proj")
  # Verificar en git tree (funciona aunque sparse checkout lo oculte) o en disco
  [[ -n "$(git ls-tree -d HEAD "$proj_dir" 2>/dev/null)" ]] || [[ -d "$proj_dir" ]] \
    || die "no existe $proj_dir"
}

# Verifica que el repo tiene historial completo (CI shallow clone fix)
require_full_history() {
  if [[ -f .git/shallow ]]; then
    info "shallow clone detectado — fetching full history"
    local remote; remote=$(resolve_remote)
    git fetch --unshallow "$remote" --quiet
  fi
}

require_tag_exists() {
  local tag="$1"
  local remote; remote=$(resolve_remote)
  git fetch "$remote" --tags --quiet
  git tag -l "$tag" | grep -q . \
    || die "tag no encontrado: $tag"
}

require_tag_not_exists() {
  local tag="$1"
  git tag -l "$tag" | grep -q . \
    && die "el tag $tag ya existe — los tags son inmutables"
  return 0
}

require_semver() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "versión inválida: $1 — usa formato 1.4.2"
}

# ── detección de dependencias shared (multi-stack) ─────────────────────────

# Lee qué libs de shared/ consume un proyecto.
# Soporta Node.js (package.json) y Python (pyproject.toml).
# Usa grep -o (POSIX) para compatibilidad con macOS y Alpine/BusyBox.
shared_deps_of() {
  local proj="$1"
  local scope="${GITOPS_SCOPE:-@deacero}"
  # convierte @deacero → deacero para uso en sed y Python
  local scope_name="${scope#@}"
  local proj_dir; proj_dir=$(_project_path "$proj")

  # Node.js: detectar "@deacero/libname" en package.json
  if [[ -f "$proj_dir/package.json" ]]; then
    grep -o "\"${scope}/[^\"]*\"" "$proj_dir/package.json" 2>/dev/null \
      | sed "s/\"${scope}\///;s/\"//" \
      | while read -r lib; do
          [[ -n "$lib" && -d "shared/libs/$lib" ]] && echo "shared/libs/$lib"
        done
  fi

  # Python: detectar "deacero-libname" en pyproject.toml dependencies
  if [[ -f "$proj_dir/pyproject.toml" ]]; then
    grep -oE "${scope_name}-[a-z][a-z0-9-]+" "$proj_dir/pyproject.toml" 2>/dev/null \
      | sed "s/${scope_name}-//" \
      | while read -r lib; do
          [[ -n "$lib" && -d "shared/libs/$lib" ]] && echo "shared/libs/$lib"
        done
  fi
}

# ── sparse checkout ────────────────────────────────────────────────────────

_sparse_set() {
  # Recibe paths separados por espacio o newline, los deduplica y aplica
  local paths="$*"
  local unique_paths
  unique_paths=$(echo "$paths" \
    | tr ' ' '\n' \
    | grep -v '^$' \
    | awk '!seen[$0]++' \
    | tr '\n' ' ')

  git sparse-checkout init --cone 2>/dev/null || true
  # shellcheck disable=SC2086
  git sparse-checkout set $unique_paths
}

cmd_work() {
  local projects=("${@}")
  [[ ${#projects[@]} -ge 1 ]] \
    || die "especifica al menos un proyecto\n  uso: release.sh work <proyecto> [proyecto-b...]"

  for proj in "${projects[@]}"; do
    require_project_dir "$proj"
  done

  local paths="scripts"
  for proj in "${projects[@]}"; do
    paths="$paths $(_project_path "$proj")"
    while IFS= read -r dep; do
      [[ -n "$dep" ]] && paths="$paths $dep"
    done < <(shared_deps_of "$proj")
  done

  _sparse_set "$paths"

  info "sparse activo:"
  git sparse-checkout list | grep -v '^$' | sed 's/^/  /'

  if [[ ${#projects[@]} -gt 1 ]]; then
    echo ""
    warn "contrato de rama de integración:"
    warn "  branch:  integration/refactor-<descripcion>"
    warn "  vida:    máximo 2 semanas"
    warn "  merge:   --no-ff, nunca squash"
    warn "  al terminar: git push $(resolve_remote) --delete integration/<nombre>"
  fi
}

cmd_expand() {
  git sparse-checkout disable
  ok "checkout completo activado"
  info "vuelve con: ./scripts/release.sh work <proyecto>"
}

cmd_status() {
  local remote; remote=$(resolve_remote)
  local trunk; trunk=$(trunk_branch)
  local branch; branch=$(git rev-parse --abbrev-ref HEAD)

  echo "remote:  $remote  ($(git remote get-url "$remote" 2>/dev/null || echo 'no url'))"
  echo "trunk:   $trunk"
  echo "branch:  $branch"
  echo "sha:     $(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
  echo ""

  if git config core.sparseCheckout 2>/dev/null | grep -q true; then
    info "sparse checkout activo — paths en disco:"
    git sparse-checkout list | grep -v '^$' | sed 's/^/  /'
  else
    info "checkout completo (sin sparse)"
  fi
}

cmd_add() {
  local path="${1:-}"
  require_arg "$path" "especifica el path a agregar\n  uso: release.sh add <path|proyecto>"

  # Normalizar: si no empieza con shared/ ni scripts, resolver via _project_path
  if [[ "$path" != shared/* && "$path" != scripts* ]]; then
    path=$(_project_path "$path")
  fi

  # Inicializar sparse si no estaba activo
  if ! git config core.sparseCheckout 2>/dev/null | grep -q true; then
    warn "sparse checkout no estaba activo — inicializando"
    git sparse-checkout init --cone 2>/dev/null || true
  fi

  # Advertir si el path no existe en el árbol del repo
  git ls-tree -d HEAD "$path" &>/dev/null \
    || warn "$path no existe en el árbol de HEAD — se agrega de todas formas"

  git sparse-checkout add "$path"
  ok "agregado: $path"
  info "paths activos:"
  git sparse-checkout list | grep -v '^$' | sed 's/^/  /'
}

cmd_drop() {
  local path="${1:-}"
  require_arg "$path" "especifica el path a quitar\n  uso: release.sh drop <path|proyecto>"

  # Normalizar igual que cmd_add
  if [[ "$path" != shared/* && "$path" != scripts* ]]; then
    path=$(_project_path "$path")
  fi

  if ! git config core.sparseCheckout 2>/dev/null | grep -q true; then
    die "sparse checkout no está activo — nada que quitar"
  fi

  local tmp; tmp=$(mktemp)
  # Filtra el path del set actual y reaplica (grep -Fx: fixed-string, exact line)
  git sparse-checkout list | grep -Fxv "$path" > "$tmp"

  if [[ ! -s "$tmp" ]]; then
    rm -f "$tmp"
    die "quitando $path quedaría un sparse set vacío — usa expand si quieres checkout completo"
  fi

  # Reconstruir el set desde el archivo (compatible con git < 2.35 sin 'remove')
  _sc_paths=()
  while IFS= read -r _p; do _sc_paths+=("$_p"); done < "$tmp"
  git sparse-checkout set "${_sc_paths[@]}"
  rm -f "$tmp"

  ok "quitado: $path"
  info "paths activos:"
  git sparse-checkout list | grep -v '^$' | sed 's/^/  /'
}

cmd_sync() {
  require_clean

  local remote; remote=$(resolve_remote)
  local trunk; trunk=$(trunk_branch)
  local branch; branch=$(git rev-parse --abbrev-ref HEAD)

  require_full_history
  git fetch "$remote" "$trunk" --quiet
  info "sincronizando $branch contra $remote/$trunk"

  if [[ "$branch" == "$trunk" ]]; then
    git pull --rebase "$remote" "$trunk"
    ok "$trunk actualizado — $(git rev-parse --short HEAD)"
    return
  fi

  if git rebase "$remote/$trunk"; then
    ok "rebase exitoso"
  else
    echo ""
    warn "conflictos al hacer rebase:"
    git diff --name-only --diff-filter=U 2>/dev/null | sed 's/^/  /' || true
    echo ""
    if git config core.sparseCheckout 2>/dev/null | grep -q true; then
      warn "si el conflicto es en un archivo fuera de tu sparse:"
      warn "  1. ./scripts/release.sh expand"
      warn "  2. resuelve el conflicto"
      warn "  3. git add <archivo>"
      warn "  4. git rebase --continue"
      warn "  5. ./scripts/release.sh work <tu-proyecto>"
    fi
    die "rebase pausado — resuelve los conflictos y corre: git rebase --continue"
  fi
}

# ── tags y releases ────────────────────────────────────────────────────────

cmd_tag() {
  local project="${1:-}"; local version="${2:-}"
  require_arg "$project" "proyecto requerido\n  uso: release.sh tag <proyecto> <1.4.2>"
  require_arg "$version" "versión requerida\n  uso: release.sh tag <proyecto> <1.4.2>"
  require_project_dir "$project"
  require_semver "$version"
  require_trunk
  require_clean

  local remote; remote=$(resolve_remote)
  local trunk; trunk=$(trunk_branch)

  require_full_history
  git fetch "$remote" "$trunk" --tags --quiet

  [[ "$(git rev-parse HEAD)" == "$(git rev-parse "$remote/$trunk")" ]] \
    || die "$trunk local no está sincronizado — corre: ./scripts/release.sh sync"

  local tag="${project}@${version}"
  require_tag_not_exists "$tag"

  # Bloquear si hay hotfixes pendientes de cherry-pick
  local pendiente=""
  local tmp; tmp=$(mktemp)
  git tag -l "${project}@*hotfix*" > "$tmp" 2>/dev/null || true
  while IFS= read -r t; do
    [[ -z "$t" ]] && continue
    local sha; sha=$(git rev-parse "${t}^{commit}" 2>/dev/null || echo "")
    [[ -z "$sha" ]] && continue
    if ! is_applied_to "$sha" "$remote/$trunk"; then
      pendiente="$t"
    fi
  done < "$tmp"
  rm -f "$tmp"

  [[ -z "$pendiente" ]] \
    || die "cherry-pick pendiente: $pendiente\n  corre: ./scripts/release.sh cherry $project <version>"

  git tag -a "$tag" -m "Release $tag"
  git push "$remote" "$tag"

  ok "tag creado: $tag → $(git rev-parse --short HEAD)"
  info "el pipeline de Bitbucket detectará el tag y desplegará a prod"
}

cmd_hotfix() {
  local project="${1:-}"; local version="${2:-}"; local nombre="${3:-fix}"
  require_arg "$project" "proyecto requerido\n  uso: release.sh hotfix <proyecto> <1.4.2> [nombre]"
  require_arg "$version" "versión requerida (la que está en prod)"
  require_project_dir "$project"
  require_semver "$version"
  require_clean

  local base_tag="${project}@${version}"
  require_tag_exists "$base_tag"

  local branch="hotfix/${project}/${nombre}"

  # Verificar que la rama no existe ya
  git rev-parse --verify "refs/heads/$branch" &>/dev/null \
    && die "la rama $branch ya existe — ¿hotfix anterior sin terminar?\n  continúa en esa rama o bórrala primero"

  # Configurar sparse si está activo
  if git config core.sparseCheckout 2>/dev/null | grep -q true; then
    info "configurando sparse para $project"
    local proj_dir; proj_dir=$(_project_path "$project")
    local paths="scripts $proj_dir"
    while IFS= read -r dep; do
      [[ -n "$dep" ]] && paths="$paths $dep"
    done < <(shared_deps_of "$project")
    _sparse_set "$paths"
  fi

  git checkout -b "$branch" "$base_tag"

  ok "branch creada: $branch"
  info "base: $base_tag ($(git rev-parse --short "${base_tag}^{commit}"))"
  echo ""
  warn "contrato de hotfix:"
  warn "  1. haz SOLO el fix — nada más"
  warn "  2. conventional commit: fix($project): descripción"
  warn "  3. git push $(resolve_remote) $branch"
  warn "  4. el pipeline crea el tag y deploya a prod"
  warn "  5. OBLIGATORIO: ./scripts/release.sh cherry $project $version"
}

cmd_cherry() {
  local project="${1:-}"; local version="${2:-}"
  require_arg "$project" "proyecto requerido\n  uso: release.sh cherry <proyecto> <1.4.2>"
  require_arg "$version" "versión requerida"
  require_clean

  local remote; remote=$(resolve_remote)
  local trunk; trunk=$(trunk_branch)

  require_full_history
  git fetch "$remote" --tags --quiet

  # Recopilar hotfix tags ordenados ascendentemente por número de hotfix
  # Formato: proyecto@1.4.2-hotfix.N → extraer N, ordenar numéricamente, reconstruir
  local tmp; tmp=$(mktemp)
  git tag -l "${project}@${version}-hotfix.*" > "$tmp" 2>/dev/null || true

  local hotfix_tags=()
  while IFS= read -r n; do
    [[ -n "$n" ]] && hotfix_tags+=("${project}@${version}-hotfix.${n}")
  done < <(sed 's/.*\.//' "$tmp" | sort -n)
  rm -f "$tmp"

  [[ ${#hotfix_tags[@]} -gt 0 ]] \
    || die "no hay hotfix tags para ${project}@${version}\n  usa: ./scripts/release.sh check $project"

  info "hotfixes encontrados para ${project}@${version}:"
  for t in "${hotfix_tags[@]}"; do info "  $t"; done
  echo ""

  git checkout "$trunk"
  git pull --rebase "$remote" "$trunk"

  local cherry_count=0

  for t in "${hotfix_tags[@]}"; do
    local sha; sha=$(git rev-parse "${t}^{commit}" 2>/dev/null || echo "")
    [[ -z "$sha" ]] && warn "tag $t no encontrado — skip" && continue

    # Skip si ya está integrado en trunk (por ancestry o por patch-id equivalente)
    if is_applied_to "$sha" "$(git rev-parse HEAD)"; then
      ok "$t ya está en $trunk — skip"
      continue
    fi

    info "cherry-pick: $t ($(git rev-parse --short "$sha"))"

    if git cherry-pick "$sha" 2>/dev/null; then
      ok "$t aplicado"
      cherry_count=$((cherry_count + 1))
    elif [[ -f .git/CHERRY_PICK_HEAD ]]; then
      # Distinguir commit vacío (ya aplicado de otra forma) de conflicto real
      local conflicts; conflicts=$(git diff --name-only --diff-filter=U 2>/dev/null)
      if [[ -z "$conflicts" ]]; then
        # Commit vacío: los cambios ya existen en trunk — skip
        git cherry-pick --skip 2>/dev/null || true
        ok "$t vacío post-apply (cambios ya en $trunk) — skip"
      else
        echo ""
        warn "conflictos en cherry-pick de $t:"
        echo "$conflicts" | sed 's/^/  /'
        echo ""
        warn "resuelve y continúa:"
        warn "  git add <archivos>"
        warn "  git cherry-pick --continue"
        warn "  git push $remote $trunk"
        warn "  luego re-corre: ./scripts/release.sh cherry $project $version"
        die "cherry-pick pausado en $t — hotfixes posteriores NO procesados"
      fi
    fi
  done

  if [[ $cherry_count -gt 0 ]]; then
    git push "$remote" "$trunk"
    ok "$cherry_count hotfix(es) absorbidos en $trunk"
    ok "SHA en $trunk: $(git rev-parse --short HEAD)"
    echo ""
  fi

  # Verificación post cherry-pick
  cmd_check "$project"
}

cmd_check() {
  local project="${1:-}"
  local remote; remote=$(resolve_remote)
  local trunk; trunk=$(trunk_branch)

  require_full_history
  git fetch "$remote" --tags --quiet 2>/dev/null

  local pattern
  [[ -n "$project" ]] \
    && pattern="${project}@*hotfix*" \
    || pattern="*@*hotfix*"

  info "verificando hotfixes pendientes${project:+ de $project}..."

  local tmp; tmp=$(mktemp)
  git tag -l "$pattern" > "$tmp" 2>/dev/null || true

  local found=0
  while IFS= read -r t; do
    [[ -z "$t" ]] && continue
    local sha; sha=$(git rev-parse "${t}^{commit}" 2>/dev/null || echo "")
    [[ -z "$sha" ]] && continue
    if ! is_applied_to "$sha" "$remote/$trunk"; then
      warn "PENDIENTE: $t"
      local proj_from_tag ver_from_tag
      proj_from_tag=$(echo "$t" | cut -d'@' -f1)
      ver_from_tag=$(echo "$t" | cut -d'@' -f2 | grep -oE '^[0-9]+\.[0-9]+\.[0-9]+')
      warn "  fix: ./scripts/release.sh cherry $proj_from_tag $ver_from_tag"
      found=1
    fi
  done < "$tmp"
  rm -f "$tmp"

  [[ $found -eq 0 ]] && ok "sin hotfixes pendientes"
}

cmd_promote() {
  local project="${1:-}"; local version="${2:-}"; local env="${3:-}"
  require_arg "$project" "proyecto requerido\n  uso: release.sh promote <proyecto> <1.4.2> <dev|rc>"
  require_arg "$version" "versión requerida"
  require_arg "$env" "ambiente requerido (dev|rc)\n  nota: promote es escape hatch para rollback/re-promote, no para el flujo CI normal"
  require_semver "$version"

  [[ "$env" == "dev" || "$env" == "rc" ]] \
    || die "ambiente inválido: $env — solo se permite dev|rc (promote no maneja el tag final de prod)"

  # rc solo desde trunk
  if [[ "$env" == "rc" ]]; then
    require_trunk
  fi

  require_clean

  local remote; remote=$(resolve_remote)
  local trunk; trunk=$(trunk_branch)

  require_full_history
  git fetch "$remote" --tags --quiet

  # Autoincremento: buscar el N más alto del patrón proyecto@ver-env.N
  local tmp; tmp=$(mktemp)
  git tag -l "${project}@${version}-${env}.*" > "$tmp" 2>/dev/null || true
  local last_n=0
  while IFS= read -r n; do
    [[ -n "$n" && "$n" -gt "$last_n" ]] && last_n="$n"
  done < <(sed 's/.*\.//' "$tmp" | grep -E '^[0-9]+$')
  rm -f "$tmp"

  local N=$(( last_n + 1 ))
  local new_tag="${project}@${version}-${env}.${N}"

  require_tag_not_exists "$new_tag"

  git tag -a "$new_tag" -m "Promote $new_tag (manual)"
  git push "$remote" "$new_tag"

  ok "tag creado: $new_tag → $(git rev-parse --short HEAD)"
  warn "promote es un escape hatch — el flujo normal usa el pipeline CI para crear estos tags"
}

cmd_versions() {
  local project="${1:-}"
  local remote; remote=$(resolve_remote)

  git fetch "$remote" --tags --quiet 2>/dev/null || true

  local pattern
  [[ -n "$project" ]] && pattern="${project}@*" || pattern="*@*"

  local tags; tags=$(git tag -l "$pattern" | sort -V)
  [[ -z "$tags" ]] && { info "sin tags${project:+ para $project}"; return; }

  echo ""
  printf "%-40s  %s\n" "TAG" "AMBIENTE"
  printf "%-40s  %s\n" "---" "--------"
  while IFS= read -r t; do
    local env
    if   echo "$t" | grep -qE '\-dev\.[0-9]+$';    then env="DEV"
    elif echo "$t" | grep -qE '\-rc\.[0-9]+$';     then env="QA"
    elif echo "$t" | grep -qE '\-hotfix\.[0-9]+$'; then env="HOTFIX"
    else                                                 env="PROD"
    fi
    printf "%-40s  %s\n" "$t" "$env"
  done <<< "$tags"
  echo ""
}

# ── router ─────────────────────────────────────────────────────────────────

# Shift el CMD para que las funciones reciban argumentos desde $1
shift 2>/dev/null || true

case "$CMD" in
  work)       cmd_work "$@" ;;
  expand)     cmd_expand ;;
  status)     cmd_status ;;
  add)        cmd_add "$@" ;;
  drop)       cmd_drop "$@" ;;
  init)       cmd_work "$@" ;;
  init-multi) cmd_work "$@" ;;
  sync)       cmd_sync ;;
  tag)        cmd_tag "$@" ;;
  hotfix)     cmd_hotfix "$@" ;;
  cherry)     cmd_cherry "$@" ;;
  check)      cmd_check "$@" ;;
  promote)    cmd_promote "$@" ;;
  versions)   cmd_versions "$@" ;;
  help|*)
    cat <<'EOF'
Uso: release.sh <comando> [args]

  ENTORNO
  work    <proy> [proy-b...]   configurar sparse checkout
  expand                       checkout completo (emergencias)
  sync                         rebase diario contra trunk
  status                       estado actual: remote, trunk, sparse paths
  add     <path|proyecto>      agregar path al sparse set actual
  drop    <path|proyecto>      quitar path del sparse set
  init    <proyecto>           alias de work (un proyecto)
  init-multi <proy...>         alias de work (multi-proyecto)

  RELEASES
  tag     <proyecto> <1.4.2>            crear tag de release desde trunk
  hotfix  <proyecto> <1.4.2> [nombre]   branch de hotfix desde tag de prod
  cherry  <proyecto> <1.4.2>            cherry-pick del/los hotfix a trunk
  check    [proyecto]                    hotfixes pendientes (sin proy: todos)
  promote  <proyecto> <1.4.2> <dev|rc>   escape hatch: re-promote / rollback
  versions [proyecto]                    listar tags por ambiente

  VARIABLES DE ENTORNO
  GITOPS_REMOTE         remote autoritativo (auto-detect: deacero > origin > upstream)
  GITOPS_TRUNK_BRANCH   rama trunk (default: main)
  GITOPS_SCOPE          scope de paquetes internos (default: @deacero)
EOF
    ;;
esac
