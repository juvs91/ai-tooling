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
# tiene rollback via git. Rollback = copiar los .bak de vuelta y borrar lo
# listado en CREATED.txt.
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
  trap 'rm -f "$MANIFEST_WORK"' EXIT
fi

# sha256sum (coreutils, todo Linux y macOS 14+) o shasum (perl; macOS viejo). Mismo
# hash con ambos → manifests portables. Sin ninguno, abortar: hashes vacíos harían que
# toda modificación local pareciera "al día" y se sobrescribiera (fail-open).
if command -v sha256sum >/dev/null 2>&1; then
    sha() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
    sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
    echo "ERROR: se requiere sha256sum o shasum" >&2; exit 1
fi

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
OWNED_NAMES=()       # hooks que el harness puede registrar (event + timeout), incl. deduplicados
REGISTERED_NAMES=()  # los que efectivamente van a settings.local.json

for f in "$HOOKS_SOURCE"/*.sh; do
    [ -f "$f" ] || continue
    grep -q "^# distributable: true" "$f" 2>/dev/null || continue

    name=$(basename "$f")
    event=$(grep "^# event:" "$f" | head -1 | awk '{print $3}')
    matcher=$(grep "^# matcher:" "$f" | head -1 | sed -e 's/^# matcher:[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/')
    timeout_val=$(grep "^# timeout:" "$f" | head -1 | awk '{print $3}')
    is_async=false
    grep -q "^# async: true" "$f" && is_async=true

    # Librerías (sin event/timeout, ej. task-scope-lib.sh) no se registran ni son "propias"
    { [ -z "$event" ] || [ -z "$timeout_val" ]; } && continue
    OWNED_NAMES+=("$name")

    # Dedupe: si settings.json (tracked, del proyecto) ya registra este script,
    # no duplicar en settings.local.json
    if [ -f "$SETTINGS_TRACKED" ] && grep -q "/$name" "$SETTINGS_TRACKED"; then
        DEDUPED="$DEDUPED $name"
        continue
    fi
    REGISTERED_NAMES+=("$name")

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

# ── Merge con settings.local.json (ADR-0074) ──────────────────────────────────
# Antes: `.hooks = $hooks` reemplazaba TODO y borraba los hooks propios del
# consumer. Ahora se quitan solo los hooks del harness — hook por hook, no el
# grupo entero (un hook ajeno puede compartir grupo) — y se agregan los nuevos.
# Propios del harness = registrables actuales ∪ manifest.registered (corrida
# previa) ∪ hooks que el manifest dice que el harness copió. Match por segmento
# de path (.claude/hooks/<nombre> absoluto, relativo o entre comillas).
OWNED_JSON=$(
  { printf '%s\n' "${OWNED_NAMES[@]+"${OWNED_NAMES[@]}"}"
    jq -r '(.registered // [])[]' "$MANIFEST_WORK" 2>/dev/null
    jq -r '.files | keys[] | select(startswith(".claude/hooks/")) | ltrimstr(".claude/hooks/")' "$MANIFEST_WORK" 2>/dev/null
  } | grep -v '^$' | sort -u | jq -R . | jq -s .
)
if [ -f "$SETTINGS_FILE" ] && ! jq empty "$SETTINGS_FILE" 2>/dev/null; then
    echo "✗ ABORTA: $SETTINGS_FILE no es JSON válido — no se escribió nada. Corrígelo y reintenta." >&2
    exit 1
fi
OLD_SETTINGS='{}'
[ -f "$SETTINGS_FILE" ] && OLD_SETTINGS=$(cat "$SETTINGS_FILE")
JQ_OWNED=$(cat <<'JQ'
def owned($names): . as $c | any($names[]; . as $b | $c | test("(^|[\\s/\"'])\\.claude/hooks/" + ($b | gsub("\\."; "\\.")) + "([\\s\"';&|)]|$)"));
JQ
)
NEW_SETTINGS=$(jq --argjson owned "$OWNED_JSON" --argjson new "$HOOKS_JSON" "$JQ_OWNED"'
  . as $root
  | (.hooks // {}) as $old
  | ($old | with_entries(.value |= (map(.hooks |= map(select(((.command // "") | owned($owned)) | not)))
                                    | map(select(.hooks | length > 0))))) as $kept
  | (reduce ($new | keys[]) as $k ($kept; .[$k] = ((.[$k] // []) + $new[$k])))
  | with_entries(select(.value | length > 0)) as $merged
  | $root | .hooks = $merged' <<< "$OLD_SETTINGS")
PRESERVED=$(jq --argjson owned "$OWNED_JSON" "$JQ_OWNED"'[(.hooks // {})[][].hooks[] | select(((.command // "") | owned($owned)) | not)] | length' <<< "$OLD_SETTINGS")
REMOVED=$(jq --argjson owned "$OWNED_JSON" "$JQ_OWNED"'[(.hooks // {})[][].hooks[] | select((.command // "") | owned($owned))] | length' <<< "$OLD_SETTINGS")
SETTINGS_CHANGED=true
if [ -f "$SETTINGS_FILE" ] && [ "$(jq -S . <<< "$NEW_SETTINGS")" = "$(jq -S . "$SETTINGS_FILE")" ]; then
    SETTINGS_CHANGED=false
fi

echo "Registro settings.local.json:"
for event_name in $(jq -r 'keys[]' <<< "$HOOKS_JSON"); do
    n=$(jq -r --arg e "$event_name" '.[$e] | length' <<< "$HOOKS_JSON")
    echo "  $event_name: $n hook(s)"
done
if [ -n "$DEDUPED" ]; then echo "  dedupe (ya en settings.json, no se duplican):$DEDUPED"; fi
echo "  preservados (hooks propios del consumer): $PRESERVED · reemplazados del harness: $REMOVED"
$SETTINGS_CHANGED || echo "  sin cambios"
echo ""

# ── Config de worktrees nativos (ADR-0074) ────────────────────────────────────
# Crea solo lo que falta, nunca sobrescribe, y respeta una eliminación hecha por
# el usuario (queda "declinado" en el manifest; --force lo recrea). No corre
# dentro de un worktree enlazado (escribiría en la rama de ese worktree).
WTI="$TARGET/.worktreeinclude"
WTI_TEMPLATE="$AI_TOOLING_DIR/templates/worktreeinclude.template"
GITIGNORE_CLAUDE="$TARGET/.claude/.gitignore"
WTI_ACTION="noop"; GI_ACTION="noop"
_gd=$(git -C "$TARGET" rev-parse --path-format=absolute --git-dir 2>/dev/null || true)
_gc=$(git -C "$TARGET" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
echo "Worktrees nativos:"
if [ -n "$_gd" ] && [ "$_gd" != "$_gc" ]; then
    echo "  SALTAR     — el destino es un worktree enlazado"
else
    if [ -f "$WTI" ]; then
        echo "  al día     .worktreeinclude (del proyecto, no se toca)"
    elif [ -n "$(m_entry ".worktreeinclude")" ] && ! $FORCE; then
        echo "  SALTAR     .worktreeinclude — eliminado por el usuario (declinado; --force para recrear)"
    elif [ -f "$WTI_TEMPLATE" ]; then
        echo "  INSTALAR   .worktreeinclude (plantilla genérica: .env, .env.local, settings.local.json)"
        WTI_ACTION="create"
    fi
    if [ -f "$GITIGNORE_CLAUDE" ] && grep -qxE '[[:space:]]*/?worktrees/?[[:space:]]*' "$GITIGNORE_CLAUDE"; then
        echo "  al día     .claude/.gitignore ignora worktrees/"
    elif [ "$(jq -r '.provisioned.gitignore_worktrees // false' "$MANIFEST_WORK")" = "true" ] && ! $FORCE; then
        echo "  SALTAR     .claude/.gitignore — /worktrees/ quitado por el usuario (declinado; --force para re-agregar)"
    else
        echo "  AGREGAR    /worktrees/ a .claude/.gitignore"
        GI_ACTION="append"
    fi
fi
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
# settings.local.json: respaldar SOLO si el merge (calculado arriba) lo cambia.
$SETTINGS_CHANGED && backup_file "$SETTINGS_FILE"
[ "$GI_ACTION" = "append" ] && backup_file "$GITIGNORE_CLAUDE"
if [ "$WTI_ACTION" = "create" ]; then
    # Archivo nuevo: no hay .bak posible — su rollback es borrarlo
    mkdir -p "$BACKUP_DIR"; echo ".worktreeinclude" >> "$BACKUP_DIR/CREATED.txt"
    BACKED=$((BACKED + 1))
fi
if [ "$BACKED" -gt 0 ]; then
    echo "✓ backup ($BACKED untracked/creados): $BACKUP_DIR/ (rollback = copiar los .bak y borrar lo listado en CREATED.txt)"
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

# settings.local.json: merge calculado arriba; solo se escribe si cambió
if $SETTINGS_CHANGED; then
    TMP_FILE="${SETTINGS_FILE}.tmp.$$"
    printf '%s\n' "$NEW_SETTINGS" | jq . > "$TMP_FILE" && mv "$TMP_FILE" "$SETTINGS_FILE"
    echo "✓ settings.local.json actualizado (hooks propios preservados: $PRESERVED)"
fi
REG_JSON=$(printf '%s\n' "${REGISTERED_NAMES[@]+"${REGISTERED_NAMES[@]}"}" | grep -v '^$' | jq -R . | jq -s .)
tmp=$(mktemp); jq --argjson r "$REG_JSON" '.registered = $r' "$MANIFEST_WORK" > "$tmp" && mv "$tmp" "$MANIFEST_WORK"

# Config de worktrees
if [ "$WTI_ACTION" = "create" ]; then
    cp "$WTI_TEMPLATE" "$WTI"
    m_set ".worktreeinclude" "$(sha "$WTI")" "harness"
    echo "  ✓ .worktreeinclude creado"
fi
if [ "$GI_ACTION" = "append" ]; then
    # Salto de línea final garantizado y append O_APPEND (convive con el append
    # concurrente de sessions/ de skill-autoload/plan-mode-gate)
    if [ -s "$GITIGNORE_CLAUDE" ] && [ -n "$(tail -c1 "$GITIGNORE_CLAUDE")" ]; then printf '\n' >> "$GITIGNORE_CLAUDE"; fi
    printf '/worktrees/\n' >> "$GITIGNORE_CLAUDE"
    tmp=$(mktemp); jq '.provisioned.gitignore_worktrees = true' "$MANIFEST_WORK" > "$tmp" && mv "$tmp" "$MANIFEST_WORK"
    echo "  ✓ /worktrees/ agregado a .claude/.gitignore"
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
