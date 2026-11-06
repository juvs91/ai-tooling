#!/usr/bin/env bash
# scripts/release.sh
# distributable: true
#
# GitOps Monorepo — Company
# Ref: docs/adr/ADR-0007-gitops-monorepo-trunk-based.md
# Ref: docs/adr/ADR-0010-manifiesto-fuente-de-verdad-version.md (bump + tag; ver commons)
#
# Uso:
#   ./scripts/release.sh work    <proy> [proy-b...]   sparse checkout
#   ./scripts/release.sh expand                       checkout completo
#   ./scripts/release.sh sync                         rebase diario
#   ./scripts/release.sh status                       estado del entorno
#   ./scripts/release.sh add     <path>               agregar al sparse set
#   ./scripts/release.sh drop    <path>               quitar del sparse set
#   ./scripts/release.sh consumers <lib>              qué proyectos usan una lib (where-used)
#   ./scripts/release.sh init    <proy>               alias de work (un proyecto)
#   ./scripts/release.sh init-multi <proy...>         alias de work (multi)
#   ./scripts/release.sh bump    <proyecto> <1.4.2>   bump versión en el manifiesto (comitear vía PR)
#   ./scripts/release.sh tag     <proyecto> [1.4.2]   tag de release del commit de <remote>/<trunk> (versión de su manifiesto)
#   ./scripts/release.sh hotfix  <proyecto> <1.4.2> [nombre]  branch desde prod
#   ./scripts/release.sh cherry  <proyecto> <1.4.2>   cherry-pick a trunk
#   ./scripts/release.sh check   [proyecto]            hotfixes pendientes
#   ./scripts/release.sh promote <proyecto> <dev|rc> [--at <ref>]  tag -dev.N/-rc.N de <remote>/<trunk>; --at: rollback (ADR-0082)
#
# tag y promote etiquetan el commit de <remote>/<trunk>, nunca HEAD: corren desde cualquier rama o worktree y solo
# liberan lo que ya entró a trunk (ADR-0082). La versión sale del manifiesto de ese commit.
#
# Variables de entorno (también se leen, sin ejecutarse, de .gitops-env en la raíz del repo; el entorno gana):
#   GITOPS_REMOTE         remote autoritativo (auto-detect si no se setea)
#   GITOPS_TRUNK_BRANCH   rama trunk (default: main)
#   GITOPS_PROJECTS_DIR   carpeta base de los proyectos (default: projects; "." = raíz)
#   GITOPS_PROJECT_MAP    nombre:carpeta,nombre:carpeta — gana sobre GITOPS_PROJECTS_DIR
#   GITOPS_SHARED_DIR     carpeta de libs compartidas (default: shared/libs; Azure: _shared/libs)
#
# Qué libs usa un proyecto sale de su manifiesto vía scripts/deps.py (ADR-0080).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# .gitops-env de la raíz del repo: se carga solo, sin pisar lo que ya trae el entorno. Solo lee líneas
# GITOPS_*=valor (nunca las ejecuta).
_load_gitops_env() {
  local root line key
  root=$(git rev-parse --show-toplevel 2>/dev/null) || return 0
  [[ -f "$root/.gitops-env" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^(GITOPS_[A-Z_]+)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    [[ -n "${!key+x}" ]] || export "$key=${BASH_REMATCH[2]}"
  done < "$root/.gitops-env"
}
_load_gitops_env

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
# Prioridad: GITOPS_REMOTE env (setéalo en .env si tu remote autoritativo no es
# "origin"/"upstream" — ej. GITOPS_REMOTE=deacero) > origin > upstream > primero disponible
resolve_remote() {
  if [[ -n "${GITOPS_REMOTE:-}" ]]; then
    git remote get-url "$GITOPS_REMOTE" &>/dev/null \
      || die "GITOPS_REMOTE='$GITOPS_REMOTE' no existe en este repo"
    echo "$GITOPS_REMOTE"
    return
  fi
  for r in origin upstream; do
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

# Raíz de las libs compartidas: shared/libs → shared, _shared/libs → _shared (ADR-0080)
_shared_root() {
  local d="${GITOPS_SHARED_DIR:-shared/libs}"
  echo "${d%%/*}"
}

# Un path ya resuelto (libs compartidas o scripts) no se traduce como nombre de proyecto
_is_literal_path() {
  local p="$1" root; root=$(_shared_root)
  [[ "$p" == "$root"/* || "$p" == scripts* ]]
}

_project_path() {
  local proj="$1"
  # Path literal (ya resuelto) — mismo criterio que ya usan cmd_add/cmd_drop: no le
  # antepongas el projects-dir. Sin esto, cmd_work/init-multi rompen el escenario
  # "solo shared" (equipo Platform): init-multi shared/libs/auth resolvía a
  # projects/shared/libs/auth (inexistente). Ver ADR-0049.
  if _is_literal_path "$proj"; then
    echo "$proj"
    return
  fi
  if [[ -n "${GITOPS_PROJECT_MAP:-}" ]]; then
    local entry
    entry=$(echo "$GITOPS_PROJECT_MAP" | tr ',' '\n' | grep "^${proj}:" | head -1)
    [[ -n "$entry" ]] && echo "${entry#*:}" && return
  fi
  local base="${GITOPS_PROJECTS_DIR:-projects}"
  [[ -z "$base" || "$base" == "." ]] && echo "$proj" || echo "$base/$proj"
}

# ── manifiesto de versión — fuente de verdad (Ref: ADR-0010 de commons) ────
# Detecta el tipo de manifiesto de un proyecto sin hardcodear nombres —
# generaliza a futuras librerías con solo agregar el archivo correspondiente.
_manifest_kind_and_path() {
  local proj_dir; proj_dir=$(_project_path "$1")
  if   [[ -f "$proj_dir/pyproject.toml" ]]; then echo "python $proj_dir/pyproject.toml"
  elif [[ -f "$proj_dir/package.json"  ]]; then echo "node   $proj_dir/package.json"
  elif [[ -f "$proj_dir/VERSION"       ]]; then echo "go     $proj_dir/VERSION"
  else die "no se pudo determinar el tipo de manifiesto para $1 (busqué pyproject.toml, package.json, VERSION)"
  fi
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
  # --git-path: correcto desde subdirectorios y worktrees (.git es archivo ahí) — ADR-0073.
  # Relativo al cwd, válido para -f; sin --path-format=absolute (exige git ≥2.31).
  if [[ -f "$(git rev-parse --git-path shallow)" ]]; then
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
# Intérprete para scripts/deps.py (Python >= 3.11, por tomllib). El python3 de macOS
# es 3.9: se prueban versiones explícitas y, si no hay, uv descarga uno (ADR-0080).
_python() {
  local c
  for c in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 \
       && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      echo "$c"
      return
    fi
  done
  if command -v uv >/dev/null 2>&1; then
    echo "uv run --no-project --python 3.12 python"
    return
  fi
  die "se necesita Python >= 3.11 para leer dependencias (scripts/deps.py)\n  instala uno: brew install python@3.12  —o—  instala uv: https://docs.astral.sh/uv/"
}

# deps.py con el intérprete de _python (la salida de _python se parte en palabras a propósito)
_deps() {
  # `|| exit 1` explícito: dentro de $(...) bash no hereda set -e
  local py; py=$(_python) || exit 1
  # shellcheck disable=SC2086
  $py "$SCRIPT_DIR/deps.py" "$@"
}

shared_deps_of() {
  # Libs que usa el proyecto, leídas de su manifiesto en git (no del disco: funciona
  # aunque el proyecto aún no esté en el sparse set). Ref: ADR-0080.
  local proj_dir; proj_dir=$(_project_path "$1")
  _deps libs "$proj_dir" \
    || die "no se pudieron leer las dependencias de $proj_dir (scripts/deps.py)"
}

cmd_consumers() {
  local lib="${1:-}"
  require_arg "$lib" "especifica la lib\n  uso: release.sh consumers <lib>  (ej: $(_shared_root)/libs/auth)"
  local out
  out=$(_deps consumers "${lib%/}") \
    || die "no se pudo calcular quién usa $lib"
  if [[ -z "$out" ]]; then
    ok "ningún proyecto declara $lib en su manifiesto"
    return
  fi
  info "usan $lib:"
  echo "$out" | sed 's/^/  /'
  info "para trabajar con la lib y todos: ./scripts/release.sh work ${lib%/} $(echo "$out" | tr '\n' ' ')"
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

  # set activa sparse + cone por sí solo (init está deprecado en git)
  # shellcheck disable=SC2086
  git sparse-checkout set --cone $unique_paths
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
    local deps; deps=$(shared_deps_of "$proj") || exit 1
    paths="$paths $deps"
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

  echo ""
  info "worktrees:"
  git worktree list

  local prunable
  prunable=$(git worktree prune --dry-run 2>&1)
  if [[ -n "$prunable" ]]; then
    echo ""
    warn "worktrees huérfanos (directorio ya no existe en disco):"
    echo "$prunable" | sed 's/^/  /'
    warn "limpiar con: ./scripts/release.sh worktree prune"
  fi
}

cmd_add() {
  local path="${1:-}"
  require_arg "$path" "especifica el path a agregar\n  uso: release.sh add <path|proyecto>"

  # Normalizar: si no es path de libs ni scripts, resolver via _project_path
  if ! _is_literal_path "$path"; then
    path=$(_project_path "$path")
  fi

  # Inicializar sparse si no estaba activo
  if ! git config core.sparseCheckout 2>/dev/null | grep -q true; then
    warn "sparse checkout no estaba activo — inicializando"
    git sparse-checkout set --cone
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
  if ! _is_literal_path "$path"; then
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

# ── commit, proyecto y versión de un release ───────────────────────────────
# Un release etiqueta el commit de <remote>/<trunk>, nunca HEAD: funciona desde cualquier worktree o rama, con el
# árbol sucio o sin hacer checkout de trunk, y solo etiqueta lo que ya está en el remote. --at <ref> elige otro
# commit (rollback), que debe estar en trunk.
_release_commit() {
  local at="${1:-}" remote trunk sha
  remote=$(resolve_remote); trunk=$(trunk_branch)
  require_full_history
  git fetch "$remote" "+refs/heads/$trunk:refs/remotes/$remote/$trunk" --tags --quiet \
    || die "no se pudo traer $remote/$trunk"
  if [[ -z "$at" ]]; then
    git rev-parse "refs/remotes/$remote/$trunk^{commit}"
    return
  fi
  sha=$(git rev-parse --verify --quiet "$at^{commit}") || die "no existe $at"
  git merge-base --is-ancestor "$sha" "refs/remotes/$remote/$trunk" \
    || die "$at no está en $remote/$trunk: solo se libera lo que ya entró a $trunk"
  echo "$sha"
}

# El proyecto existe en ese commit (no en el árbol de trabajo)
_require_project_at() {
  local proj_dir; proj_dir=$(_project_path "$1")
  [[ -n "$(git ls-tree -d "$2" -- "$proj_dir" 2>/dev/null)" ]] \
    || die "no existe $proj_dir en $(git rev-parse --short "$2") — revisa el nombre, GITOPS_PROJECT_MAP o GITOPS_PROJECTS_DIR (.gitops-env)"
}

# Versión del manifiesto en ese commit, sin \r ni espacios
_manifest_version_at() {
  local proj_dir content v
  proj_dir=$(_project_path "$1")
  if content=$(git show "$2:$proj_dir/pyproject.toml" 2>/dev/null); then
    v=$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' <<< "$content" | head -1)
  elif content=$(git show "$2:$proj_dir/package.json" 2>/dev/null); then
    v=$(python3 -I -c 'import json, sys; print(json.load(sys.stdin)["version"])' <<< "$content") || v=""
  elif content=$(git show "$2:$proj_dir/VERSION" 2>/dev/null); then
    v=$(head -1 <<< "$content")
  else
    die "$1 no tiene manifiesto en $(git rev-parse --short "$2") (busqué pyproject.toml, package.json y VERSION en $proj_dir)"
  fi
  v="${v//$'\r'/}"; v="${v//[[:space:]]/}"
  [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "el manifiesto de $1 no tiene una versión de release limpia: '$v'\n  corre: ./scripts/release.sh bump $1 <version> (por PR)"
  echo "$v"
}

# Sube un tag solo; si no entra (alguien subió el mismo nombre, o sin permiso: git dice cuál), lo borra en local
# y falla
_push_tag() {
  git push --quiet "$1" "refs/tags/$2" && return 0
  git tag -d "$2" >/dev/null 2>&1 || true
  return 1
}

# ── tags y releases ────────────────────────────────────────────────────────

# El manifiesto de cada proyecto es la fuente de verdad de la versión (ADR-0010
# de commons, mismo contrato compartido). bump la escribe (vía PR revisado);
# tag la lee y publica — nunca se pasan versiones "a ciegas" como argumento.
cmd_bump() {
  local project="${1:-}"; local version="${2:-}"
  require_arg "$project" "proyecto requerido\n  uso: release.sh bump <proyecto> <1.4.2>"
  require_arg "$version" "versión requerida\n  uso: release.sh bump <proyecto> <1.4.2>"
  require_project_dir "$project"
  require_semver "$version"

  local kind manifest
  read -r kind manifest <<< "$(_manifest_kind_and_path "$project")"
  case "$kind" in
    python)
      # -i.bak + rm: portable entre BSD sed (macOS) y GNU sed (Linux/CI) —
      # sed -i sin sufijo falla en macOS ("unterminated substitute")
      sed -i.bak "s/^version = \".*\"/version = \"$version\"/" "$manifest" && rm -f "$manifest.bak"
      ;;
    node)
      (cd "$(dirname "$manifest")" && npm version "$version" --no-git-tag-version --allow-same-version)
      ;;
    go)
      echo "$version" > "$manifest"
      ;;
  esac

  ok "manifiesto actualizado: $manifest → $version"
  info "siguiente paso:"
  info "  git add $manifest"
  info "  git commit -m \"chore($project): bump version to $version\""
  info "  abre PR hacia trunk como cualquier otro cambio"
}

cmd_tag() {
  local project="${1:-}"; local expected_version="${2:-}"
  require_arg "$project" "proyecto requerido\n  uso: release.sh tag <proyecto> [version-esperada]"

  local remote; remote=$(resolve_remote)
  local trunk; trunk=$(trunk_branch)
  local sha; sha=$(_release_commit)
  _require_project_at "$project" "$sha"
  local version; version=$(_manifest_version_at "$project" "$sha")

  if [[ -n "$expected_version" && "$expected_version" != "$version" ]]; then
    die "el manifiesto tiene $version pero esperabas $expected_version\n  ¿olvidaste bumpear? ./scripts/release.sh bump $project $expected_version"
  fi

  local tag="${project}@${version}"
  require_tag_not_exists "$tag"

  # Bloquear si hay hotfixes pendientes de cherry-pick
  local pendiente="" t hsha
  while IFS= read -r t; do
    [[ -z "$t" ]] && continue
    hsha=$(git rev-parse "${t}^{commit}" 2>/dev/null || echo "")
    [[ -z "$hsha" ]] && continue
    is_applied_to "$hsha" "$sha" || pendiente="$t"
  done < <(git tag -l "${project}@*hotfix*")
  [[ -z "$pendiente" ]] \
    || die "cherry-pick pendiente: $pendiente\n  corre: ./scripts/release.sh cherry $project <version>"

  git tag -a "$tag" -m "Release $tag" "$sha"
  _push_tag "$remote" "$tag" || die "no se pudo subir $tag a $remote (arriba, la razón de git)"

  ok "tag creado: $tag → $(git rev-parse --short "$sha") ($remote/$trunk)"
  info "el pipeline detecta el tag y despliega a prod"
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
    local deps; deps=$(shared_deps_of "$project") || exit 1
    paths="$paths $deps"
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
    elif [[ -f "$(git rev-parse --git-path CHERRY_PICK_HEAD)" ]]; then
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

  [[ $found -eq 0 ]] && ok "sin hotfixes pendientes" || true
}

cmd_promote() {
  local usage="uso: release.sh promote <proyecto> <dev|rc> [--at <ref>]   (o <proyecto> <X.Y.Z> <dev|rc>: la versión debe ser la del manifiesto)"
  local project="${1:-}"; [[ $# -gt 0 ]] && shift
  require_arg "$project" "proyecto requerido\n  $usage"
  local version="" env="" at=""
  if [[ "${1:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then version="$1"; shift; fi
  env="${1:-}"; [[ $# -gt 0 ]] && shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --at) at="${2:-}"; [[ -n "$at" ]] || die "--at necesita un ref"; shift 2 ;;
      *) die "argumento desconocido: $1\n  $usage" ;;
    esac
  done
  require_arg "$env" "ambiente requerido (dev|rc)\n  $usage"
  [[ "$env" == "dev" || "$env" == "rc" ]] \
    || die "ambiente inválido: $env — solo se permite dev|rc (promote no maneja el tag final de prod)"

  local remote; remote=$(resolve_remote)
  local sha; sha=$(_release_commit "$at")
  _require_project_at "$project" "$sha"
  local manifest; manifest=$(_manifest_version_at "$project" "$sha")
  if [[ -n "$version" && "$version" != "$manifest" ]]; then
    die "el manifiesto de $project en $(git rev-parse --short "$sha") dice $manifest, no $version\n  sin versión, promote usa la del manifiesto"
  fi
  version="$manifest"

  # El N sale de los tags del remote (no de los locales); si otro sube el mismo N antes, se recalcula
  local attempt n last_n new_tag
  for attempt in 1 2 3; do
    last_n=0
    while IFS= read -r n; do
      [[ "$n" =~ ^[0-9]+$ && "$n" -gt "$last_n" ]] && last_n="$n"
    done < <(git ls-remote --tags "$remote" "refs/tags/${project}@${version}-${env}.*" \
               | sed -e 's#.*refs/tags/##' -e 's/\^{}$//' -e 's/.*\.//')
    new_tag="${project}@${version}-${env}.$((last_n + 1))"
    git tag -d "$new_tag" >/dev/null 2>&1 || true   # uno local con ese nombre que el remote no tiene
    git tag -a "$new_tag" -m "Promote $new_tag" "$sha"
    if _push_tag "$remote" "$new_tag"; then
      ok "tag creado: $new_tag → $(git rev-parse --short "$sha")"
      return 0
    fi
    warn "$new_tag ya lo subió alguien más; recalculo ($attempt/3)"
  done
  die "no se pudo crear un tag ${project}@${version}-${env}.N después de 3 intentos"
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

# ── worktree management (Ref: ADR-0044, auto-sparse Ref: ADR-0048) ────────

# Si el árbol principal tiene sparse-checkout activo, replica el mismo
# sparse-set dentro del worktree nuevo. Portado desde deacero/commons
# (cmd_worktree_add aplicaba esto por nombre de proyecto); aquí se generaliza
# al sparse-set actualmente activo para encajar con el modelo rama-céntrico
# de este release.sh (add/add-branch no reciben un "proyecto", reciben una rama).
_replicate_sparse_checkout() {
  local wt_path="$1"
  git config core.sparseCheckout 2>/dev/null | grep -q true || return 0
  local paths; paths=$(git sparse-checkout list)
  [[ -z "$paths" ]] && return 0
  # shellcheck disable=SC2086
  git -C "$wt_path" sparse-checkout set --cone $paths
  info "sparse checkout replicado en el worktree: $paths"
}

# Checkout principal (primera entrada de `git worktree list`), para que los
# paths por defecto de worktree no dependan del cwd (desde un worktree anidado
# en .claude/worktrees/ creaban el nuevo DENTRO de él) — ADR-0073
_main_worktree_dir() {
  git worktree list --porcelain | awk '/^worktree /{sub(/^worktree /,""); print; exit}'
}

# Rama de worktree "mergeada" solo si tiene ≥1 commit PROPIO contenido en el
# trunk. Un worktree nativo recién creado (sin commits) es ancestro del trunk y
# antes salía como candidato. El commit de creación sale del reflog de la rama;
# sin reflog (expirado, rama traída del remoto) → desconocido, no se lista.
_wt_branch_merged() {
  local br="$1" target="$2" created own
  git rev-parse --verify -q "$br" >/dev/null || return 1
  created=$(git reflog show --format=%H "refs/heads/$br" 2>/dev/null | tail -1)
  [[ -n "$created" ]] || return 1
  own=$(git rev-list --count "$created..$br" 2>/dev/null || echo 0)
  [[ "$own" -ge 1 ]] || return 1
  git merge-base --is-ancestor "$br" "$target" 2>/dev/null
}

cmd_worktree() {
  local sub="${1:-list}"
  shift || true

  case "$sub" in
    list)
      git worktree list
      ;;

    add)
      local branch="${1:-}"
      require_arg "$branch" "rama requerida\n  uso: release.sh worktree add <rama> [<path>]"
      local wt_path="${2:-$(_main_worktree_dir)/../${branch//\//-}}"
      git worktree add "$wt_path" "$branch"
      _replicate_sparse_checkout "$wt_path"
      ok "worktree creado en: $wt_path"
      ;;

    add-branch)
      local branch="${1:-}"
      require_arg "$branch" "nueva rama requerida\n  uso: release.sh worktree add-branch <rama> [<path>] [<base>]"
      local wt_path="${2:-$(_main_worktree_dir)/../${branch//\//-}}"
      local base="${3:-$(trunk_branch)}"
      git worktree add "$wt_path" -b "$branch" "$base"
      _replicate_sparse_checkout "$wt_path"
      ok "worktree creado en: $wt_path (desde $base)"
      ;;

    rm|remove)
      local wt_path="${1:-}"
      require_arg "$wt_path" "path del worktree requerido\n  uso: release.sh worktree rm <path>"
      git worktree remove "$wt_path"
      ok "worktree eliminado: $wt_path"
      ;;

    prune)
      git worktree prune --verbose
      ok "prune completado"
      ;;

    clean)
      local trunk; trunk=$(trunk_branch)
      local remote; remote=$(resolve_remote)
      git fetch "$remote" "$trunk" --quiet 2>/dev/null || true

      info "candidatos a limpiar (ramas con commits propios ya mergeados a $trunk):"
      local found=0 wt_path="" wt_branch="" line
      while IFS= read -r line; do
        case "$line" in
          "worktree "*) wt_path="${line#worktree }"; wt_branch="" ;;
          "branch refs/heads/"*) wt_branch="${line#branch refs/heads/}" ;;
          "")
            if [[ -n "$wt_branch" ]] && _wt_branch_merged "$wt_branch" "$remote/$trunk"; then
              warn "  $wt_path  [$wt_branch]"
              found=1
            fi
            wt_path=""; wt_branch=""
            ;;
        esac
      done < <(git worktree list --porcelain | awk 'NR==1,/^$/{next} {print} END{print ""}')

      [[ $found -eq 0 ]] && ok "ningún candidato" || true
      info "para eliminar: ./scripts/release.sh worktree rm <path>"
      info "para limpiar refs: ./scripts/release.sh worktree prune"
      ;;

    *)
      cat <<'EOF'
Uso: release.sh worktree <subcomando>

  list                                  listar worktrees activos
  add        <rama> [<path>]            checkout de rama existente en nuevo worktree
  add-branch <nueva> [<path>] [<base>]  nueva rama + worktree (base default: trunk)
  rm         <path>                     eliminar worktree (NO usar rm -rf)
  prune                                 limpiar refs de worktrees borrados manualmente
  clean                                 mostrar worktrees con rama ya mergeada al trunk

  Nota: add/add-branch replican automáticamente el sparse-set activo del árbol
  principal dentro del worktree nuevo (si no hay sparse activo, no hacen nada).
EOF
      ;;
  esac
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
  consumers)  cmd_consumers "$@" ;;
  init)       cmd_work "$@" ;;
  init-multi) cmd_work "$@" ;;
  sync)       cmd_sync ;;
  bump)       cmd_bump "$@" ;;
  tag)        cmd_tag "$@" ;;
  hotfix)     cmd_hotfix "$@" ;;
  worktree)   cmd_worktree "$@" ;;
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
  status                       estado actual: remote, trunk, sparse paths, worktrees
  add     <path|proyecto>      agregar path al sparse set actual
  drop    <path|proyecto>      quitar path del sparse set
  consumers <lib>              qué proyectos usan la lib (where-used, ADR-0080)
  init    <proyecto>           alias de work (un proyecto)
  init-multi <proy...>         alias de work (multi-proyecto)

  WORKTREES (ADR-0008)
  worktree list                         listar worktrees activos
  worktree add        <rama> [<path>]   checkout de rama existente en worktree
  worktree add-branch <rama> [<path>]   nueva rama + worktree desde trunk
  worktree rm         <path>            eliminar worktree (no usar rm -rf)
  worktree prune                        limpiar refs de worktrees borrados
  worktree clean                        mostrar worktrees con rama ya mergeada

  RELEASES  (ADR-0010 de commons: el manifiesto de cada proyecto es la fuente de verdad de la versión)
  bump    <proyecto> <1.4.2>            bump versión en el manifiesto (comitear vía PR)
  tag     <proyecto> [1.4.2]            tag de release del commit de <remote>/<trunk> — versión de su manifiesto
  hotfix  <proyecto> <1.4.2> [nombre]   branch de hotfix desde tag de prod
  cherry  <proyecto> <1.4.2>            cherry-pick del/los hotfix a trunk
  check    [proyecto]                    hotfixes pendientes (sin proy: todos)
  promote  <proyecto> <dev|rc> [--at <ref>]  tag -dev.N/-rc.N de <remote>/<trunk>; --at <ref>: rollback a un commit de trunk
           (también <proyecto> <1.4.2> <dev|rc>, si 1.4.2 es la versión del manifiesto)
  versions [proyecto]                    listar tags por ambiente

  VARIABLES DE ENTORNO (o en .gitops-env de la raíz del repo; el entorno gana)
  GITOPS_REMOTE         remote autoritativo (auto-detect: company > origin > upstream)
  GITOPS_TRUNK_BRANCH   rama trunk (default: main)
  GITOPS_PROJECTS_DIR   carpeta base de proyectos (default: projects; "." = raíz)
  GITOPS_PROJECT_MAP    nombre:carpeta,... (gana sobre GITOPS_PROJECTS_DIR)
  GITOPS_SHARED_DIR     libs compartidas (default: shared/libs; Azure: _shared/libs)

  Las libs de cada proyecto salen de su manifiesto (scripts/deps.py, ADR-0080):
  [tool.uv.sources] en pyproject.toml, file:/workspace: en package.json,
  replace en go.mod, o deps.txt.
EOF
    ;;
esac
