#!/bin/bash
# install-hooks.sh — Instala guardrails de ai-tooling en cualquier proyecto CC
# Usage: bash ~/ai-tooling/scripts/install-hooks.sh [project-dir]
#
# Discovery dinámico: copia todos los hooks y scripts con "# distributable: true"
# en sus headers. No requiere mantener arrays hardcodeados — agregar el header
# a un nuevo hook/script es suficiente para que se distribuya automáticamente.
#
# También genera settings.local.json con los eventos correctos leídos desde
# los mismos headers (# event, # matcher, # timeout, # async).
# Idempotente: seguro de ejecutar múltiples veces.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_TOOLING_DIR="$(dirname "$SCRIPT_DIR")"
TARGET="${1:-$(pwd)}"
HOOKS_SOURCE="$AI_TOOLING_DIR/.claude/hooks"
SCRIPTS_SOURCE="$AI_TOOLING_DIR/scripts"
HOOKS_TARGET="$TARGET/.claude/hooks"
SCRIPTS_TARGET="$TARGET/scripts"
SETTINGS_FILE="$TARGET/.claude/settings.local.json"

echo "→ Instalando hooks en: $TARGET"
echo "  Fuente hooks:   $HOOKS_SOURCE"
echo "  Fuente scripts: $SCRIPTS_SOURCE"
echo ""

mkdir -p "$HOOKS_TARGET"
mkdir -p "$SCRIPTS_TARGET"

# ── 1. Copiar archivos con # distributable: true ──────────────────────────────

copy_distributable() {
    local src_dir="$1"
    local dst_dir="$2"
    local label="$3"
    local count=0
    for f in "$src_dir"/*.sh; do
        [ -f "$f" ] || continue
        grep -q "^# distributable: true" "$f" 2>/dev/null || continue
        cp "$f" "$dst_dir/$(basename "$f")"
        chmod +x "$dst_dir/$(basename "$f")"
        echo "  ✓ $(basename "$f")"
        count=$((count + 1))
    done
    echo "  → $count $label copiados"
}

echo "Hooks:"
copy_distributable "$HOOKS_SOURCE" "$HOOKS_TARGET" "hooks"
echo ""
echo "Scripts:"
copy_distributable "$SCRIPTS_SOURCE" "$SCRIPTS_TARGET" "scripts"
echo ""

# ── 2. Generar settings.local.json desde headers de hooks ────────────────────
# Usa $CLAUDE_PROJECT_DIR para rutas absolutas robustas en cualquier CWD.
# Cada hook genera su propio grupo (un hook por grupo) — Claude Code fusiona
# múltiples grupos con el mismo matcher en tiempo de ejecución.

PRE_FILE=$(mktemp)
POST_FILE=$(mktemp)
echo "[]" > "$PRE_FILE"
echo "[]" > "$POST_FILE"

for f in "$HOOKS_SOURCE"/*.sh; do
    [ -f "$f" ] || continue
    grep -q "^# distributable: true" "$f" 2>/dev/null || continue

    name=$(basename "$f")
    event=$(grep "^# event:" "$f" | head -1 | awk '{print $3}')
    matcher=$(grep "^# matcher:" "$f" | head -1 | awk '{print $3}')
    timeout_val=$(grep "^# timeout:" "$f" | head -1 | awk '{print $3}')
    is_async=false
    grep -q "^# async: true" "$f" && is_async=true

    [ -z "$event" ] || [ -z "$matcher" ] || [ -z "$timeout_val" ] && continue

    cmd_path='"$CLAUDE_PROJECT_DIR"/.claude/hooks/'"$name"

    if $is_async; then
        hook_json=$(jq -n \
            --arg matcher "$matcher" \
            --arg cmd "$cmd_path" \
            --argjson timeout "$timeout_val" \
            '{matcher: $matcher, hooks: [{type: "command", command: $cmd, timeout: $timeout, async: true}]}')
    else
        hook_json=$(jq -n \
            --arg matcher "$matcher" \
            --arg cmd "$cmd_path" \
            --argjson timeout "$timeout_val" \
            '{matcher: $matcher, hooks: [{type: "command", command: $cmd, timeout: $timeout}]}')
    fi

    if [ "$event" = "PreToolUse" ]; then
        tmp=$(mktemp)
        jq --argjson entry "$hook_json" '. + [$entry]' "$PRE_FILE" > "$tmp" && mv "$tmp" "$PRE_FILE"
    elif [ "$event" = "PostToolUse" ]; then
        tmp=$(mktemp)
        jq --argjson entry "$hook_json" '. + [$entry]' "$POST_FILE" > "$tmp" && mv "$tmp" "$POST_FILE"
    fi
done

HOOKS_JSON=$(jq -n \
    --slurpfile pre "$PRE_FILE" \
    --slurpfile post "$POST_FILE" \
    '{PreToolUse: $pre[0], PostToolUse: $post[0]}')

rm -f "$PRE_FILE" "$POST_FILE"

# ── 3. Actualizar settings.local.json ─────────────────────────────────────────
TMP_FILE="${SETTINGS_FILE}.tmp.$$"

if [ -f "$SETTINGS_FILE" ]; then
    jq --argjson hooks "$HOOKS_JSON" '.hooks = $hooks' \
        "$SETTINGS_FILE" > "$TMP_FILE" && mv "$TMP_FILE" "$SETTINGS_FILE"
    echo "✓ settings.local.json actualizado (hooks reemplazados, resto preservado)"
else
    jq -n --argjson hooks "$HOOKS_JSON" '{"hooks": $hooks}' > "$SETTINGS_FILE"
    echo "✓ settings.local.json creado"
fi

echo ""
echo "── Verificación ────────────────────────────────────────────────────────"
echo ""
echo "Hooks instalados en $HOOKS_TARGET:"
ls -1 "$HOOKS_TARGET/"

echo ""
echo "PreToolUse hooks registrados:"
jq -r '.hooks.PreToolUse[].hooks[].command' "$SETTINGS_FILE" 2>/dev/null | sed 's|"$CLAUDE_PROJECT_DIR"/||'

echo ""
echo "PostToolUse hooks registrados:"
jq -r '.hooks.PostToolUse[].hooks[].command' "$SETTINGS_FILE" 2>/dev/null | sed 's|"$CLAUDE_PROJECT_DIR"/||'

echo ""
echo "Prueba rápida de block-dangerous (debe salir exit 2 = bloqueado):"
echo '{"tool_name":"Bash","tool_input":{"command":"git reset --hard HEAD"}}' | \
    bash "$HOOKS_TARGET/block-dangerous.sh" 2>&1 && echo "⚠ Salió exit 0 — revisar" || echo "✓ bloqueado correctamente (exit $?)"
