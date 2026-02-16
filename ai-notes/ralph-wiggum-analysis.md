# Ralph Wiggum / Ralph Loop - Analisis Exhaustivo

## Que es Ralph Wiggum?

Tecnica de desarrollo autonomo creada por Geoffrey Huntley. El concepto es simple:
**un `while true` que alimenta a un agente IA con el mismo prompt repetidamente**,
donde el agente ve su trabajo previo en archivos y git history para iterar hasta completar.

Nombre inspirado en Ralph Wiggum de Los Simpsons: persistencia a pesar de fallas.

---

## 3 Implementaciones Analizadas

### 1. Plugin Oficial de Claude Code (`ralph-loop`)
- **Repo**: `anthropics/claude-code/plugins/ralph-loop/`
- **Autores**: Anthropic (Daisy Hollman)
- **Instalacion**: Via `/plugin` command en Claude Code
- **Mecanismo**: Stop hook nativo de Claude Code
- **Archivos**: 8 archivos (plugin.json, hooks.json, stop-hook.sh, setup-ralph-loop.sh, 3 commands .md, README)
- **RECOMENDADO**: Mas simple, integrado nativamente, sin dependencias externas

### 2. frankbria/ralph-claude-code
- **Repo**: https://github.com/frankbria/ralph-claude-code
- **Mecanismo**: Framework bash externo con monitoring, rate limiting, circuit breaker
- **Instalacion**: `git clone` + `./install.sh` + `ralph-enable` por proyecto
- **Features extra**: tmux dashboard, session management, rate limiting (100/hr), task import
- **Trade-off**: Mas features pero mas complejo y mantiene estado externo

### 3. ghuntley/how-to-ralph-wiggum (El Original)
- **Repo**: https://github.com/ghuntley/how-to-ralph-wiggum
- **Mecanismo**: Guia/playbook, no software instalable
- **Concepto**: `cat PROMPT.md | claude -p --dangerously-skip-permissions`
- **Fases**: 1) Definir reqs → 2) Planning prompt → 3) Building prompt → loop
- **Filosofia**: "Let Ralph Ralph" — 3 fases, 2 prompts, 1 loop

---

## Analisis Detallado: Plugin Oficial (`ralph-loop`)

### Arquitectura

```
[Usuario] → /ralph-loop "prompt" --max-iterations 20 --completion-promise "DONE"
                ↓
[setup-ralph-loop.sh] → Crea .claude/ralph-loop.local.md (estado)
                ↓
[Claude Code] → Trabaja en la tarea
                ↓
[Claude intenta salir]
                ↓
[Stop Hook: stop-hook.sh] → Lee estado, incrementa iteracion
    ↓ decision: "block"     → Reenvía MISMO prompt
    ↓ decision: allow       → Sale (max_iterations o completion_promise detectado)
```

### Archivos del Plugin

| Archivo | Funcion |
|---------|---------|
| `.claude-plugin/plugin.json` | Metadata: nombre, version, descripcion |
| `hooks/hooks.json` | Registra stop-hook.sh como Stop hook |
| `hooks/stop-hook.sh` | **Core**: intercepta exit, lee transcript, detecta completion, reenvía prompt |
| `scripts/setup-ralph-loop.sh` | Parsea argumentos, crea archivo de estado con frontmatter YAML |
| `commands/ralph-loop.md` | Slash command: `/ralph-loop` |
| `commands/cancel-ralph.md` | Slash command: `/cancel-ralph` |
| `commands/help.md` | Slash command: `/ralph-loop:help` |

### stop-hook.sh - Flujo Detallado

1. Lee stdin (hook input JSON con `transcript_path`)
2. Verifica `.claude/ralph-loop.local.md` existe
3. Parsea frontmatter YAML: iteration, max_iterations, completion_promise
4. Valida campos numericos (corrupcion check)
5. Verifica max_iterations alcanzado → si: limpia estado, exit 0 (permite salir)
6. Lee transcript JSONL, extrae ultimo mensaje del assistant
7. Busca `<promise>TEXTO</promise>` en output con Perl regex
8. Si promise match exacto → limpia estado, exit 0 (permite salir)
9. Si no: incrementa iteracion, actualiza estado, output JSON:
   ```json
   {"decision": "block", "reason": "PROMPT_TEXT", "systemMessage": "iter N"}
   ```

### Archivo de Estado (.claude/ralph-loop.local.md)

```markdown
---
active: true
iteration: 1
max_iterations: 20
completion_promise: "DONE"
started_at: "2026-02-16T..."
---

Build a REST API for todos. Requirements: CRUD operations, input validation, tests.
Output <promise>DONE</promise> when done.
```

### Comandos

| Comando | Uso |
|---------|-----|
| `/ralph-loop "prompt" --max-iterations N --completion-promise "TEXT"` | Inicia loop |
| `/cancel-ralph` | Cancela loop activo (borra estado) |
| `/ralph-loop:help` | Ayuda del plugin |

### Mejores Practicas para Prompts

1. **Criterios claros de completacion** — definir que significa "terminado"
2. **Metas incrementales** — fases con checkpoints
3. **Auto-correccion** — incluir TDD (write test → implement → run → fix)
4. **Escape hatches** — SIEMPRE usar `--max-iterations`
5. **Completion promise** — usar `<promise>TEXTO</promise>` XML tags

### Cuando Usar Ralph

**Bueno para:**
- Tareas bien definidas con criterios de exito claros
- Tareas que requieren iteracion (tests que pasen)
- Proyectos greenfield donde puedes irte
- Tareas con verificacion automatica (tests, linters)

**No bueno para:**
- Tareas que requieren juicio humano o decisiones de diseno
- Operaciones de un solo paso
- Tareas con criterios de exito ambiguos
- Debugging en produccion

### Seguridad

- El plugin usa el sistema de permisos normal de Claude Code
- NO usa `--dangerously-skip-permissions` (a diferencia del enfoque original de Huntley)
- El Stop hook solo puede block/allow — no ejecuta codigo arbitrario
- `.claude/ralph-loop.local.md` es local al proyecto (`.local.md` = gitignored)

---

## Instalacion

El plugin ya esta disponible en el marketplace oficial. Para instalarlo:

```bash
# Desde dentro de Claude Code:
/plugin ralph-loop
```

O manualmente en settings:
```json
// .claude/settings.local.json o settings.json
{
  "plugins": ["ralph-loop@claude-plugins-official"]
}
```

---

## Resultados Documentados

- 6 repositorios generados overnight en Y Combinator hackathon
- Contrato de $50k completado por $297 en costos de API
- Lenguaje de programacion completo ("cursed") creado en 3 meses
