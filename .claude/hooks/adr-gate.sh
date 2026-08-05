#!/usr/bin/env bash
# adr-gate.sh — PreToolUse hook: bloquea edits a rutas guarded sin ADR staged.
#
# Configuración por proyecto: .claude/adr-gate.conf
#   Formato: PREFIJO [EXTENSION_REGEX]   (una regla por línea)
#   Ejemplo: backend/app/models/   \.py$
#   Si no existe el archivo, se usan los defaults de ai-tooling.
#
# Bypass: si hay un ADR-*.md nuevo en git staging → permitir
# Bypass: si la edición es el ADR mismo → permitir
#
# Input (stdin): JSON de Claude Code
#   { "tool_name": "Edit|Write", "tool_input": { "file_path": "..." } }
# Exit 0 = permitir, Exit 2 = bloquear

set -euo pipefail

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || true)

[[ -z "$FILE" ]] && exit 0

# Normalizar: quitar ./ inicial
FILE="${FILE#./}"

# Normalizar: si es ruta absoluta, quitar el REPO_ROOT
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")
if [[ "$FILE" == "$REPO_ROOT"/* ]]; then
  FILE="${FILE#$REPO_ROOT/}"
fi

# ── Directorio de ADRs (configurable) ────────────────────────────────────────
ADR_PATH="docs/adr"
CONFIG_FILE=".claude/adr-gate.conf"
if [ -f "$CONFIG_FILE" ] && grep -qE '^adr_path:' "$CONFIG_FILE" 2>/dev/null; then
  ADR_PATH=$(grep -E '^adr_path:' "$CONFIG_FILE" | head -1 | sed 's/^adr_path:[[:space:]]*//' | sed 's:/*$::')
fi

# ── Enforcement: numeración secuencial de ADRs nuevos ─────────────────────────
# Solo aplica al CREAR un archivo nuevo (no existe aún en disco) con el patrón
# ADR-NNNN-*.md. Editar un ADR ya existente (status, typo) no dispara este chequeo.
if echo "$FILE" | grep -qE "^${ADR_PATH}/ADR-[0-9]+-.*\\.md\$"; then
  TARGET_ABS="$REPO_ROOT/$FILE"
  if [ ! -f "$TARGET_ABS" ]; then
    NEW_NUM=$(echo "$FILE" | grep -oE 'ADR-[0-9]+' | grep -oE '[0-9]+')
    MAX_NUM=0
    if [ -d "$REPO_ROOT/$ADR_PATH" ]; then
      while IFS= read -r f; do
        [ -z "$f" ] && continue
        n=$(basename "$f" | grep -oE '^ADR-[0-9]+' | grep -oE '[0-9]+' || true)
        [ -z "$n" ] && continue
        n=$((10#$n))
        [ "$n" -gt "$MAX_NUM" ] && MAX_NUM=$n
      done < <(find "$REPO_ROOT/$ADR_PATH" -maxdepth 1 -name 'ADR-*.md' 2>/dev/null || true)
    fi
    EXPECTED=$((MAX_NUM + 1))
    NEW_NUM_INT=$((10#$NEW_NUM))
    if [ "$NEW_NUM_INT" -ne "$EXPECTED" ]; then
      EXPECTED_PADDED=$(printf 'ADR-%04d' "$EXPECTED")
      cat >&2 <<EOF
ADR GATE BLOQUEADO — numeración no secuencial

Intentas crear: $FILE
Último ADR existente: ADR-$(printf '%04d' "$MAX_NUM")
Siguiente número válido: ${EXPECTED_PADDED}

Usa exactamente ${EXPECTED_PADDED}-<titulo>.md
EOF
      exit 2
    fi
  fi
fi

# ── Rutas que SIEMPRE se permiten ─────────────────────────────────────────────
# 1. El propio archivo es un ADR
if echo "$FILE" | grep -qE "^${ADR_PATH}/(ADR-[0-9]+-|[0-9]+-)?.*\\.md\$"; then
  exit 0
fi

# 2. Archivos de tests
if echo "$FILE" | grep -qE '(/tests?/|/test_|_test\.)'; then
  exit 0
fi

# 3. Documentación que no sea skills/agents (nuevos archivos aquí son una
#    superficie real de contaminación entre sesiones — ver ADR-0043)
if echo "$FILE" | grep -qE '\.(md|rst|txt)$' \
   && ! echo "$FILE" | grep -qE '\.agents/skills/|\.claude/agents/|\.claude/skills/'; then
  exit 0
fi

# 4. Artefactos de sesión
if echo "$FILE" | grep -qE '^(ai-notes|context|templates)/'; then
  exit 0
fi

# ── Cargar patrones guardados ─────────────────────────────────────────────────
GUARDED_PREFIXES=()
GUARDED_EXTENSIONS=()

if [ -f "$CONFIG_FILE" ] && [ -r "$CONFIG_FILE" ]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *:* ]] && continue  # directivas tipo "adr_path: ..."
    read -r prefix ext_regex <<< "$line"
    [ -z "$prefix" ] && continue
    GUARDED_PREFIXES+=("$prefix")
    GUARDED_EXTENSIONS+=("${ext_regex:-}")
  done < "$CONFIG_FILE"
else
  # Defaults para ai-tooling (backward compatible)
  # .claude/agents/ y .claude/skills/ se agregaron tras ADR-0043: un archivo
  # nuevo ahí registra un subagent/skill real (discovery recursivo,
  # confirmado empíricamente) sin pasar por workflow-coordinator — misma
  # clase de riesgo que editar .agents/skills/ directamente.
  GUARDED_PREFIXES=("vendor/claude-code-proxy/" ".agents/skills/" ".claude/agents/" ".claude/skills/")
  GUARDED_EXTENSIONS=('\.py$' '\.md$' '\.md$' '\.md$')
fi

# ── Comprobar si el archivo es "guarded" ──────────────────────────────────────
GUARDED=false
GUARDED_REASON=""

for i in "${!GUARDED_PREFIXES[@]}"; do
  prefix="${GUARDED_PREFIXES[$i]}"
  ext="${GUARDED_EXTENSIONS[$i]:-}"
  if [[ "$FILE" == "$prefix"* ]]; then
    if [ -z "$ext" ] || echo "$FILE" | grep -qE "$ext" 2>/dev/null; then
      GUARDED=true
      GUARDED_REASON="$prefix"
      break
    fi
  fi
done

[[ "$GUARDED" == "false" ]] && exit 0

# ── Verificar si hay un ADR nuevo staged en git RELEVANTE a esta edición ──────
# No basta con "existe algún ADR nuevo" — debe mencionar el prefijo guardado
# (GUARDED_REASON) que disparó el gate, si no cualquier ADR sin relación
# (p.ej. de trabajo previo sin commitear) bypasea la protección para siempre.
NEW_ADR_FILES=$(git -C "$REPO_ROOT" status --porcelain "${ADR_PATH}/" 2>/dev/null \
  | grep -E '^(\?\?|A ).*\.(md|txt|rst)$' 2>/dev/null \
  | cut -c4-)

while IFS= read -r adr_rel; do
  [ -z "$adr_rel" ] && continue
  adr_abs="$REPO_ROOT/$adr_rel"
  [ -f "$adr_abs" ] || continue
  if grep -qF -- "$GUARDED_REASON" "$adr_abs" 2>/dev/null; then
    exit 0
  fi
done <<< "$NEW_ADR_FILES"

# ── Bloquear ──────────────────────────────────────────────────────────────────
cat >&2 <<EOF
ADR GATE BLOQUEADO

El archivo que intentas editar requiere un ADR previo:
  → $FILE
  (guardado por regla: $GUARDED_REASON)

Para continuar:
  1. Activa el skill Architect: lee .agents/skills/software/architecture/architect/SKILL.md
  2. Diseña la decisión y sus trade-offs
  3. Activa el skill ADR Writer: lee .agents/skills/software/architecture/adr-writer/SKILL.md
  4. Crea ${ADR_PATH}/ADR-NNNN-<titulo>.md
  5. Reintenta la edición

Arreglo trivial? Agrega [skip-adr] al mensaje de commit después.
EOF

exit 2
