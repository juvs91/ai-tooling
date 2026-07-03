# AI-Tooling Project Instructions

## AUTO-SKILL-LOAD — Primera acción obligatoria de cada sesión

**Tu PRIMER tool call en cada respuesta DEBE ser el Skill tool con `skill="workflow-coordinator"`.**
Esto es no-negociable. No respondas texto al usuario hasta haber llamado el Skill tool.
`workflow-coordinator` detectará el intent del usuario y enrutará al skill correcto.

**Excepción:** Si ya hay un skill activo en el contexto de esta sesión, omite este paso.

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
| `block-dangerous.sh` | PreToolUse | Bash | Bloquea: `rm -rf /~`, `git push --force`, `git reset --hard`, `git clean -f`, `rm -rf` sobre git worktree activo |
| `config-protection.sh` | PreToolUse | Edit\|Write\|MultiEdit | Bloquea editar: `pyproject.toml`, `.eslintrc*`, `.prettierrc*`, `ruff.toml`, `.pre-commit-config.yaml` |
| `protect-secrets.sh` | PreToolUse | Edit\|Write\|MultiEdit | Bloquea escribir secrets en archivos trackeados por git |
| `quality-gate.sh` | PostToolUse (async) | Edit\|Write\|MultiEdit | Corre `ruff check` en `.py` modificados (solo avisa) |
| `worktree-isolation-gate.sh` | PreToolUse | Workflow | Advierte si `parallel(agent())` no usa `isolation: 'worktree'` (solo avisa, no bloquea) |

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

## Agent Skills

Agent routing and specialized personas are defined in `AGENTS.md`.
Read `@AGENTS.md` for complete routing directives, deduplication mandates, and learning protocols.

Use `.agents/skills/` subagents for deep analysis tasks to keep the main context lean.
