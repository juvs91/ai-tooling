#!/bin/bash
# install-hooks.sh — Instala guardrails de ai-tooling en cualquier proyecto CC
# Usage: bash ~/ai-tooling/scripts/install-hooks.sh [project-dir] [--apply|--dry-run|--force]
#
# ADR-0072 (M3): el harness reporta por default, escribe solo con intención, y
# todo lo que escribe es reversible.
#
#   --dry-run   (default) imprime el plan: qué instalaría, qué actualizaría,
#               qué saltaría y qué registraría. No escribe NADA.
#   --apply     ejecuta el plan.
#   --force     con --apply: sobrescribe también archivos marcados como
#               "modificados localmente" o "adoptados como locales".
#
# Reglas de escritura (manifest .claude/.harness-manifest.json):
#   - Archivo nuevo                    -> instalar
#   - Local == toolbox                 -> al día
#   - Local == sha del manifest        -> actualizar (lo instaló el harness y
#                                         nadie lo tocó)
#   - Local != sha del manifest        -> SALTAR "modificado localmente"
#   - Sin entrada en manifest (adopt)  -> SALTAR "adoptado como local" y
#                                         registrarlo como tal
#
# Dedupe: hooks ya registrados en settings.json del proyecto (match por
# basename del script) NO se duplican en settings.local.json.
#
# Backup: antes de sobrescribir, directorio .claude/.harness-backup-<ts>/ con
# los archivos afectados (como .bak) — SOLO los untracked: lo trackeado ya
# tiene rollback via git. Rollback = copiar los .bak de vuelta.
#
# Discovery dinámico: copia archivos con "# distributable: true" en sus headers.
# Idempotente: seguro de ejecutar múltiples veces.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_TOOLING_DIR="$(dirname "$SCRIPT_DIR")"
TARGET=""
MODE="dry-run"
FORCE=false

for arg in "$@"; do
  case "$arg" in
    --apply) MODE="apply" ;;
    --dry-run) MODE="dry-run" ;;
    --force) FORCE=true ;;
    -*) echo "flag desconocida: $arg" >&2; exit 1 ;;
    *) if [ -z "$TARGET" ]; then TARGET="$arg"; fi ;;
  esac
done
TARGET="${TARGET:-$(pwd)}"

HOOKS_SOURCE="$AI_TOOLING_DIR/.claude/hooks"
SCRIPTS_SOURCE="$AI_TOOLING_DIR/scripts"
HOOKS_TARGET="$TARGET/.claude/hooks"
SCRIPTS_TARGET="$TARGET/scripts"
SETTINGS_FILE="$TARGET/.claude/settings.local.json"
SETTINGS_TRACKED="$TARGET/.claude/settings.json"
MANIFEST="$TARGET/.claude/.harness-manifest.json"

# Manifest de trabajo: si no existe, temporal en memoria (jamás se escribe en
# dry-run; solo se persiste al final de un --apply)
if [ -f "$MANIFEST" ]; then
  MANIFEST_WORK="$MANIFEST"
else
  MANIFEST_WORK=$(mktemp)
  echo '{"files":{}}' > "$MANIFEST_WORK"
fi

sha() { shasum -a 256 "$1" | cut -d' ' -f1; }

m_entry() { jq -r --arg k "$1" '.files[$k] // empty' "$MANIFEST_WORK"; }
m_set() { # m_set <relpath> <sha> <origin>
  local tmp; tmp=$(mktemp)
  jq --arg k "$1" --arg s "$2" --arg o "$3" '.files[$k] = {sha: $s, origin: $o}' \
    "$MANIFEST_WORK" > "$tmp" && mv "$tmp" "$MANIFEST_WORK"
}

MODE_LABEL="$MODE"
$FORCE && MODE_LABEL="$MODE --force"
echo "→ install-hooks [$MODE_LABEL] en: $TARGET"
echo "  Fuente: $AI_TOOLING_DIR"
if [ "$MODE" = "dry-run" ]; then
  echo "  (dry-run: no se escribirá nada — usa --apply para ejecutar)"
fi
echo ""

mkdir -p "$TARGET/.claude"
BACKUP_DIR=""
TO_BACKUP=()

plan_copy() {
    local src_dir="$1" dst_dir="$2" rel_prefix="$3"
    for f in "$src_dir"/*.sh "$src_dir"/*.py; do
        [ -f "$f" ] || continue
        grep -q "^# distributable: true" "$f" 2>/dev/null || continue
        local b rel dst src_sha
        b=$(basename "$f"); rel="$rel_prefix/$b"; dst="$dst_dir/$b"
        src_sha=$(sha "$f")
        if [ ! -f "$dst" ]; then
            echo "  INSTALAR   $rel"
            PLAN+=("$f|$dst|$rel|install")
        else
            local dst_sha; dst_sha=$(sha "$dst")
            if [ "$dst_sha" = "$src_sha" ]; then
                echo "  al día     $rel"
                PLAN+=("$f|$dst|$rel|noop")
            else
                local entry; entry=$(m_entry "$rel")
                if [ -n "$entry" ]; then
                    local m_sha; m_sha=$(echo "$entry" | jq -r '.sha')
                    if [ "$dst_sha" = "$m_sha" ]; then
                        echo "  ACTUALIZAR $rel (instalado por harness, sin toques locales)"
                        PLAN+=("$f|$dst|$rel|update")
                        continue
                    fi
                    echo "  SALTAR     $rel — modificado localmente (usa --force para sobrescribir)"
                    PLAN+=("$f|$dst|$rel|skip-local")
                else
                    echo "  SALTAR     $rel — adoptado como local (sin historia harness; --force para convertir)"
                    PLAN+=("$f|$dst|$rel|adopt")
                fi
            fi
        fi
    done
}

PLAN=()
echo "Hooks:"
plan_copy "$HOOKS_SOURCE" "$HOOKS_TARGET" ".claude/hooks"
echo ""
echo "Scripts:"
plan_copy "$SCRIPTS_SOURCE" "$SCRIPTS_TARGET" "scripts"
echo ""

# ── Generar HOOKS_JSON (registro) con dedupe contra settings.json ────────────
EVENTS_DIR=$(mktemp -d)
DEDUPED=""

for f in "$HOOKS_SOURCE"/*.sh; do
    [ -f "$f" ] || continue
    grep -q "^# distributable: true" "$f" 2>/dev/null || continue

    name=$(basename "$f")

    # Dedupe: si settings.json (tracked, del proyecto) ya registra este script,
    # no duplicar en settings.local.json
    if [ -f "$SETTINGS_TRACKED" ] && grep -q "/$name" "$SETTINGS_TRACKED"; then
        DEDUPED="$DEDUPED $name"
        continue
    fi

    event=$(grep "^# event:" "$f" | head -1 | awk '{print $3}')
    matcher=$(grep "^# matcher:" "$f" | head -1 | sed -e 's/^# matcher:[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/')
    timeout_val=$(grep "^# timeout:" "$f" | head -1 | awk '{print $3}')
    is_async=false
    grep -q "^# async: true" "$f" && is_async=true

    [ -z "$event" ] || [ -z "$timeout_val" ] && continue

    cmd_path='"$CLAUDE_PROJECT_DIR"/.claude/hooks/'"$name"

    if $is_async; then
        hook_json=$(jq -n --arg matcher "$matcher" --arg cmd "$cmd_path" --argjson timeout "$timeout_val" \
            '{matcher: $matcher, hooks: [{type: "command", command: $cmd, timeout: $timeout, async: true}]}')
    else
        hook_json=$(jq -n --arg matcher "$matcher" --arg cmd "$cmd_path" --argjson timeout "$timeout_val" \
            '{matcher: $matcher, hooks: [{type: "command", command: $cmd, timeout: $timeout}]}')
    fi

    event_file="$EVENTS_DIR/$event.json"
    [ -f "$event_file" ] || echo "[]" > "$event_file"
    tmp=$(mktemp)
    jq --argjson entry "$hook_json" '. + [$entry]' "$event_file" > "$tmp" && mv "$tmp" "$event_file"
done

HOOKS_JSON="{}"
for event_file in "$EVENTS_DIR"/*.json; do
    [ -f "$event_file" ] || continue
    event_name=$(basename "$event_file" .json)
    tmp=$(mktemp)
    jq --arg k "$event_name" --slurpfile v "$event_file" '.[$k] = $v[0]' <<< "$HOOKS_JSON" > "$tmp" && HOOKS_JSON=$(cat "$tmp") && rm -f "$tmp"
done
rm -rf "$EVENTS_DIR"

echo "Registro settings.local.json:"
for event_name in $(jq -r 'keys[]' <<< "$HOOKS_JSON"); do
    n=$(jq -r --arg e "$event_name" '.[$e] | length' <<< "$HOOKS_JSON")
    echo "  $event_name: $n hook(s)"
done
if [ -n "$DEDUPED" ]; then echo "  dedupe (ya en settings.json, no se duplican):$DEDUPED"; fi
echo ""

# ── Ejecutar ──────────────────────────────────────────────────────────────────
if [ "$MODE" = "dry-run" ]; then
    echo "── dry-run: 0 cambios escritos. Revisa el plan y corre con --apply."
    exit 0
fi

# Backup SOLO de archivos untracked que efectivamente se van a sobrescribir.
# Lo trackeado ya tiene rollback via git (git checkout/diff) — respaldarlo es
# redundante. El backup es el único salvavidas de lo que git no cubre:
# settings.local.json (untracked por convención) y consumers sin .claude
# trackeado. Directorio plano: rollback = copiar los .bak de vuelta.
is_tracked() { git -C "$TARGET" ls-files --error-unmatch "$1" >/dev/null 2>&1; }

TS=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_DIR="$TARGET/.claude/.harness-backup-$TS"
BACKED=0
backup_file() { # backup_file <dst>
    local dst="$1" rel
    [ -f "$dst" ] || return 0
    rel="${dst#$TARGET/}"
    if is_tracked "$rel"; then
        return 0  # git ya lo cubre
    fi
    mkdir -p "$BACKUP_DIR"
    cp "$dst" "$BACKUP_DIR/$(basename "$dst").bak"
    BACKED=$((BACKED + 1))
}

for entry in "${PLAN[@]}"; do
    IFS='|' read -r src dst rel action <<< "$entry"
    case "$action" in
        update) backup_file "$dst" ;;
        skip-local|adopt) if $FORCE; then backup_file "$dst"; fi ;;
    esac
done
# settings.local.json: respaldar SOLO si esta corrida va a cambiar su bloque
# hooks (comparación canónica con jq -S). Si no cambia, no hay nada que proteger.
if [ -f "$SETTINGS_FILE" ]; then
    CUR_HOOKS=$(jq -S '.hooks // {}' "$SETTINGS_FILE" 2>/dev/null || echo '{}')
    NEW_HOOKS=$(jq -S '.' <<< "$HOOKS_JSON")
    if [ "$CUR_HOOKS" != "$NEW_HOOKS" ]; then backup_file "$SETTINGS_FILE"; fi
fi
if [ "$BACKED" -gt 0 ]; then
    echo "✓ backup ($BACKED untracked): $BACKUP_DIR/ (rollback = copiar los .bak de vuelta)"
fi

# Aplicar copias (cp+mv atómico: un hook puede invocarse a sí mismo — ADR-0047)
mkdir -p "$HOOKS_TARGET" "$SCRIPTS_TARGET"
for entry in "${PLAN[@]}"; do
    IFS='|' read -r src dst rel action <<< "$entry"
    case "$action" in
        install|update)
            tmp="$dst.tmp.$$"
            cp "$src" "$tmp" && chmod +x "$tmp" && mv "$tmp" "$dst"
            m_set "$rel" "$(sha "$src")" "harness"
            echo "  ✓ $rel ($action)"
            ;;
        noop)
            m_set "$rel" "$(sha "$src")" "harness"
            ;;
        adopt)
            m_set "$rel" "$(sha "$dst")" "local"
            ;;
        skip-local)
            if $FORCE; then
                tmp="$dst.tmp.$$"
                cp "$src" "$tmp" && chmod +x "$tmp" && mv "$tmp" "$dst"
                m_set "$rel" "$(sha "$src")" "harness"
                echo "  ✓ $rel (forzado sobre modificación local)"
            fi
            ;;
    esac
done

# Actualizar settings.local.json (preserva todo lo demás)
TMP_FILE="${SETTINGS_FILE}.tmp.$$"
if [ -f "$SETTINGS_FILE" ]; then
    jq --argjson hooks "$HOOKS_JSON" '.hooks = $hooks' "$SETTINGS_FILE" > "$TMP_FILE" && mv "$TMP_FILE" "$SETTINGS_FILE"
    echo "✓ settings.local.json actualizado (resto preservado)"
else
    jq -n --argjson hooks "$HOOKS_JSON" '{"hooks": $hooks}' > "$SETTINGS_FILE"
    echo "✓ settings.local.json creado"
fi

# Persistir manifest (si era temporal, copiarlo al consumer)
if [ "$MANIFEST_WORK" != "$MANIFEST" ]; then
  cp "$MANIFEST_WORK" "$MANIFEST"
fi
echo "✓ manifest actualizado: $MANIFEST"

echo ""
echo "── Verificación ────────────────────────────────────────────────────────"
for event_name in $(jq -r '.hooks | keys[]' "$SETTINGS_FILE" 2>/dev/null); do
    echo "$event_name:"
    jq -r --arg e "$event_name" '.hooks[$e][].hooks[].command' "$SETTINGS_FILE" 2>/dev/null | sed 's|.*hooks/|  |'
done
