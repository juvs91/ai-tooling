#!/usr/bin/env bash
# distributable: true
# event: PreToolUse
# matcher: Write|Edit|Bash
# timeout: 5
# protect-skill-gate-bypass.sh — impide que el AGENTE (no un humano) neutralice
# skill-load-gate.sh: ni creando/editando el marker .claude/no-skill-gate, ni
# editando skill-load-gate.sh (el único archivo que define qué nombre de
# archivo cuenta como bypass — sin esta segunda protección, renombrar el
# marker + editar el hook lograría el mismo resultado).
#
# El bypass sigue funcionando igual si un HUMANO crea el archivo fuera de las
# tools del agente (IDE, terminal directo).
#
# Gap residual aceptado: el agente aún podría borrar el registro de
# skill-load-gate.sh en .claude/settings.json para desactivar el hook por
# completo, o nombrar el archivo sin escribirlo (globs, variables armadas por
# partes). Es un badén, no una frontera: el gate además acepta el marker
# <sid>-skill-loaded.
#
# Ramal Bash (ADR-0074): bloquea si el comando MENCIONA un archivo protegido Y
# tiene una señal de escritura. Se eximen solo construcciones cuyo destino es
# probadamente seguro (redirecciones a /dev/null, a un fd o a otra ruta literal;
# tee y cp hacia otra ruta). Antes cualquier `>` en el comando (ej. 2>/dev/null)
# bloqueaba lecturas, y `rm`, `dd of=`, `python -c`, `find -delete` o
# `git checkout --` no se detectaban.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

_block() {
  echo "BLOCKED: $1" >&2
  echo "Esto es una decisión humana, no del agente. Si el gate bloquea por error," >&2
  echo "resuelve lo que pide (Read del SKILL.md correspondiente) o pide al usuario" >&2
  echo "que haga este cambio directamente." >&2
  exit 2
}

NAMES_RE='skill-load-gate\.sh|no-skill-gate'

case "$TOOL" in
  Write|Edit)
    FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
    [ -z "$FILE" ] && exit 0
    case "$(basename "$FILE")" in
      no-skill-gate) _block "creación/edición de .claude/no-skill-gate" ;;
      skill-load-gate.sh) _block "edición de skill-load-gate.sh" ;;
    esac
    ;;
  Bash)
    CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
    [ -z "$CMD" ] && exit 0
    printf '%s\n' "$CMD" | grep -qE "$NAMES_RE" || exit 0
    CWD=$(echo "$INPUT" | jq -r '.cwd // "."')

    # 1. Redirecciones (destino entre comillas completo; se corta en ; & | ( ))
    SQ="'"; DQ='"'
    REDIR_RE="[0-9]*(>>|>\\||&>>|&>|>&|<>|>)[[:space:]]*((${DQ}[^${DQ}]*${DQ}|${SQ}[^${SQ}]*${SQ}|[^[:space:];&|()<>${DQ}${SQ}])+)"
    UNRESOLVED=0
    while IFS= read -r r; do
      [ -z "$r" ] && continue
      tgt=$(printf '%s' "$r" | sed -E 's/^[0-9]*(>>|>\||&>>|&>|>&|<>|>)[[:space:]]*//' | tr -d "\"'")
      printf '%s' "$tgt" | grep -qE "$NAMES_RE" && _block "redirección hacia un archivo protegido"
      case "$tgt" in
        -|[0-9]|[0-9][0-9]|/dev/null|/dev/stderr|/dev/stdout|/dev/tty) ;;
        *'$'*|*'`'*|*'*'*|*'?'*|*'['*|*'{'*) UNRESOLVED=1 ;;
      esac
    done < <(printf '%s\n' "$CMD" | grep -oE "$REDIR_RE")
    REST=$(printf '%s\n' "$CMD" | sed -E "s/$REDIR_RE/ /g")
    [ "$UNRESOLVED" = "1" ] && _block "redirección con destino no resuelto en un comando que menciona un archivo protegido"
    # Un `>` que sobrevivió (comilla sin cerrar) = redirección no resuelta (fail-closed)
    printf '%s' "$REST" | grep -q '>' && _block "redirección no resuelta en un comando que menciona un archivo protegido"

    # 2-3. Cláusulas tee/cp: se evalúan por segmento y, si son seguras, se quitan
    PREFIX_WORDS='^((sudo|env|command|exec|time|nohup)[[:space:]]+|timeout[[:space:]]+[0-9]+[[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*'
    while IFS= read -r seg; do
      seg_trim=$(printf '%s' "$seg" | sed -E 's/^[[:space:]]+//')
      core=$(printf '%s' "$seg_trim" | sed -E "s/$PREFIX_WORDS//")
      case "$core" in
        tee|tee\ *)
          printf '%s' "$core" | grep -qE "$NAMES_RE" && _block "tee hacia un archivo protegido"
          REST=${REST//"$seg"/ } ;;
        cp\ *)
          printf '%s' "$core" | grep -qE "$NAMES_RE" || continue
          # shellcheck disable=SC2086
          set -- $core; shift
          last=""; for a in "$@"; do
            case "$a" in -t|--target-directory*) _block "cp con directorio destino (-t) y un archivo protegido" ;; esac
            last="$a"
          done
          last=$(printf '%s' "$last" | tr -d "\"'")
          printf '%s' "$last" | grep -qE "$NAMES_RE" && _block "cp hacia un archivo protegido"
          case "$last" in */|*.|'$'*|*'`'*|*'*'*|*'?'*|*'['*|*'{'*) _block "cp con destino no resuelto o directorio" ;; esac
          case "$last" in /*) dst="$last" ;; *) dst="$CWD/$last" ;; esac
          [ -d "$dst" ] && _block "cp hacia un directorio con un archivo protegido como origen"
          REST=${REST//"$seg"/ } ;;
      esac
    done < <(printf '%s\n' "$REST" | tr ';&|' '\n\n\n')

    printf '%s\n' "$REST" | grep -qE "$NAMES_RE" || exit 0

    # 5. Fallback: verbo de escritura en posición de comando (línea por línea)
    POS='(^|[;&|(`{!]|\$\(|(^|[[:space:]])(then|do|else|elif|if|while|until|xargs|-exec)[[:space:]])[[:space:]]*'
    PRE='((sudo|env|command|exec|time|nohup)[[:space:]]+|timeout[[:space:]]+[0-9]+[[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*'
    VERB='(\\|/[^[:space:]]*/)?(touch|tee|mv|install|ln|rm|unlink|truncate|chmod|chown|dd|rsync|eval|xargs|patch|sed[[:space:]]+(-[a-zA-Z]*i|--in-place)|perl[[:space:]]+-[a-zA-Z]*[ie]|(python3?|node|ruby)[[:space:]]+(-c|-e|-[[:space:]]|-$|<<)|(ba|z)?sh[[:space:]]+-c|find[[:space:]].*-(delete|exec)|git[[:space:]]+(checkout|restore|apply|stash|mv|rm))([[:space:]]|$)'
    printf '%s\n' "$REST" | grep -qE "${POS}${PRE}${VERB}" \
      && _block "escritura vía Bash a no-skill-gate o skill-load-gate.sh"
    ;;
esac
exit 0
