#!/usr/bin/env bash
# sync_skills.sh — Sincroniza agent skills desde el repo central (cornerstone-agents).
#
# Estrategia:
#   1. Throttle de 24h — si ya sincronizó hoy, no hace nada.
#   2. Lee .ai-tooling para obtener capabilities, repo URL y ref.
#   3. Usa git sparse-checkout para descargar SOLO las capas declaradas.
#   4. Copia los SKILL.md resultantes a .agents/skills/ (merge flat por capas).
#   5. Escribe .agents/.last_sync con timestamp al terminar.
#
# Dependencias: git, jq (opcional para leer .ai-tooling)
# Uso: bash .agents/sync_skills.sh
#       bash .agents/sync_skills.sh --force   (ignora throttle)

set -euo pipefail

# ── Configuración ──────────────────────────────────────────────────────────────
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo ".")"
MARKER="$REPO_ROOT/.ai-tooling"
LAST_SYNC="$REPO_ROOT/.agents/.last_sync"
CACHE_DIR="$REPO_ROOT/.agents/_repo"
SKILLS_DIR="$REPO_ROOT/.agents/skills"

AGENTS_REPO_DEFAULT="https://github.com/deagentic/cornerstone-agents"
AGENTS_REF_DEFAULT="master"

FORCE="${1:-}"

# ── Throttle 24h ───────────────────────────────────────────────────────────────
if [[ "$FORCE" != "--force" ]] && [[ -f "$LAST_SYNC" ]]; then
  NOW=$(date +%s)
  if [[ "$(uname)" == "Darwin" ]]; then
    MTIME=$(stat -f%m "$LAST_SYNC")
  else
    MTIME=$(stat -c%Y "$LAST_SYNC")
  fi
  AGE=$(( NOW - MTIME ))
  if [[ $AGE -lt 86400 ]]; then
    # Synced within 24h — skip silently
    exit 0
  fi
fi

echo "[sync] Starting skill sync..." >&2

# ── Leer .ai-tooling ───────────────────────────────────────────────────────────
if [[ -f "$MARKER" ]] && command -v jq &>/dev/null; then
  AGENTS_REPO=$(jq -r '.agents_repo // empty' "$MARKER")
  AGENTS_REF=$(jq -r '.agents_ref // empty' "$MARKER")
  # Lee capabilities como array bash
  mapfile -t CAPABILITIES < <(jq -r '.capabilities[]? // empty' "$MARKER")
else
  AGENTS_REPO=""
  AGENTS_REF=""
  CAPABILITIES=()
fi

AGENTS_REPO="${AGENTS_REPO:-$AGENTS_REPO_DEFAULT}"
AGENTS_REF="${AGENTS_REF:-$AGENTS_REF_DEFAULT}"

# Si no hay capabilities, usar "common" como mínimo
if [[ ${#CAPABILITIES[@]} -eq 0 ]]; then
  CAPABILITIES=("common")
fi

echo "[sync] repo       : $AGENTS_REPO@$AGENTS_REF" >&2
echo "[sync] capabilities: ${CAPABILITIES[*]}" >&2

# ── Fast-path: ¿hay commits nuevos en remoto? ─────────────────────────────────
_remote_head() {
  git ls-remote "$AGENTS_REPO" "$AGENTS_REF" 2>/dev/null | awk '{print $1}' | head -1
}

_local_head() {
  git -C "$CACHE_DIR" rev-parse HEAD 2>/dev/null || echo ""
}

_is_cache_current() {
  [[ ! -d "$CACHE_DIR/.git" ]] && return 1
  local remote
  remote=$(_remote_head)
  [[ -z "$remote" ]] && return 0  # Sin red → asumir cache válido
  local local_sha
  local_sha=$(_local_head)
  [[ "$remote" == "$local_sha" ]]
}

# ── Clone o fetch ──────────────────────────────────────────────────────────────
if _is_cache_current; then
  echo "[sync] Cache is current — skipping fetch" >&2
elif [[ -d "$CACHE_DIR/.git" ]]; then
  echo "[sync] Fetching updates..." >&2
  git -C "$CACHE_DIR" fetch --depth=1 origin "$AGENTS_REF" 2>/dev/null
  git -C "$CACHE_DIR" checkout FETCH_HEAD 2>/dev/null
else
  echo "[sync] Cloning sparse repo..." >&2
  mkdir -p "$CACHE_DIR"
  if ! git clone \
    --depth=1 \
    --filter=blob:none \
    --sparse \
    --branch "$AGENTS_REF" \
    "$AGENTS_REPO" \
    "$CACHE_DIR" 2>/dev/null; then
    # Retry sin --branch (repo sin tags todavía)
    git clone \
      --depth=1 \
      --filter=blob:none \
      --sparse \
      "$AGENTS_REPO" \
      "$CACHE_DIR" 2>/dev/null || {
        echo "[sync] ERROR: No se pudo clonar $AGENTS_REPO" >&2
        echo "[sync] Verifica acceso de red o 'gh auth login'" >&2
        exit 1
      }
  fi
fi

# ── Resolver sparse paths ─────────────────────────────────────────────────────
SPARSE_PATHS=("common/")

for cap in "${CAPABILITIES[@]}"; do
  SPARSE_PATHS+=("capabilities/$cap/")
done

# Inferir integrations: si tienes cap A y cap B, y existe integrations/A+B/, inclúyela
if [[ -d "$CACHE_DIR/integrations" ]]; then
  for intg_dir in "$CACHE_DIR/integrations"/*/; do
    intg_name=$(basename "$intg_dir")
    # Separar por "+"
    IFS='+' read -ra parts <<< "$intg_name"
    all_present=true
    for part in "${parts[@]}"; do
      found=false
      for cap in "${CAPABILITIES[@]}"; do
        [[ "$cap" == "$part" ]] && found=true && break
      done
      $found || { all_present=false; break; }
    done
    $all_present && SPARSE_PATHS+=("integrations/$intg_name/")
  done
fi

echo "[sync] sparse paths: ${SPARSE_PATHS[*]}" >&2

# ── Aplicar sparse-checkout ───────────────────────────────────────────────────
git -C "$CACHE_DIR" sparse-checkout set "${SPARSE_PATHS[@]}" 2>/dev/null || true

# ── Copiar skills a .agents/skills/ ──────────────────────────────────────────
echo "[sync] Copying skills to $SKILLS_DIR..." >&2

# Preservar skills locales que no vienen del repo central (custom skills)
CUSTOM_SKILLS=()
if [[ -d "$SKILLS_DIR" ]]; then
  while IFS= read -r -d '' skill_md; do
    rel=$(dirname "${skill_md#$SKILLS_DIR/}")
    # Si este skill NO existe en el cache, es custom — preservar
    found_in_cache=false
    for layer in common capabilities integrations; do
      [[ -d "$CACHE_DIR/$layer/$rel" ]] && found_in_cache=true && break
    done
    $found_in_cache || CUSTOM_SKILLS+=("$rel")
  done < <(find "$SKILLS_DIR" -name "SKILL.md" -print0 2>/dev/null)
fi

# Copiar capas en orden: common → capabilities → integrations (merge flat)
COUNT=0
for layer in common capabilities integrations; do
  layer_dir="$CACHE_DIR/$layer"
  [[ -d "$layer_dir" ]] || continue

  # Encontrar todos los directorios con SKILL.md
  while IFS= read -r -d '' skill_md; do
    skill_source_dir=$(dirname "$skill_md")
    # Ruta relativa dentro de la capa (e.g. "bdd-writer" de "capabilities/bdd-writer/SKILL.md")
    rel="${skill_source_dir#$layer_dir/}"
    dest="$SKILLS_DIR/$rel"

    mkdir -p "$dest"
    cp -r "$skill_source_dir/." "$dest/"
    COUNT=$(( COUNT + 1 ))
  done < <(find "$layer_dir" -name "SKILL.md" -print0 2>/dev/null)

  # Copiar fragmentos AGENTS.*.md a .agents/
  find "$layer_dir" -name "AGENTS.*.md" -exec cp {} "$REPO_ROOT/.agents/" \; 2>/dev/null || true
done

echo "[sync] Copied $COUNT skill(s)" >&2

# ── Escribir timestamp ────────────────────────────────────────────────────────
mkdir -p "$(dirname "$LAST_SYNC")"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$LAST_SYNC"

echo "[sync] Done. Skills up to date." >&2
