#!/bin/bash
# install-hooks.sh — Instala guardrails de ai-tooling en cualquier proyecto CC
# Usage: bash ~/ai-tooling/scripts/install-hooks.sh [project-dir]
#
# Copia los 8 hooks de ai-tooling al proyecto destino y configura
# settings.local.json con todos los eventos correctos.
# Idempotente: seguro de ejecutar múltiples veces.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_TOOLING_DIR="$(dirname "$SCRIPT_DIR")"
TARGET="${1:-$(pwd)}"
HOOKS_SOURCE="$AI_TOOLING_DIR/.claude/hooks"
HOOKS_TARGET="$TARGET/.claude/hooks"
SETTINGS_FILE="$TARGET/.claude/settings.local.json"

echo "→ Instalando hooks en: $TARGET"
echo "  Fuente: $HOOKS_SOURCE"
echo ""

mkdir -p "$HOOKS_TARGET"

# ── 1. Copiar todos los hooks ─────────────────────────────────────────────────
HOOKS_TO_COPY=(
  block-dangerous.sh
  protect-secrets.sh
  config-protection.sh
  quality-gate.sh
  verify-implementation.sh
  migration-gate.sh
  adr-gate.sh
  skill-autoload.sh
)

for hook in "${HOOKS_TO_COPY[@]}"; do
  src="$HOOKS_SOURCE/$hook"
  if [ -f "$src" ]; then
    cp "$src" "$HOOKS_TARGET/$hook"
    chmod +x "$HOOKS_TARGET/$hook"
    echo "✓ $hook"
  else
    echo "⚠ $hook no encontrado en $HOOKS_SOURCE — saltando"
  fi
done

echo ""

# ── 2. Estructura canónica de hooks para settings.local.json ─────────────────
# Usa $CLAUDE_PROJECT_DIR para rutas absolutas robustas en cualquier CWD.
HOOKS_JSON=$(cat <<'ENDJSON'
{
  "PreToolUse": [
    {
      "matcher": "Bash",
      "hooks": [
        {
          "type": "command",
          "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/block-dangerous.sh",
          "timeout": 10
        }
      ]
    },
    {
      "matcher": "Edit|Write|MultiEdit",
      "hooks": [
        {
          "type": "command",
          "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/protect-secrets.sh",
          "timeout": 10
        },
        {
          "type": "command",
          "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/config-protection.sh",
          "timeout": 10
        },
        {
          "type": "command",
          "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/adr-gate.sh",
          "timeout": 10
        }
      ]
    }
  ],
  "PostToolUse": [
    {
      "matcher": "Edit|Write|MultiEdit",
      "hooks": [
        {
          "type": "command",
          "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/quality-gate.sh",
          "timeout": 15,
          "async": true
        },
        {
          "type": "command",
          "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/verify-implementation.sh",
          "timeout": 15
        },
        {
          "type": "command",
          "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/migration-gate.sh",
          "timeout": 10
        }
      ]
    }
  ],
  "UserPromptSubmit": [
    {
      "matcher": "",
      "hooks": [
        {
          "type": "command",
          "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/skill-autoload.sh",
          "timeout": 5
        }
      ]
    }
  ]
}
ENDJSON
)

# ── 3. Actualizar settings.local.json ─────────────────────────────────────────
TMP_FILE="${SETTINGS_FILE}.tmp.$$"

if [ -f "$SETTINGS_FILE" ]; then
  # Reemplazar SOLO la sección .hooks, preservar todo lo demás
  # También limpia la entrada duplicada de block-dangerous con matcher vacío
  jq --argjson hooks "$HOOKS_JSON" '.hooks = $hooks' \
    "$SETTINGS_FILE" > "$TMP_FILE" && mv "$TMP_FILE" "$SETTINGS_FILE"
  echo "✓ settings.local.json actualizado (hooks reemplazados, resto preservado)"
else
  # Crear settings.local.json mínimo con solo los hooks
  jq -n --argjson hooks "$HOOKS_JSON" '{"hooks": $hooks}' > "$SETTINGS_FILE"
  echo "✓ settings.local.json creado"
fi

echo ""
echo "── Verificación ────────────────────────────────────────────────────────"
echo ""
echo "Hooks instalados en $HOOKS_TARGET:"
ls -1 "$HOOKS_TARGET/"

echo ""
echo "Hooks registrados en settings.local.json:"
jq '.hooks | keys[]' "$SETTINGS_FILE"

echo ""
echo "Prueba rápida de block-dangerous (debe salir exit 2 = bloqueado):"
echo '{"tool_name":"Bash","tool_input":{"command":"git reset --hard HEAD"}}' | \
  bash "$HOOKS_TARGET/block-dangerous.sh" 2>&1 && echo "⚠ Salió exit 0 — revisar" || echo "✓ bloqueado correctamente (exit $?)"
