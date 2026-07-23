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
# completo. No se cierra aquí — es una acción más visible en el diff.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

_block() {
  echo "BLOCKED: $1" >&2
  echo "Esto es una decisión humana, no del agente. Si el gate bloquea por error," >&2
  echo "resuelve lo que pide (Read del SKILL.md correspondiente) o pide al usuario" >&2
  echo "que haga este cambio directamente." >&2
  exit 2
}

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
    # Strip fd-duplication redirects (2>&1, 1>&2, etc.) before checking — those
    # redirect STREAMS, not files, and would otherwise false-positive on any
    # read-only command (e.g. `ls ... 2>&1`) that merely mentions the path.
    SANITIZED=$(echo "$CMD" | sed -E 's/[0-9]?>&[0-9]//g')
    if echo "$SANITIZED" | grep -qE '(no-skill-gate|skill-load-gate\.sh)' \
       && echo "$SANITIZED" | grep -qE '(>|touch\b|tee\b|cp\b|mv\b|sed\s+-i)'; then
      _block "escritura vía Bash a no-skill-gate o skill-load-gate.sh"
    fi
    ;;
esac
exit 0
