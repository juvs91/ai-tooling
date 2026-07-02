#!/usr/bin/env bash
# scripts/gitops-init.sh
#
# Bootstrap GitOps Monorepo para un proyecto existente.
# Copia release.sh, genera .pre-commit-config.yaml según stack detectado,
# crea CODEOWNERS base, copia ADR gate tools e instala hooks.
#
# Uso (desde ai-tooling o apuntando a él):
#   ./scripts/gitops-init.sh [--target /ruta/a/proyecto] [--dry-run] [--skip-precommit]
#
# Desde el proyecto destino:
#   bash /ruta/a/ai-tooling/scripts/gitops-init.sh --target .
#
# Variables de entorno:
#   GITOPS_REMOTE         remote autoritativo del proyecto destino (default: auto)
#   GITOPS_TRUNK_BRANCH   rama trunk del proyecto destino (default: main)
#   GITOPS_SCOPE          scope de paquetes internos (default: @deacero)

set -euo pipefail

# ── Paths ──────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATES_DIR="$REPO_ROOT/templates/gitops"

# ── Defaults ───────────────────────────────────────────────────────────────

TARGET_DIR=""
DRY_RUN=false
SKIP_PRECOMMIT=false
GITOPS_TRUNK="${GITOPS_TRUNK_BRANCH:-main}"
GITOPS_SCOPE="${GITOPS_SCOPE:-@deacero}"
GITOPS_STACKS_FLAG=""
GITOPS_PROJECT_MAP_FLAG=""

# ── Output helpers ─────────────────────────────────────────────────────────

die()     { printf "ERROR: %b\n" "$*" >&2; exit 1; }
info()    { echo "→ $*"; }
ok()      { echo "✓ $*"; }
warn()    { echo "⚠ $*"; }
section() { echo ""; echo "── $* ──────────────────────────────────────────"; }
dry()     { echo "[dry-run] $*"; }

# Copia un archivo; en dry-run solo lo muestra
copy_file() {
  local src="$1" dst="$2" label="${3:-}"
  if $DRY_RUN; then
    dry "cp $src → $dst"
    return
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  ok "${label:-$(basename "$dst")} copiado"
}

# Escribe contenido a un archivo; en dry-run solo lo muestra
write_file() {
  local dst="$1" label="${2:-}"
  if $DRY_RUN; then
    dry "write → $dst"
    return
  fi
  mkdir -p "$(dirname "$dst")"
  cat > "$dst"
  ok "${label:-$(basename "$dst")} generado"
}

# ── Argument parsing ───────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)      TARGET_DIR="$2"; shift 2 ;;
    --dry-run)     DRY_RUN=true; shift ;;
    --skip-precommit) SKIP_PRECOMMIT=true; shift ;;
    --trunk)       GITOPS_TRUNK="$2"; shift 2 ;;
    --scope)       GITOPS_SCOPE="$2"; shift 2 ;;
    --stack)       GITOPS_STACKS_FLAG="$2"; shift 2 ;;
    --project-map) GITOPS_PROJECT_MAP_FLAG="$2"; shift 2 ;;
    --help|-h)
      cat <<'EOF'
Uso: gitops-init.sh [opciones]

  --target <dir>           directorio del proyecto destino (default: directorio actual)
  --dry-run                mostrar qué haría sin ejecutar nada
  --skip-precommit         no instalar pre-commit hooks
  --trunk <rama>           rama trunk del proyecto (default: main)
  --scope <scope>          scope de paquetes internos (default: @deacero)
  --stack <stacks>         stacks del proyecto, separados por coma (ej: "python,typescript,go")
  --project-map <map>      mapeo nombre:directorio separado por coma (ej: "backend:backend,frontend:.")

Variables de entorno:
  GITOPS_TRUNK_BRANCH   rama trunk (sobreescribe --trunk)
  GITOPS_SCOPE          scope de paquetes (sobreescribe --scope)
  GITOPS_STACKS         stacks del proyecto (sobreescribe --stack)
  GITOPS_PROJECT_MAP    mapeo nombre:dir (sobreescribe --project-map)

Si --stack o --project-map no se proporcionan, el script preguntará interactivamente.
EOF
      exit 0
      ;;
    *) die "argumento desconocido: $1\n  usa --help para ver opciones" ;;
  esac
done

# ── Validaciones iniciales ─────────────────────────────────────────────────

# Resolver directorio destino
if [[ -z "$TARGET_DIR" ]]; then
  TARGET_DIR="$(pwd)"
fi
[[ -d "$TARGET_DIR" ]] || die "directorio destino no existe: $TARGET_DIR"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

[[ "$TARGET_DIR" != "$REPO_ROOT" ]] \
  || die "el directorio destino no puede ser el mismo ai-tooling\n  usa --target /ruta/a/otro-proyecto"

# Verificar que es un repo git
git -C "$TARGET_DIR" rev-parse --git-dir &>/dev/null \
  || die "$TARGET_DIR no es un repositorio git"

# Verificar que ai-tooling tiene los archivos fuente
[[ -f "$REPO_ROOT/scripts/release.sh" ]] \
  || die "no encontré scripts/release.sh en $REPO_ROOT\n  ¿estás corriendo desde ai-tooling?"
[[ -f "$REPO_ROOT/tools/check_adr_gate.py" ]] \
  || die "no encontré tools/check_adr_gate.py en $REPO_ROOT"
[[ -f "$REPO_ROOT/tools/install_hooks.sh" ]] \
  || die "no encontré tools/install_hooks.sh en $REPO_ROOT"
[[ -f "$TEMPLATES_DIR/.pre-commit-config.yaml.template" ]] \
  || die "no encontré templates/gitops/.pre-commit-config.yaml.template en $REPO_ROOT"

# Genera el patrón del ADR gate según estructura del proyecto
generate_adr_pattern() {
  local target="$1"
  local patterns=()

  [[ -d "$target/vendor" ]]   && patterns+=("vendor/.*\\.py")
  [[ -d "$target/src" ]]      && patterns+=("src/.*\\.py")
  [[ -d "$target/projects" ]] && patterns+=("projects/.*\\.(py|ts|go)")
  [[ -d "$target/.agents" ]]  && patterns+=("\\.agents/skills/.*\\.md")

  if [[ ${#patterns[@]} -eq 0 ]]; then
    echo "(?x)^(\\.agents/skills/.*\\.md)$"
    return
  fi

  local joined
  joined=$(printf "%s|" "${patterns[@]}")
  joined="${joined%|}"
  echo "(?x)^(${joined})$"
}

# ── Ejecución ──────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  GitOps Init — Deacero                           ║"
echo "║  Ref: ADR-0007-gitops-monorepo-trunk-based.md    ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
info "Destino:  $TARGET_DIR"
info "Trunk:    $GITOPS_TRUNK"
info "Scope:    $GITOPS_SCOPE"
$DRY_RUN && warn "MODO DRY-RUN — no se escribe nada"
echo ""

# Resolver stacks y project map (flag → env → wizard interactivo)
STACKS="${GITOPS_STACKS_FLAG:-${GITOPS_STACKS:-}}"
PROJECT_MAP="${GITOPS_PROJECT_MAP_FLAG:-${GITOPS_PROJECT_MAP:-}}"

if [[ -z "$STACKS" ]]; then
  echo ""
  warn "No se especificó --stack ni GITOPS_STACKS."
  printf "¿Qué stacks usa este proyecto? (python, typescript, node, go — separados por coma)\n  > "
  read -r STACKS
  STACKS="${STACKS// /}"
fi

[[ -z "$STACKS" ]] && die "GITOPS_STACKS es requerido — usa --stack o setea GITOPS_STACKS en .gitops-env"

if [[ -z "$PROJECT_MAP" ]]; then
  echo ""
  warn "No se especificó --project-map ni GITOPS_PROJECT_MAP."
  printf "¿Cuántos proyectos independientes tiene este repo? (número)\n  > "
  read -r _n
  _n="${_n//[^0-9]/}"
  [[ -z "$_n" ]] && _n=1
  PROJECT_MAP=""
  for _i in $(seq 1 "$_n"); do
    echo ""
    echo "  Proyecto $_i:"
    printf "    Nombre (ej: backend, api, frontend): "
    read -r _pname
    printf "    Directorio relativo desde la raíz (ej: backend, python/auth, .): "
    read -r _pdir
    [[ -n "$PROJECT_MAP" ]] && PROJECT_MAP="${PROJECT_MAP},"
    PROJECT_MAP="${PROJECT_MAP}${_pname}:${_pdir}"
  done
fi

info "Stack:    $STACKS"
[[ -n "$PROJECT_MAP" ]] && info "Projects: $PROJECT_MAP"

# ── PASO 1: release.sh ──────────────────────────────────────────────────────

section "1/5 release.sh"

if [[ -f "$TARGET_DIR/scripts/release.sh" ]]; then
  warn "scripts/release.sh ya existe — sobreescribiendo"
fi

copy_file "$REPO_ROOT/scripts/release.sh" "$TARGET_DIR/scripts/release.sh" "release.sh"
$DRY_RUN || chmod +x "$TARGET_DIR/scripts/release.sh"

# Crear .gitops-env si no existe (config del proyecto)
GITOPS_ENV="$TARGET_DIR/.gitops-env"
if [[ ! -f "$GITOPS_ENV" ]]; then
  write_file "$GITOPS_ENV" ".gitops-env" <<EOF
# Configuración GitOps de este proyecto
# Cargar: source .gitops-env
# Ref: ADR-0007-gitops-monorepo-trunk-based.md

export GITOPS_TRUNK_BRANCH="${GITOPS_TRUNK}"
export GITOPS_SCOPE="${GITOPS_SCOPE}"
# export GITOPS_REMOTE="origin"  # descomentar si el remote no se detecta automáticamente
export GITOPS_STACKS="${STACKS}"
export GITOPS_PROJECT_MAP="${PROJECT_MAP}"
EOF
else
  info ".gitops-env ya existe — no sobreescribir"
fi

# ── PASO 2: ADR gate tools ──────────────────────────────────────────────────

section "2/5 ADR gate tools"

if [[ -f "$TARGET_DIR/tools/check_adr_gate.py" ]]; then
  info "tools/check_adr_gate.py ya existe — skip"
else
  copy_file "$REPO_ROOT/tools/check_adr_gate.py" "$TARGET_DIR/tools/check_adr_gate.py"
fi

if [[ -f "$TARGET_DIR/tools/install_hooks.sh" ]]; then
  info "tools/install_hooks.sh ya existe — skip"
else
  copy_file "$REPO_ROOT/tools/install_hooks.sh" "$TARGET_DIR/tools/install_hooks.sh"
  $DRY_RUN || chmod +x "$TARGET_DIR/tools/install_hooks.sh"
fi

# ── PASO 3: CODEOWNERS ─────────────────────────────────────────────────────

section "3/5 CODEOWNERS"

CODEOWNERS_DST="$TARGET_DIR/CODEOWNERS"
if [[ -f "$CODEOWNERS_DST" ]]; then
  warn "CODEOWNERS ya existe — no sobreescribir"
  info "template disponible en: $TEMPLATES_DIR/CODEOWNERS.template"
else
  copy_file "$TEMPLATES_DIR/CODEOWNERS.template" "$CODEOWNERS_DST" "CODEOWNERS"
  warn "CODEOWNERS creado desde template — editar @equipo-* con usuarios/grupos reales"
fi

# ── PASO 4: .pre-commit-config.yaml ────────────────────────────────────────

section "4/5 .pre-commit-config.yaml"

PRECOMMIT_DST="$TARGET_DIR/.pre-commit-config.yaml"
ADR_PATTERN=$(generate_adr_pattern "$TARGET_DIR")

if [[ -f "$PRECOMMIT_DST" ]]; then
  warn ".pre-commit-config.yaml ya existe — no sobreescribir"
  info "para regenerar: rm $PRECOMMIT_DST && re-corre gitops-init.sh"
elif $DRY_RUN; then
  dry "write → $PRECOMMIT_DST"
  dry "  trunk:       $GITOPS_TRUNK"
  dry "  stack:       $STACKS"
  dry "  adr pattern: $ADR_PATTERN"
else
  # Generación directa con heredoc — evita problemas de sed con multilinea en macOS
  {
    # ── Cabecera ──
    cat <<HEADER
# .pre-commit-config.yaml — GitOps Monorepo Deacero
# Generado por scripts/gitops-init.sh — $(date '+%Y-%m-%d')
# Ref: ADR-0007-gitops-monorepo-trunk-based.md
#
# Activar:
#   pip install pre-commit
#   pre-commit install
#   pre-commit install --hook-type commit-msg

repos:
  # ── Hooks genéricos ──────────────────────────────────────────────────────
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-added-large-files
        args: ["--maxkb=500"]
      - id: check-yaml
        exclude: ^templates/
      - id: check-toml
      - id: check-json
        exclude: ^\.vscode/
      - id: check-merge-conflict
      - id: no-commit-to-branch
        args: ["--branch", "${GITOPS_TRUNK}"]
HEADER

    # ── Sección de stack ──
    if echo "$STACKS" | grep -q "python"; then
      cat <<PYTHON

  # ── Python — ruff (lint + format) ────────────────────────────────────────
  # Instalar: pip install ruff
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff (lint)
        entry: ruff check --fix
        language: system
        types: [python]

      - id: ruff-format
        name: ruff (format)
        entry: ruff format
        language: system
        types: [python]
PYTHON
    fi

    if echo "$STACKS" | grep -qE "node|typescript"; then
      cat <<NODE

  # ── Node.js / TypeScript — eslint + prettier ─────────────────────────────
  # Instalar: npm install -D eslint prettier eslint-config-prettier
  - repo: local
    hooks:
      - id: eslint
        name: eslint
        entry: npx eslint --fix
        language: system
        types_or: [javascript, jsx, ts, tsx]

      - id: prettier
        name: prettier
        entry: npx prettier --write
        language: system
        types_or: [javascript, jsx, ts, tsx, json, markdown]
NODE
    fi

    if echo "$STACKS" | grep -q "go"; then
      cat <<GOLANG

  # ── Go — gofmt + golangci-lint ────────────────────────────────────────────
  # Instalar: go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
  - repo: local
    hooks:
      - id: gofmt
        name: gofmt
        entry: gofmt -w
        language: system
        types: [go]

      - id: golangci-lint
        name: golangci-lint
        entry: bash -c 'find . -name go.mod -not -path "./.git/*" -exec dirname {} \; | xargs -I{} sh -c "cd \"{}\" && golangci-lint run --fix"'
        language: system
        pass_filenames: false
        files: '\.go$'
GOLANG
    fi

    # ── ADR gate + Conventional commit ──
    cat <<COMMON

  # ── ADR gate + Conventional Commits ─────────────────────────────────────
  - repo: local
    hooks:
      - id: adr-gate
        name: ADR gate
        entry: python tools/check_adr_gate.py
        language: system
        pass_filenames: false
        always_run: false
        files: '${ADR_PATTERN}'

      - id: conventional-commit
        name: conventional commit
        entry: >-
          bash -c '
          msg=\$(cat "\$1");
          pattern="^(feat|fix|chore|docs|style|refactor|perf|test|build|ci|revert)(\(.+\))?(![^:]*)?:";
          if ! echo "\$msg" | grep -qE "\$pattern"; then
            echo "";
            echo "  ERROR: commit message no sigue Conventional Commits";
            echo "  Formato: tipo(scope): descripcion";
            echo "  Tipos: feat fix chore docs style refactor perf test build ci revert";
            echo "  Ejemplo: feat(auth): agregar refresh token";
            echo "  Actual: \$msg";
            echo "";
            exit 1;
          fi' --
        language: system
        stages: [commit-msg]
        always_run: true
COMMON
  } > "$PRECOMMIT_DST"

  ok ".pre-commit-config.yaml generado (stack: $STACKS, trunk: $GITOPS_TRUNK)"
fi

# ── PASO 5: Instalar pre-commit hooks ──────────────────────────────────────

section "5/5 pre-commit install"

if $SKIP_PRECOMMIT; then
  warn "--skip-precommit activo — instalación omitida"
  info "para instalar manualmente:"
  info "  cd $TARGET_DIR && pre-commit install && pre-commit install --hook-type commit-msg"
else
  if ! command -v pre-commit &>/dev/null; then
    warn "pre-commit no está instalado — omitiendo instalación de hooks"
    info "instalar con: pip install pre-commit"
    info "luego:        cd $TARGET_DIR && pre-commit install && pre-commit install --hook-type commit-msg"
  elif $DRY_RUN; then
    dry "cd $TARGET_DIR && pre-commit install"
    dry "cd $TARGET_DIR && pre-commit install --hook-type commit-msg"
  else
    cd "$TARGET_DIR"
    pre-commit install
    pre-commit install --hook-type commit-msg
    ok "pre-commit hooks instalados"
    cd - > /dev/null
  fi
fi

# ── Resumen final ───────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  GitOps Init completado                                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Archivos creados en $TARGET_DIR:"
echo ""
[[ -f "$TARGET_DIR/scripts/release.sh" ]]           && echo "  ✓ scripts/release.sh"
[[ -f "$TARGET_DIR/.gitops-env" ]]                  && echo "  ✓ .gitops-env"
[[ -f "$TARGET_DIR/tools/check_adr_gate.py" ]]      && echo "  ✓ tools/check_adr_gate.py"
[[ -f "$TARGET_DIR/tools/install_hooks.sh" ]]       && echo "  ✓ tools/install_hooks.sh"
[[ -f "$TARGET_DIR/CODEOWNERS" ]]                   && echo "  ✓ CODEOWNERS"
[[ -f "$TARGET_DIR/.pre-commit-config.yaml" ]]      && echo "  ✓ .pre-commit-config.yaml"
echo ""
echo "  Próximos pasos:"
echo ""
echo "  1. Editar CODEOWNERS con usuarios/grupos reales"
echo "  2. source .gitops-env  (o agregar al .envrc / shell profile)"
echo "  3. pre-commit run --all-files  (primera pasada de limpieza)"
echo "  4. Configurar branch restrictions en Bitbucket para CODEOWNERS"
echo "  5. Leer docs: scripts/release.sh --help"
echo ""
