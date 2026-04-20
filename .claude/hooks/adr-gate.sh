#!/usr/bin/env bash
# adr-gate.sh — PreToolUse hook: bloquea edits a rutas guarded sin ADR staged.
#
# Rutas guardadas (requieren ADR antes de editar):
#   vendor/claude-code-proxy/**/*.py   ← cambios al proxy core
#   .agents/skills/**/*.md             ← cambios a skills existentes
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

# Sin file_path → no es Edit/Write relevante → permitir
[[ -z "$FILE" ]] && exit 0

# Normalizar: quitar ./ inicial
FILE="${FILE#./}"

# ── Rutas que SIEMPRE se permiten ─────────────────────────────────────────────
# 1. El propio archivo es un ADR
if echo "$FILE" | grep -qE 'docs/adr/ADR-[0-9]{4}-.*\.md$'; then
  exit 0
fi

# 2. Archivos de tests
if echo "$FILE" | grep -qE '(/tests?/|/test_|_test\.)'; then
  exit 0
fi

# 3. Archivos de documentación que no sean skills
if echo "$FILE" | grep -qE '\.(md|rst|txt)$' && ! echo "$FILE" | grep -q '\.agents/skills/'; then
  exit 0
fi

# 4. ai-notes/, context/, templates/ — artefactos de sesión
if echo "$FILE" | grep -qE '^(ai-notes|context|templates)/'; then
  exit 0
fi

# ── Comprobar si el archivo es "guarded" ──────────────────────────────────────
GUARDED=false

# vendor/claude-code-proxy/**/*.py
if echo "$FILE" | grep -qE '^vendor/claude-code-proxy/.*\.py$'; then
  GUARDED=true
fi

# .agents/skills/**/*.md (skills existentes — no nuevas creaciones)
if echo "$FILE" | grep -qE '^\.agents/skills/.*\.md$'; then
  GUARDED=true
fi

# Si no es guarded → permitir sin restricción
[[ "$GUARDED" == "false" ]] && exit 0

# ── Verificar si hay un ADR nuevo staged en git ───────────────────────────────
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")

# Buscar ADR-*.md nuevo (untracked ?? o staged A) en docs/adr/
NEW_ADR=$(git -C "$REPO_ROOT" status --porcelain "docs/adr/" 2>/dev/null \
  | grep -E '^(\?\?|A ).*ADR-[0-9]{4}-.*\.md' \
  | head -1)

if [[ -n "$NEW_ADR" ]]; then
  # ADR presente → permitir
  exit 0
fi

# ── Bloquear ──────────────────────────────────────────────────────────────────
cat >&2 <<EOF
ADR GATE BLOQUEADO

El archivo que intentas editar requiere un ADR previo:
  → $FILE

Para continuar:
  1. Activa el skill Architect: lee .agents/skills/software/architecture/architect/SKILL.md
  2. Diseña la decisión y sus trade-offs
  3. Activa el skill ADR Writer: lee .agents/skills/software/architecture/adr-writer/SKILL.md
  4. Crea docs/adr/ADR-NNNN-<titulo>.md (ver docs/adr/ para el siguiente número)
  5. Reintenta la edición

Arreglo trivial? Agrega [skip-adr] al mensaje de commit después.
EOF

exit 2
