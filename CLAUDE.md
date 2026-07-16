# AI-Tooling Project Instructions

## AUTO-SKILL-LOAD — Primera acción obligatoria de cada sesión

**Tu PRIMER tool call en cada respuesta DEBE ser el Skill tool con `skill="workflow-coordinator"`.**
Esto es no-negociable. No respondas texto al usuario hasta haber llamado el Skill tool.
`workflow-coordinator` detectará el intent del usuario y enrutará al skill correcto.

**Excepción:** Si ya hay un skill activo en el contexto de esta sesión, omite este paso.

---

## Plan Mode — Triggers Obligatorios

**MANDATORY. No-negociable. Aplica en TODA sesión.**

DEBES llamar `EnterPlanMode` ANTES de responder cuando se cumpla CUALQUIERA de estos:
1. La tarea requiere modificar ≥3 archivos
2. El usuario dice: "planea", "diseña", "propón", "qué harías", "cómo abordarías", "dame un approach", "make a plan"
3. La tarea es ambigua o arquitectónica (nueva feature, refactor multi-módulo, integración)
4. Necesitas explorar >2 archivos antes de saber qué cambiar

DEBES llamar `ExitPlanMode` cuando:
1. El plan está completo y escrito en el plan file
2. El usuario dice "implementa", "hazlo", "procede", "apruebo"

**Regla de auto-detección:** Si en tu respuesta vas a escribir pasos, diseño, o "aquí está mi plan" — para. Llama EnterPlanMode primero.

---

## Mandatory: Read before working
- ALWAYS read `ai-notes/AI_LEARNING.md` at the start of every session (if it exists)
- ALWAYS read `AGENTS.md` antes de cualquier tarea no trivial — contiene la tabla de routing de skills (qué skill cargar para cada tipo de tarea)
- Ante nueva subtarea mid-session: re-verifica la tabla de routing en `AGENTS.md`

## Agent Skills
Skills en `.agents/skills/`. Routing completo con descripción de capacidad en `AGENTS.md`.
Índice para discovery mid-session: `.agents/skills/skills.md`.
Sync automático via `sync_skills.sh` (throttle 24h). Force: `bash .agents/sync_skills.sh --force`

## Guardrails
- Do NOT guess or fabricate file paths, commands, or outputs
- Do NOT dump large outputs into chat — write everything to `ai-notes/`
- Do NOT use MCP tools autonomously. Only call MCP tools when the user explicitly requests it (e.g., "busca en Jira", "consulta la base de datos", "busca en la web"). Nunca invoques MCP tools de forma proactiva sin una petición directa del usuario.
- Do NOT read the same file more than twice consecutively. Re-reading the same file in a loop is a bug — break it and move on.

## Feedback Loop
- At the end of every session, update `ai-notes/AI_LEARNING.md` with:
  - Technical decisions made and why
  - Errors encountered and how they were resolved
  - Patterns that worked or failed
- ALL project knowledge goes to `ai-notes/` (shared with team and future agents)

## Project Structure

### Core
- `vendor/claude-code-proxy/` — Anthropic→OpenAI proxy (hot-reload via bind mount)
- `scripts/` — Workflow CLI tools (cc-proxy-up, cc-switch, cc-health, cc-chat, cc-proxy-init.sh)
- `bin/` — Standalone utilities (ollama-up, ollama-down, ollama-status, ollama-model)
- `profile-envs/` — Per-provider environment configs
- `cloud-provider-ymls/` — Docker compose overrides per provider

### Knowledge & Enforcement
- `templates/` — Workflow enforcement templates (GUARDRAILS, AI_CONTEXT, AI_PLAN, AI_LEARNING)
- `docs/` — Project documentation (organized by `documentation.sections` from settings.json)
- `ai-notes/` — Session artifacts (learnings, analyses, plans)

### MCP Servers
Configured in `.mcp.json` → `mcpServers`:
- `alloydb` — AlloyDB queries (postgres-mcp)
- `atlassian` — Jira/Confluence (uvx mcp-atlassian)
- `bitbucket` — Bitbucket PRs/repos (mismo script que atlassian con arg `bitbucket`)
- `squit` — Legacy SP search (npx mcp-remote)
- `cloudsql` — CloudSQL wrapper (scripts/cloudsql-mcp.sh)
- `context7` — Live library/framework docs (npx @upstash/context7-mcp)
- `serper` — Web search (npx serper-search-scrape-mcp-server)
- `playwright` — Browser automation (npx @executeautomation/playwright-mcp-server)
- `sequential-thinking` — Chain-of-thought reasoning (npx @modelcontextprotocol/server-sequential-thinking)
- `memory` — Cross-session persistent facts (npx @modelcontextprotocol/server-memory)

## Skills

### Workflow Skills (via Skill tool)
| Skill | Description |
|-------|-------------|
| `workflow-coordinator` | Detecta intent y enruta al skill correcto (AUTO-LOAD en cada sesión) |
| `ticket-planner` | Planificación Jira con pre-planning bloat (11 fuentes) y grokking refinement |
| `ticket-implementation` | Ejecución 7-hop multihop grounding con verificación iterativa |

### Database Skills (via AlloyDB MCP)
| Skill | Command | Description |
|-------|---------|-------------|
| `alloydb-query` | `mcp__alloydb__query_tool` | Query pricing data |
| `alloydb-debug` | `mcp__alloydb__query_tool` | Diagnose calculation errors |
| `cascade-analyzer` | `mcp__alloydb__query_tool` | Analyze price/freight cascade |

### Dynamic Skills (via scripts)
| Skill | Command | Documentation |
|-------|---------|---------------|
| `api-test` | `./scripts/api-test` | Tests API integration |
| `validation-checker` | `./scripts/validation-checker` | Validates business rules |

### Project Skills (via MCP)
| Skill | Commands | Docs |
|-------|-----------|------|
| `jira-context` | `mcp__atlassian__jira_get_issue`, `mcp__atlassian__jira_search` | docs/jira/ |
| `sp-search` | `mcp__squit__squit_search`, `mcp__squit__squit_get_code` | docs/sp-search/ |
| `tunnel-health` | `./scripts/tunnel-health` | docs/tunnel-health/ |

## MCP Credentials

All MCP credentials are stored as environment variables (see `.env`):

| Provider | Env Variables | Description |
|----------|---------------|-------------|
| **AlloyDB** | `ALLOYDB_PASSWORD` | Password for postgres connection |
| **Atlassian** | `ATLASSIAN_CONFLUENCE_TOKEN`, `ATLASSIAN_JIRA_API_TOKEN` | Confluence & Jira tokens |
| **Squit** | `SQUIT_API_KEY` | API key |
| **CloudSQL** | `WPC_ENV` + `PROD/QA/DEV_*` | Per-environment DB credentials |
| **Serper** | `SERPER_API_KEY` | Web search API key |

## ADR-First Gate
Antes de editar `vendor/claude-code-proxy/**/*.py` o `.agents/skills/**/*.md` debes
tener un ADR nuevo en staging (`docs/adr/ADR-NNNN-*.md`).

- **Hook Claude Code** (PreToolUse): `.claude/hooks/adr-gate.sh` bloquea la edición
- **Hook git** (pre-commit): `tools/check_adr_gate.py` bloquea el commit
- **Instalar git hook**: `bash tools/install_hooks.sh`
- **Bypass trivial**: agrega `[skip-adr]` al commit message

## Hooks de Seguridad (`.claude/hooks/`)

Claude Code ejecuta estos scripts automáticamente. **Contrato de input: JSON via stdin** (NO variables de entorno).

| Script | Evento | Matcher | Comportamiento |
|--------|--------|---------|----------------|
| `block-dangerous.sh` | PreToolUse | *(any)* | Bloquea: `rm -rf /~`, `git push --force`, `git reset --hard`, `git clean -f`, `rm -rf` sobre git worktree activo |
| `config-protection.sh` | PreToolUse | Edit\|Write\|MultiEdit | Bloquea editar: `pyproject.toml`, `.eslintrc*`, `.prettierrc*`, `ruff.toml`, `.pre-commit-config.yaml` |
| `protect-secrets.sh` | PreToolUse | Edit\|Write\|MultiEdit | Bloquea escribir secrets en archivos trackeados por git |
| `adr-gate.sh` | PreToolUse | Edit\|Write | Bloquea edits a `vendor/` y `.agents/` sin ADR staged |
| `worktree-isolation-gate.sh` | PreToolUse | Workflow\|Agent | Advierte si `parallel(agent())` no usa `isolation: 'worktree'`; redirige Agent→Workflow para paralelismo (solo avisa) |
| `quality-enforce.sh` | PreToolUse | Edit\|Write | Bloquea edits si hay errores pendientes en `.claude/quality-state/` |
| `scope-gate.sh` | PreToolUse | Edit\|Write | Bloquea edits fuera del scope en `.claude/task-scope.json` |
| `ts-quality-gate.sh` | PostToolUse | Edit\|Write | Corre `tsc` tras edits de `*.ts/*.tsx`, guarda estado para ts-enforce |
| `migration-gate.sh` | PostToolUse | Edit\|Write\|MultiEdit | Avisa cuando se edita un modelo SQLAlchemy sin correr `alembic revision` |
| `verify-implementation.sh` | PostToolUse | Edit\|Write\|MultiEdit | Detecta funciones stub (`pass`/`TODO`/`NotImplemented`) en `.py` editados |
| `edit-drift-detector.sh` | PostToolUse | Edit\|Write\|Bash | Rastrea edits vs test runs; advierte a 8/15/25 edits sin verificación |
| `quality-gate.sh` | PostToolUse | Edit\|Write | Corre `ruff check` en `.py` modificados (solo avisa) |

También activo vía `settings.json`: `npx block-no-verify@1.1.2` (PreToolUse/Bash) — bloquea `git --no-verify`.

### Patrón de input para futuros hooks
```bash
INPUT=$(cat)                                                          # leer JSON de stdin
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')       # Edit/Write
CMD=$(echo "$INPUT"  | jq -r '.tool_input.command   // empty')       # Bash
[ -z "$FILE" ] && exit 0                                             # guard
# exit 0 = permitir, exit 2 = bloquear (razón en stderr)
```

### Test manual
```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"/path/pyproject.toml","old_string":"x","new_string":"y"}}' \
  | .claude/hooks/config-protection.sh; echo "exit: $?"
# Esperado: exit 2 + mensaje en stderr
```

## Utility Scripts

| Script | Function |
|--------|----------|
| `check-mcp-status.sh` | Check health of all MCP services |
| `cloudsql-mcp.sh` | Wrapper for CloudSQL MCP (switches WPC_ENV) |
| `_load-skill-doc.sh` | Helper to load markdown docs for dynamic skills |
| `task-verify.sh` | Verifica completitud de tarea vs `.claude/task-scope.json`; exit 0 = completa |
| `install-hooks.sh` | Distribuye hooks/scripts con `# distributable: true` a otro proyecto. **Correr tras actualizar hooks en ai-tooling.** |

## Agent Skills

Agent routing and specialized personas are defined in `AGENTS.md`.
Read `@AGENTS.md` for complete routing directives, deduplication mandates, and learning protocols.

Use `.agents/skills/` subagents for deep analysis tasks to keep the main context lean.

---

## Task Mode Protocol (Universal)

Aplica en CUALQUIER proyecto. Copia este bloque a `CLAUDE.md` de cada proyecto.
Las partes específicas del proyecto van en `completion_checklist` del `task-scope.json`, no aquí.

### Iniciar una tarea con scope declarado:
1. Antes de empezar, escribe `.claude/task-scope.json`:
   ```json
   {
     "task_id": "<descripción-fecha>",
     "mode": "<analysis|build|validate|synthesize|full>[:<ts|py|go|rs>]",
     "allowed_patterns": [],
     "completion_checklist": ["<comando>  # descripción", ...]
   }
   ```
2. El hook `scope-gate.sh` aplicará restricciones de write según el modo automáticamente
3. Si no hay `task-scope.json`: comportamiento default (mode=full, sin restricciones extra)

### Scope Discipline — la diferencia entre modos:
- **analysis**: SOLO leer, contar, comparar, reportar. Write solo en `ai-notes/findings/` y `.claude/plans/`.
  **"Analizar X" ≠ "Documentar X".** No crear archivos fuera de esas rutas.
- **build**: editar código en `allowed_patterns[]`. Correr tests al terminar.
- **synthesize**: crear/editar documentación en `ai-notes/`, `docs/`, `*.md`. No editar código.
- **validate**: solo leer + correr comandos. No escribir nada.

### Verificación numérica (OBLIGATORIO antes de reportar cualquier conteo):
1. NUNCA usar `~X` cuando se puede contar exactamente:
   ```bash
   find <dir> -name "<pattern>" | grep -v node_modules | wc -l
   ```
2. SIEMPRE explorar subdirectorios antes de reportar estructura:
   ```bash
   find <dir> -mindepth 1 -maxdepth 2 -type d | sort
   ```
   Si encuentras subdirs inesperados: explóralos PRIMERO, reporta DESPUÉS.
3. Al terminar un análisis: listar explícitamente qué NO fue revisado y por qué.

### Task Completion Gate:
- SIEMPRE correr `./scripts/task-verify.sh` antes de reportar la tarea como completa
- Si exit 1: completar los ítems pendientes listados antes de reportar
- Si exit 0: task-verify.sh indicará si hay tests a correr según modo:lenguaje

### Instalación en un nuevo proyecto:
1. Copiar este bloque al `CLAUDE.md` del proyecto
2. Copiar `scripts/task-verify.sh` de `ai-tooling/scripts/` al proyecto
3. Copiar `.claude/hooks/scope-gate.sh` de `ai-tooling/.claude/hooks/` al proyecto
