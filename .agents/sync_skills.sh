#!/usr/bin/env bash
# sync_skills.sh — Sincroniza agent skills desde el repo de skills (ai-tooling o remoto).
#
# Estrategia:
#   1. Throttle de 24h — si ya sincronizó hoy, no hace nada.
#   2. Lee .ai-tooling para obtener capabilities, repo URL y ref.
#   3. Usa git sparse-checkout para descargar SOLO las capas declaradas.
#   4. Detecta si el repo es "layered" (common/capabilities/) o "flat" (.agents/skills/).
#   5. Copia skills con resolución de conflictos via soul.md:
#      - Local tiene soul.md → local gana (skill customizado/versionado).
#      - Solo remoto tiene soul.md → remoto gana.
#      - Ninguno tiene soul.md → remoto gana.
#      - Ambos tienen soul.md → compara campo version:; mayor versión gana; empate → local gana.
#   6. Copia skills desde local_skills_sources declarados en .ai-tooling.
#   7. Escribe .agents/.last_sync con timestamp al terminar.
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

AGENTS_REPO_DEFAULT="git@github.com:juvs91/ai-tooling.git"
AGENTS_REF_DEFAULT="master"

FORCE="${1:-}"

# ── Resolución de conflictos via soul.md ──────────────────────────────────────
# Retorna 0 si debe copiar el skill remoto, 1 si el local gana.
_should_copy_remote() {
  local remote_dir="$1" local_dir="$2"
  local remote_soul="$remote_dir/soul.md" local_soul="$local_dir/soul.md"

  # No hay versión local → siempre copiar remoto
  [[ ! -d "$local_dir" ]] && return 0

  # Local tiene soul.md y remoto no → local gana (customizado)
  [[ -f "$local_soul" ]] && ! [[ -f "$remote_soul" ]] && return 1

  # Remoto tiene soul.md y local no → remoto gana
  [[ -f "$remote_soul" ]] && ! [[ -f "$local_soul" ]] && return 0

  # Ambos tienen soul.md → comparar versiones semánticas
  if [[ -f "$remote_soul" ]] && [[ -f "$local_soul" ]]; then
    local rv lv
    rv=$(grep -m1 '^version:' "$remote_soul" | sed 's/[^0-9.]//g')
    lv=$(grep -m1 '^version:' "$local_soul"  | sed 's/[^0-9.]//g')
    # Comparación semántica simple (string sort funciona para x.y.z si misma longitud)
    [[ "$rv" > "$lv" ]] && return 0
    return 1  # empate o local mayor → local gana
  fi

  # Sin soul.md en ninguno → remoto gana (última versión siempre)
  return 0
}

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
  # Lee capabilities como array bash (compatible bash 3.2 — sin mapfile)
  CAPABILITIES=()
  while IFS= read -r line; do
    [[ -n "$line" ]] && CAPABILITIES+=("$line")
  done < <(jq -r '.capabilities[]? // empty' "$MARKER")
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

# ── Detectar si el repo es local (path) o remoto (URL) ───────────────────────
_is_local_repo() {
  [[ "$AGENTS_REPO" != http* ]] && [[ "$AGENTS_REPO" != git@* ]] && [[ "$AGENTS_REPO" != file://* ]]
}

# ── Actualizar remote del cache si cambió ────────────────────────────────────
if [[ -d "$CACHE_DIR/.git" ]]; then
  current_remote=$(git -C "$CACHE_DIR" remote get-url origin 2>/dev/null || echo "")
  if [[ "$current_remote" != "$AGENTS_REPO" ]]; then
    echo "[sync] Actualizando remote: $current_remote → $AGENTS_REPO" >&2
    git -C "$CACHE_DIR" remote set-url origin "$AGENTS_REPO" 2>/dev/null || true
  fi
fi

# ── Clone o fetch ──────────────────────────────────────────────────────────────
if _is_cache_current; then
  echo "[sync] Cache is current — skipping fetch" >&2
elif [[ -d "$CACHE_DIR/.git" ]]; then
  echo "[sync] Fetching updates..." >&2
  if _is_local_repo; then
    git -C "$CACHE_DIR" fetch origin "$AGENTS_REF" 2>/dev/null
  else
    git -C "$CACHE_DIR" fetch --depth=1 origin "$AGENTS_REF" 2>/dev/null
  fi
  git -C "$CACHE_DIR" checkout FETCH_HEAD 2>/dev/null
else
  echo "[sync] Cloning repo..." >&2
  mkdir -p "$CACHE_DIR"
  if _is_local_repo; then
    # Repo local: sin --filter=blob:none (solo aplica a remotes HTTP)
    git clone --sparse --branch "$AGENTS_REF" "$AGENTS_REPO" "$CACHE_DIR" 2>/dev/null || \
    git clone --sparse "$AGENTS_REPO" "$CACHE_DIR" 2>/dev/null || {
      echo "[sync] ERROR: No se pudo clonar $AGENTS_REPO" >&2
      exit 1
    }
  else
    git clone \
      --depth=1 \
      --filter=blob:none \
      --sparse \
      --branch "$AGENTS_REF" \
      "$AGENTS_REPO" \
      "$CACHE_DIR" 2>/dev/null || \
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

# ── Detectar estructura del repo clonado ─────────────────────────────────────
# HAS_LAYERED_STRUCTURE: repo con common/ + capabilities/ + integrations/
# HAS_FLAT_STRUCTURE:    repo con .agents/skills/ (estructura ai-tooling)
HAS_LAYERED_STRUCTURE=false
HAS_FLAT_STRUCTURE=false

if [[ -d "$CACHE_DIR/common" ]] || [[ -d "$CACHE_DIR/capabilities" ]]; then
  HAS_LAYERED_STRUCTURE=true
elif [[ -d "$CACHE_DIR/.agents/skills" ]]; then
  HAS_FLAT_STRUCTURE=true
  echo "[sync] Flat repo structure detected (.agents/skills/)" >&2
  git -C "$CACHE_DIR" sparse-checkout set ".agents/skills/" 2>/dev/null || true
else
  echo "[sync] No recognized skill structure in remote — skipping remote copy" >&2
fi

if $HAS_LAYERED_STRUCTURE; then
  # ── Resolver sparse paths ───────────────────────────────────────────────────
  SPARSE_PATHS=("common/")

  for cap in "${CAPABILITIES[@]}"; do
    SPARSE_PATHS+=("capabilities/$cap/")
  done

  # Inferir integrations: si tienes cap A y cap B, y existe integrations/A+B/, inclúyela
  if [[ -d "$CACHE_DIR/integrations" ]]; then
    for intg_dir in "$CACHE_DIR/integrations"/*/; do
      intg_name=$(basename "$intg_dir")
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
  git -C "$CACHE_DIR" sparse-checkout set "${SPARSE_PATHS[@]}" 2>/dev/null || true
fi

# ── Copiar skills a .agents/skills/ ──────────────────────────────────────────
echo "[sync] Copying skills to $SKILLS_DIR..." >&2

BACKUP_DIR=$(mktemp -d)
CUSTOM_COUNT=0

if $HAS_LAYERED_STRUCTURE; then
  # Backup de custom skills (los que NO vienen del repo central)
  if [[ -d "$SKILLS_DIR" ]]; then
    while IFS= read -r -d '' skill_md; do
      rel=$(dirname "${skill_md#$SKILLS_DIR/}")
      found_in_cache=false
      for layer in common capabilities integrations; do
        [[ -d "$CACHE_DIR/$layer/$rel" ]] && found_in_cache=true && break
      done
      if ! $found_in_cache; then
        mkdir -p "$BACKUP_DIR/$rel"
        cp -r "$SKILLS_DIR/$rel/." "$BACKUP_DIR/$rel/"
        CUSTOM_COUNT=$(( CUSTOM_COUNT + 1 ))
        echo "[sync] custom (backed up): $rel" >&2
      fi
    done < <(find "$SKILLS_DIR" -name "SKILL.md" -print0 2>/dev/null)
  fi
  echo "[sync] Backed up $CUSTOM_COUNT custom skill(s)" >&2

  # Copiar capas en orden: common → capabilities → integrations (merge flat)
  COUNT=0
  for layer in common capabilities integrations; do
    layer_dir="$CACHE_DIR/$layer"
    [[ -d "$layer_dir" ]] || continue

    while IFS= read -r -d '' skill_md; do
      skill_source_dir=$(dirname "$skill_md")
      rel="${skill_source_dir#$layer_dir/}"
      dest="$SKILLS_DIR/$rel"
      mkdir -p "$dest"
      cp -r "$skill_source_dir/." "$dest/"
      COUNT=$(( COUNT + 1 ))
    done < <(find "$layer_dir" -name "SKILL.md" -print0 2>/dev/null)

    find "$layer_dir" -name "AGENTS.*.md" -exec cp {} "$REPO_ROOT/.agents/" \; 2>/dev/null || true
  done
  echo "[sync] Copied $COUNT skill(s) from layered remote" >&2

elif $HAS_FLAT_STRUCTURE; then
  # ── Copia flat con resolución de conflictos via soul.md ───────────────────
  FLAT_SRC="$CACHE_DIR/.agents/skills"

  # Leer categorías opcionales desde .ai-tooling (campo agents_categories)
  AGENTS_CATEGORIES=()
  if [[ -f "$MARKER" ]] && command -v jq &>/dev/null; then
    while IFS= read -r line; do
      [[ -n "$line" ]] && AGENTS_CATEGORIES+=("$line")
    done < <(jq -r '.agents_categories[]? // empty' "$MARKER")
  fi

  COUNT=0
  _copy_flat() {
    local src_base="$1"
    local skill_src_dir rel dest
    while IFS= read -r -d '' skill_md; do
      skill_src_dir=$(dirname "$skill_md")
      rel="${skill_src_dir#$FLAT_SRC/}"
      dest="$SKILLS_DIR/$rel"
      if _should_copy_remote "$skill_src_dir" "$dest"; then
        mkdir -p "$dest"
        cp -r "$skill_src_dir/." "$dest/"
        COUNT=$(( COUNT + 1 ))
      else
        echo "[sync] local soul wins: $rel" >&2
      fi
    done < <(find "$src_base" -name "SKILL.md" -print0 2>/dev/null)
  }

  if [[ ${#AGENTS_CATEGORIES[@]} -eq 0 ]]; then
    # Sin filtro: copiar todos los skills del repo remoto
    _copy_flat "$FLAT_SRC"
  else
    # Con filtro: solo las categorías declaradas en agents_categories
    for cat in "${AGENTS_CATEGORIES[@]}"; do
      [[ -d "$FLAT_SRC/$cat" ]] && _copy_flat "$FLAT_SRC/$cat"
    done
  fi
  echo "[sync] Copied $COUNT skill(s) from flat remote" >&2

else
  echo "[sync] Self-hosted — remote skill copy skipped (0 remote skills)" >&2
fi

# ── Restaurar custom skills (merge — el remoto no los toca) ──────────────────
if [[ $CUSTOM_COUNT -gt 0 ]]; then
  while IFS= read -r -d '' skill_md; do
    rel=$(dirname "${skill_md#$BACKUP_DIR/}")
    dest="$SKILLS_DIR/$rel"
    mkdir -p "$dest"
    cp -r "$BACKUP_DIR/$rel/." "$dest/"
  done < <(find "$BACKUP_DIR" -name "SKILL.md" -print0 2>/dev/null)
  echo "[sync] Restored $CUSTOM_COUNT custom skill(s)" >&2
fi
rm -rf "$BACKUP_DIR"

# ── Copiar desde local_skills_sources ────────────────────────────────────────
if [[ -f "$MARKER" ]] && command -v jq &>/dev/null; then
  LOCAL_COUNT=$(jq -r '.local_skills_sources | length // 0' "$MARKER" 2>/dev/null || echo "0")
  if [[ "$LOCAL_COUNT" -gt 0 ]]; then
    echo "[sync] Processing $LOCAL_COUNT local_skills_source(s)..." >&2
    for i in $(seq 0 $((LOCAL_COUNT - 1))); do
      src_path=$(jq -r ".local_skills_sources[$i].path" "$MARKER" | sed "s|^~|$HOME|")
      src_categories=()
      while IFS= read -r line; do
        [[ -n "$line" ]] && src_categories+=("$line")
      done < <(jq -r ".local_skills_sources[$i].categories[]?" "$MARKER")
      if [[ ! -d "$src_path" ]]; then
        echo "[sync] local_source not found (skipping): $src_path" >&2
        continue
      fi
      LOCAL_SKILL_COUNT=0
      for cat in "${src_categories[@]}"; do
        cat_src="$src_path/$cat"
        [[ -d "$cat_src" ]] || continue
        while IFS= read -r -d '' skill_md; do
          skill_src_dir=$(dirname "$skill_md")
          rel="${skill_src_dir#$src_path/}"
          dest="$SKILLS_DIR/$rel"
          mkdir -p "$dest"
          cp -r "$skill_src_dir/." "$dest/"
          LOCAL_SKILL_COUNT=$(( LOCAL_SKILL_COUNT + 1 ))
          echo "[sync] local: $rel" >&2
        done < <(find "$cat_src" -name "SKILL.md" -print0 2>/dev/null)
      done
      echo "[sync] Imported $LOCAL_SKILL_COUNT skill(s) from $src_path" >&2
    done
  fi
fi

# ── Escribir timestamp ────────────────────────────────────────────────────────
mkdir -p "$(dirname "$LAST_SYNC")"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$LAST_SYNC"

echo "[sync] Done. Skills up to date." >&2
