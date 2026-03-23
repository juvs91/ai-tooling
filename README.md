# ai-tooling

Proxy + workflow estandarizado para usar Claude Code con **cualquier proveedor LLM** (Z.AI, Groq, Gemini, DeepSeek, OpenAI, OpenRouter, Ollama local) con guardrails fuertes y separacion clara entre trabajo local ($0) y cloud (on-demand).

```
Claude Code CLI
    |
    | ANTHROPIC_BASE_URL=http://127.0.0.1:8083
    v
FastAPI Proxy (vendor/claude-code-proxy)
    |
    |-- Intent Classification (LLM o regex)
    |-- Policy & Routing (BIG_MODEL / SMALL_MODEL)
    |-- Schema Conversion (Anthropic -> OpenAI/Gemini)
    v
Proveedor LLM (Z.AI, Groq, Gemini, DeepSeek, OpenAI, Ollama...)
```

**Dos modos:**

| Modo | Puerto | Costo | Tools | Uso |
|------|--------|-------|-------|-----|
| **Local** | 8082 | $0 | OFF | Scan, plan, validacion textual |
| **Cloud** | 8083 | On-demand | ON | Ejecucion/automatizacion |

---

## Quick Start (5 min)

### 1. Clonar e instalar

```bash
git clone <repo-url> && cd ai-tooling
source ~/.zshrc     # asegura que ~/.local/bin este en el PATH
```

### 2. Configurar proveedor

Copia el .env del proveedor que quieras y pon tu API key:

```bash
# Ejemplo: Z.AI
cp profile-envs/cloud.zai.env profile-envs/cloud.zai.env.bak
# Editar profile-envs/cloud.zai.env con tu OPENAI_API_KEY
```

### 3. Levantar el proxy

```bash
cc-proxy-up cloud-provider-ymls/docker-compose.zai.override.yml
```

### 4. Verificar que funciona

```bash
cc-health --port 8083
```

### 5. Usar Claude Code

```bash
cc-chat "hola, que modelo eres?"          # chat local (tools OFF)
cc-chat --cloud "hola, que modelo eres?"  # chat cloud (tools OFF)
cc-agent                                   # agente local (tools ON)
cc-agent-cloud                             # agente cloud (tools ON, requiere plan REVIEWED)
```

---

## Estructura del repo

```
ai-tooling/
|-- vendor/claude-code-proxy/    # Proxy Anthropic->OpenAI (fork propio, hot-reload)
|   |-- server.py                # FastAPI principal (endpoints /v1/messages, /health, /count_tokens)
|   |-- llm/
|   |   |-- schemas.py           # Pydantic models (content blocks de Anthropic)
|   |   |-- converters.py        # Anthropic <-> LiteLLM <-> Provider
|   |   +-- streaming.py         # SSE streaming handler
|   |-- proxy/proxy.py           # Policy, routing, tool allowlist
|   +-- router/
|       |-- model_mapper.py      # Claude alias -> modelo real del proveedor
|       +-- llm_router.py        # Intent classifier (LLM o regex)
|
|-- .agents/skills/              # Sistema de skills para agentes (26 SKILL.md files)
|   |-- skills.md                # Indice global de skills
|   |-- core/                    # learning-protocol, tool-writer
|   |-- infrastructure/          # database-expert, gitops-expert
|   |-- integrations/            # claude-api, documentation-lookup, deep-research
|   |-- security/                # security-expert, security-review
|   |-- software/
|   |   |-- api/                 # api-design, backend-patterns, mcp-server-patterns
|   |   |-- architecture/        # architect, adr-writer, decision-logger
|   |   |-- discovery/           # software-archeologist, retro-engineer, unknown-domain-protocol
|   |   |-- language/go/         # golang-patterns, golang-testing
|   |   +-- quality/             # bdd-writer, code-reviewer, tdd-workflow, coding-standards,
|   |                            #   verification-loop, eval-harness
|   +-- semantic-code-search.md  # Patron de busqueda semantica
|
|-- .claude/
|   |-- hooks/
|   |   |-- config-protection.sh # Bloquea ediciones a pyproject.toml, ruff.toml, etc.
|   |   +-- quality-gate.sh      # Ruff check async post-edit en .py files
|   +-- settings.json            # Claude Code settings (hooks, permissions)
|
|-- .mcp.json                    # Configuracion MCP activa (9 servidores)
|-- .mcp.json.template           # Template para nuevos devs (sin credenciales reales)
|
|-- scripts/                     # CLI tools (symlinked a ~/.local/bin/)
|-- profile-envs/                # Un .env por proveedor (API keys, modelos)
|-- cloud-provider-ymls/         # Docker compose overrides por proveedor
|-- profiles/                    # Perfiles JSON para Claude Code CLI
|-- templates/                   # Plantillas para AI_CONTEXT, AI_PLAN, AI_LEARNING, GUARDRAILS
|   +-- ralph/                   #   Ralph: autonomous agent framework boilerplate
|-- ai-notes/                    # Artefactos de sesion (scans, contextos, planes, learnings)
|-- context/
|   +-- run_context.md           # Hechos confirmados del proyecto (runtime, puertos, MCPs)
|-- docs/
|   |-- adr/                     # Architecture Decision Records (ADR-NNNN-*.md)
|   +-- findings/                # Descubrimientos (ledger F-XXX)
|-- .env                         # Variables globales (policy defaults + credenciales MCP)
|-- docker-compose.yml           # Servicios Docker (proxy_local + proxy_cloud)
|-- AGENTS.md                    # Routing de skills para agentes (26 directivas)
+-- CLAUDE.md                    # Instrucciones mandatorias para agentes Claude
```

---

## Proveedores soportados

### Proveedores configurados

| Proveedor | Archivo .env | Override YAML | Modelos | Notas |
|-----------|-------------|---------------|---------|-------|
| **Z.AI** | `profile-envs/cloud.zai.env` | `cloud-provider-ymls/docker-compose.zai.override.yml` | glm-4.7, glm-4.7-flash | OpenAI-compatible. Endpoint: `api.z.ai/api/paas/v4` |
| **Groq** | `profile-envs/cloud.groq.env` | `cloud-provider-ymls/docker-compose.groq.override.yml` | llama-3.1-8b-instant | Ultra rapido. OpenAI-compatible |
| **Gemini** | `profile-envs/cloud.gemini.env` | `cloud-provider-ymls/docker-compose.gemini.override.yml` | gemini-2.5-flash | Usa `PREFERRED_PROVIDER=google` (nativo, no OpenAI) |
| **DeepSeek** | `profile-envs/cloud.deepseek.env` | `cloud-provider-ymls/docker-compose.deepseek.override.yml` | deepseek-reasoner, deepseek-chat | OpenAI-compatible. Bueno como classifier |
| **OpenAI** | `profile-envs/cloud.openai.env` | (base docker-compose.yml) | gpt-4.1, gpt-4.1-mini | Default si no se especifica override |
| **OpenRouter** | `profile-envs/cloud.openrouter.env` | (usar con PROFILE_ENV) | Cualquier modelo de OpenRouter | Aggregador. Verificar que el modelo soporte tools |
| **Ollama (local)** | `profile-envs/local.env` | (base docker-compose.yml) | qwen2.5-coder:7b variants | $0, offline. Usa `host.docker.internal:11434` |

### Levantar un proveedor

**Opcion A: Con override YAML (recomendado)**

```bash
# Z.AI
cc-proxy-up cloud-provider-ymls/docker-compose.zai.override.yml

# Groq
cc-proxy-up cloud-provider-ymls/docker-compose.groq.override.yml

# Gemini
cc-proxy-up cloud-provider-ymls/docker-compose.gemini.override.yml

# DeepSeek
cc-proxy-up cloud-provider-ymls/docker-compose.deepseek.override.yml
```

**Opcion B: Con PROFILE_ENV (para proveedores sin override YAML)**

```bash
# OpenRouter
PROFILE_ENV=profile-envs/cloud.openrouter.env cc-proxy-up cloud-provider-ymls/docker-compose.zai.override.yml
```

**Opcion C: Proxy local con Ollama**

```bash
# Primero levantar Ollama y crear modelos tagueados
cc-ollama-up make-all --base qwen2.5-coder:7b --prefix cc-local

# Luego levantar el proxy local (puerto 8082)
docker compose up -d proxy_local
```

### Agregar un proveedor nuevo

1. **Crear el archivo .env** en `profile-envs/`:

```bash
# profile-envs/cloud.nuevo-proveedor.env
PREFERRED_PROVIDER=openai              # openai | google | anthropic
OPENAI_BASE_URL=https://api.nuevo.com/v1
OPENAI_API_KEY=tu-api-key-aqui

BIG_MODEL=nombre-modelo-grande
SMALL_MODEL=nombre-modelo-chico
BUILDING_MODEL=nombre-modelo-building  # opcional, default = BIG_MODEL

# Intent classifier (opcional)
#CLASSIFIER_MODEL=openai/deepseek-chat
#CLASSIFIER_API_KEY=tu-key
#CLASSIFIER_BASE_URL=https://api.deepseek.com/v1

TOOL_ALLOWLIST=*
POLICY_NOTE_IN_SYSTEM=1
```

2. **Crear el override YAML** en `cloud-provider-ymls/`:

```yaml
# cloud-provider-ymls/docker-compose.nuevo-proveedor.override.yml
services:
  proxy_cloud:
    env_file:
      - ./.env
      - profile-envs/cloud.nuevo-proveedor.env
```

3. **Levantar y verificar:**

```bash
cc-proxy-up cloud-provider-ymls/docker-compose.nuevo-proveedor.override.yml
cc-health --port 8083
```

### Variables de entorno del proxy

**Modelos (en cada .env de proveedor):**

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `PREFERRED_PROVIDER` | `openai` | Tipo de API: `openai`, `google`, `anthropic` |
| `OPENAI_BASE_URL` | - | Endpoint del proveedor (solo para openai-compatible) |
| `OPENAI_API_KEY` | - | API key del proveedor |
| `GEMINI_API_KEY` | - | API key de Google (solo para `google` provider) |
| `BIG_MODEL` | `SMALL_MODEL` | Modelo para tareas complejas (PLANNING, BUILDING) |
| `SMALL_MODEL` | `cc-local:chat` | Modelo para tareas simples (CHAT) |
| `BUILDING_MODEL` | `BIG_MODEL` | Modelo especifico para building (opcional) |

**Policy (en `.env` global):**

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `TOOL_ALLOWLIST` | `""` | `*` = todas, o lista separada por comas. Vacio = todas |
| `POLICY_NOTE_IN_SYSTEM` | `1` | Inyectar nota de policy en system prompt |
| `MAX_INPUT_TOKENS` | `30000` | Limite de tokens de entrada. `0` = sin limite |
| `HARD_BLOCK_OVERSIZE` | `0` | `1` = rechazar con HTTP 413 si excede MAX_INPUT_TOKENS |

**Intent Classifier (opcional, en .env del proveedor):**

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `CLASSIFIER_MODEL` | `""` | Modelo LLM para clasificar intents. Vacio = regex fallback |
| `CLASSIFIER_API_KEY` | `""` | API key del clasificador |
| `CLASSIFIER_BASE_URL` | `""` | Endpoint del clasificador |
| `CLASSIFIER_TIMEOUT` | `3.0` | Timeout en segundos |

---

## Scripts (CLI)

Los scripts se usan directamente desde `scripts/` o instalados como symlinks en `~/.local/bin/`.

### cc-chat — Chat simple (tools OFF)

```bash
cc-chat "que es un monad?"                # una sola pregunta, local
cc-chat --cloud "que es un monad?"        # una sola pregunta, cloud directo
cc-chat                                    # modo REPL interactivo
cat archivo.py | cc-chat "analiza esto"   # leer de stdin
```

- **Tools:** Siempre desactivados
- **Puerto local:** 8082 (proxy)
- **Puerto cloud:** API directa de Anthropic (requiere `ANTHROPIC_API_KEY`)
- **Env vars:** `CC_MODEL` (local), `CC_CLOUD_MODEL` (cloud), `CC_SYSTEM_PROMPT`

### cc-scan — Analisis de archivo (tools OFF)

```bash
cc-scan path/to/file.py
cc-scan --cloud path/to/file.py
```

Genera `ai-notes/<nombre>.analysis.md` con:
1. Resumen (que hace)
2. Inputs/Dependencias (DB, env vars, endpoints)
3. Outputs/Efectos (escrituras, side effects)
4. Riesgos (bugs, seguridad, performance)
5. TODOs sugeridos (max 7)

### cc-plan — Generador de plan (tools OFF)

```bash
cc-plan
cc-plan --cloud
```

Lee `ai-notes/AI_CONTEXT.md` + `templates/AI_PLAN.template.md` y genera `ai-notes/AI_PLAN.md` con `STATUS: DRAFT`. El humano debe revisar y cambiar a `STATUS: REVIEWED` + agregar `Reviewed-by: <nombre>`.

### cc-agent — Agente local (tools ON)

```bash
cc-agent
cc-agent "refactoriza la funcion X"
```

- **Puerto:** 8082 (proxy local, Ollama)
- **Tools:** Activados
- **Sin guardrails de plan:** Pensado para desarrollo local

### cc-agent-cloud — Agente cloud (tools ON, con guardrails)

```bash
cc-agent-cloud
```

- **Puerto:** 8083 (proxy cloud)
- **Tools:** Activados
- **Guardrails enforced:**
  - Requiere `ai-notes/AI_PLAN.md`
  - Plan debe contener `STATUS: REVIEWED`
  - Plan debe contener `Reviewed-by: <nombre>`
- **Exit codes:** 2 (sin plan), 3 (no reviewed), 4 (sin reviewer)

### cc-proxy-up — Levanta el proxy Docker

```bash
cc-proxy-up cloud-provider-ymls/docker-compose.zai.override.yml

# Con variables opcionales:
PROFILE_ENV=profile-envs/cloud.groq.env cc-proxy-up docker-compose.cloud.override.yml
BIND_PORT=9090 cc-proxy-up cloud-provider-ymls/docker-compose.zai.override.yml
```

- **Env vars:** `PROFILE_ENV`, `HOST_SERVER`, `CONTAINER_SERVER`, `ENABLE_RELOAD`, `BIND_PORT`
- Genera un override temporal con bind-mount de `vendor/claude-code-proxy/` para hot-reload
- Soporta multiples proxies simultaneos con diferentes `BIND_PORT`

### cc-health — Health check del proxy

```bash
cc-health                 # texto legible
cc-health --json          # JSON crudo
cc-health --port 9090     # puerto custom
```

### cc-switch — Cambio rapido de modelo (Claude Code directo)

```bash
cc-switch opus    # -> Opus 4.6 (Anthropic directo, tareas pesadas)
cc-switch sonnet  # -> Sonnet 4.5 (Anthropic directo, dia a dia)
cc-switch zai     # -> Z.AI GLM-4.7 via proxy (barato)
```

Copia el settings correspondiente a `~/.claude/settings.local.json` e intenta recargar VS Code.

### cc-profile — Cambio de perfil completo

```bash
cc-profile local   # -> Ollama via proxy local
cc-profile cloud   # -> Z.AI via proxy cloud
cc-profile apply profiles/custom.json  # perfil personalizado
```

Hace backup automatico de `~/.claude/settings.json` antes de sobrescribir.

### cc-checklist-validate — Validacion de checklist

```bash
cc-checklist-validate               # texto con emojis
cc-checklist-validate --json        # formato JSON
cc-checklist-validate --item proxy  # solo items que matcheen "proxy"
```

### cc-ollama-up — Crear modelos Ollama tagueados

```bash
# Crear 3 variantes (chat, planning, building) con contextos diferentes
cc-ollama-up make-all --base qwen2.5-coder:7b --prefix cc-local

# Crear uno solo
cc-ollama-up make --base qwen2.5-coder:7b --tag cc-local:chat --profile chat

# Ver env vars sugeridas
cc-ollama-up print-env --prefix cc-local
```

---

## MCP Servers

Los MCP servers se configuran en `.mcp.json` (activo) y `.mcp.json.template` (referencia para nuevos devs). Claude Code los carga automaticamente al iniciar sesion en el proyecto.

### Servidores configurados

| Server | Paquete | Auth | Proposito |
|--------|---------|------|-----------|
| `alloydb` | `postgres-mcp` (node) | `ALLOYDB_PASSWORD` | Queries a AlloyDB — pricing, cascade, debug |
| `atlassian` | `uvx mcp-atlassian` | `ATLASSIAN_CONFLUENCE_TOKEN`, `ATLASSIAN_JIRA_API_TOKEN` | Jira issues, Confluence search |
| `squit` | `npx mcp-remote` | `SQUIT_API_KEY` (header) | Busqueda de stored procedures legacy |
| `cloudsql` | `bash scripts/cloudsql-mcp.sh` | `WPC_ENV` + per-env vars | CloudSQL (requiere SSH tunnel) |
| `context7` | `npx @upstash/context7-mcp` | ninguna | Documentacion live de cualquier libreria/framework |
| `serper` | `npx serper-search-scrape-mcp-server` | `SERPER_API_KEY` | Busqueda web Google via Serper API |
| `playwright` | `npx @executeautomation/playwright-mcp-server` | ninguna | Automatizacion de browser (chromium) |
| `sequential-thinking` | `npx @modelcontextprotocol/server-sequential-thinking` | ninguna | Razonamiento encadenado para tareas complejas |
| `memory` | `npx @modelcontextprotocol/server-memory` | ninguna | Knowledge graph persistente entre sesiones |

### Setup de credenciales

Todas las credenciales se almacenan como variables de entorno en `.env` (nunca en `.mcp.json`):

```bash
# .env — credenciales MCP
ALLOYDB_PASSWORD=...
ATLASSIAN_CONFLUENCE_TOKEN=...
ATLASSIAN_JIRA_API_TOKEN=...
SERPER_API_KEY=...
```

Claude Code expande automaticamente `$VAR_NAME` en los bloques `env` de `.mcp.json`.

### Setup para nuevos devs

```bash
# 1. Copiar el template
cp .mcp.json.template .mcp.json

# 2. Llenar las credenciales en .env (ver sección MCP Credentials en CLAUDE.md)

# 3. Instalar dependencias de playwright (solo una vez)
mkdir -p /tmp/pw157 && cd /tmp/pw157 && \
  echo '{"name":"tmp","version":"1.0.0"}' > package.json && \
  npm install playwright@1.57.0 --save-quiet && \
  ./node_modules/.bin/playwright install chromium
```

> `alloydb` y `cloudsql` requieren SSH tunnel activo. Los 7 servidores restantes funcionan sin tunnel.

---

## Guardrails

### Que son

Los guardrails son reglas de seguridad que previenen que los agentes AI ejecuten acciones sin supervision humana. Estan definidos en `templates/GUARDRAILS.template.md` y se aplican en dos niveles:

### Nivel 1: Enforcement en scripts (implementado)

El script `cc-agent-cloud` valida **antes de ejecutar** que:

```
1. Existe ai-notes/AI_PLAN.md                    -> si no: exit 2
2. Contiene "STATUS: REVIEWED" o "REVIEWED: YES"  -> si no: exit 3
3. Contiene "Reviewed-by: <nombre>"               -> si no: exit 4
```

Esto impide que un agente con tools ON ejecute sin un plan revisado por un humano.

### Nivel 2: Policy en el proxy (implementado)

El proxy aplica politicas en cada request:

- **TOOL_ALLOWLIST**: Restringe que tools puede usar el LLM (ej: solo `Read`, `Grep`)
- **MAX_INPUT_TOKENS**: Limite de tokens de entrada
- **HARD_BLOCK_OVERSIZE**: Rechazar requests demasiado grandes (HTTP 413)
- **POLICY_NOTE_IN_SYSTEM**: Inyecta nota de guardrails en el system prompt

### Nivel 3: Inyeccion en CLAUDE.md (implementado)

El archivo `CLAUDE.md` en la raiz del proyecto contiene instrucciones mandatorias que Claude Code carga automaticamente:

```markdown
# Mandatorio
- ALWAYS read ai-notes/AI_LEARNING.md al inicio
- ALWAYS read templates/GUARDRAILS.template.md
- Do NOT execute tools si AI_PLAN.md no tiene STATUS: REVIEWED
- Do NOT inventar paths, comandos, o outputs
- ALL outputs van a ai-notes/
```

### Nivel 4: Hooks de Claude Code (implementado)

Configurados en `.claude/settings.json`. Se ejecutan automaticamente sin intervencion del agente:

| Hook | Evento | Archivo | Comportamiento |
|------|--------|---------|----------------|
| `config-protection` | PreToolUse (Edit/Write) | `.claude/hooks/config-protection.sh` | Exit 2 si intenta editar `pyproject.toml`, `ruff.toml`, `.pre-commit-config.yaml`, `.eslintrc*`, `.prettierrc*` |
| `quality-gate` | PostToolUse (Edit/Write) | `.claude/hooks/quality-gate.sh` | Corre `ruff check` async en cada `.py` editado — resultado visible en terminal |

```bash
# Ver hooks activos
cat .claude/settings.json | jq .hooks
```

**Posibles mejoras futuras:**
- Proxy-side tool gating basado en el plan
- Session-aware guardrails (leer AI_PLAN.md al inicio)
- Sistema de permisos por archivo (solo modificar lo listado en AI_CONTEXT.md)

---

## Templates y ai-notes

### Templates (en `templates/`)

Las plantillas son **inmutables** — nunca se modifican directamente. Se copian a `ai-notes/` para crear instancias de trabajo.

| Template | Proposito | Cuando se usa |
|----------|-----------|---------------|
| `AI_CONTEXT.template.md` | Define scope, inputs, outputs permitidos, comandos | El humano lo llena al inicio de cada tarea |
| `AI_PLAN.template.md` | Estructura del plan con pasos atomicos, riesgos, validacion | `cc-plan` lo usa como guia para generar el plan |
| `AI_LEARNING.template.md` | Estructura para capturar patrones, errores, decisiones | Se scaffoldea en `ai-notes/` via scaffolding manual |
| `GUARDRAILS.template.md` | Reglas core de seguridad y feedback loop | Agentes lo leen al inicio de cada sesion |

### ai-notes/ (artefactos de sesion)

**Todo** lo que generan los agentes y humanos va aqui. Nunca a `.claude/` ni a la memoria privada del agente.

| Archivo | Quien lo crea | Quien lo consume |
|---------|--------------|-----------------|
| `AI_CONTEXT.md` | Humano (desde template) | `cc-plan`, `cc-agent-cloud` |
| `AI_PLAN.md` | `cc-plan` (STATUS: DRAFT) | Humano revisa -> REVIEWED. `cc-agent-cloud` valida |
| `AI_LEARNING.md` | Agentes + humanos al final de sesion | Todos los agentes al inicio de sesion |
| `*.analysis.md` | `cc-scan` | Humano, `cc-plan` (referencia en AI_CONTEXT) |
| `GUARDRAILS.md` | Copia manual de template | Agentes al inicio de sesion |

### Como pasar esto a Claude Code

Para que Claude Code respete los guardrails y templates, hay **cuatro mecanismos**:

**1. CLAUDE.md (automatico)**

Claude Code carga automaticamente `CLAUDE.md` de la raiz del proyecto. Ahi estan las instrucciones mandatorias: leer AI_LEARNING, leer GUARDRAILS, no ejecutar sin REVIEWED.

**2. System prompt del proxy (`POLICY_NOTE_IN_SYSTEM=1`)**

Cuando esta activo, el proxy inyecta una nota de policy en el system prompt de cada request. Esto refuerza las reglas a nivel de modelo.

**3. Scripts como wrappers**

Los scripts (`cc-agent-cloud`, `cc-plan`, `cc-scan`) actuan como wrappers que:
- Validan pre-condiciones (plan existe, esta reviewed)
- Fuerzan tools OFF donde corresponde
- Ponen el system prompt correcto
- Redirigen output a `ai-notes/`

**4. Hooks de Claude Code (`.claude/hooks/`)**

Hooks de ciclo de vida configurados en `.claude/settings.json`. Se ejecutan automaticamente sin intervencion del agente — ver [Nivel 4: Hooks](#nivel-4-hooks-de-claude-code-implementado).

---

## Agent Skills

El sistema de skills es un mecanismo de routing documentation-driven. No hay plugin registry ni codigo — Claude lee un archivo SKILL.md y adopta la persona/protocolo descrito.

### Como funciona la cadena de carga

```
CLAUDE.md  (auto-cargado por Claude Code)
    |
    |-- "Read @AGENTS.md"
    v
AGENTS.md  (26 directivas de routing)
    |
    |-- "Si X, MUST read .agents/skills/categoria/skill/SKILL.md"
    v
SKILL.md   (frontmatter: name + description + body con persona y protocolo)
```

Claude Code carga `CLAUDE.md` automaticamente. `CLAUDE.md` referencia `AGENTS.md`. `AGENTS.md` mapea intenciones → archivos `SKILL.md`. Cuando Claude lee un `SKILL.md`, adopta la persona y sigue el protocolo definido en el body.

### Skills instalados (26)

| Categoria | Skill | Trigger en AGENTS.md |
|-----------|-------|----------------------|
| **Core** | `learning-protocol` | Nuevo concepto aprendido, patron reutilizable |
| **Core** | `tool-writer` | Necesitas crear un nuevo tool o script |
| **Architecture** | `architect` | Diseñar sistema, revisar componentes |
| **Architecture** | `adr-writer` | Tomar o confirmar una decision arquitectural |
| **Architecture** | `decision-logger` | Extraer decision embebida en codigo |
| **Discovery** | `software-archeologist` | Reverse engineering, executions graph |
| **Discovery** | `retro-engineer` | Backtrack comportamiento → entry point |
| **Discovery** | `unknown-domain-protocol` | Dominio completamente desconocido |
| **Quality** | `bdd-writer` | Specs Gherkin, feature files BDD |
| **Quality** | `code-reviewer` | Review de PR, auditoria de diff |
| **Quality** | `tdd-workflow` | Escribir features o bugs (test-first) |
| **Quality** | `coding-standards` | Linting Python/JS/TS, gates de formato |
| **Quality** | `verification-loop` | Verificar correctitud post-implementacion |
| **Quality** | `eval-harness` | Harness de evals, scoring de outputs LLM |
| **Infrastructure** | `database-expert` | AlloyDB, SQL, MCP data tools, schema |
| **Infrastructure** | `gitops-expert` | Docker, CI/CD, GitHub Actions, infra |
| **Integrations** | `claude-api` | Anthropic SDK, streaming, tool use, caching |
| **Integrations** | `documentation-lookup` | Docs live via Context7 MCP |
| **Integrations** | `deep-research` | Investigacion multi-fuente (serper + context7) |
| **Security** | `security-expert` | Threat modeling, hardening, vulnerabilidades |
| **Security** | `security-review` | Code review de seguridad en diffs y PRs |
| **API** | `api-design` | Diseño REST, OpenAPI specs, versionado |
| **API** | `backend-patterns` | Error handling, paginacion, rate limiting |
| **API** | `mcp-server-patterns` | Construir MCP servers (stdio vs HTTP) |
| **Go** | `golang-patterns` | Go idiomatico: interfaces, errors, concurrency |
| **Go** | `golang-testing` | Tests Go, table-driven tests, benchmarks |

Indice completo: `.agents/skills/skills.md`

### ADR-First Mandate

**HARD STOP** — antes de cambiar cualquier arquitectura del proxy o skill core, debes escribir un ADR:

```
1. Descubrir la decision de diseño necesaria
2. Crear docs/adr/ADR-NNNN-<titulo>.md
3. Commitear ADR + codigo juntos
```

### Hooks de ciclo de vida

Configurados en `.claude/settings.json`, ejecutados automaticamente por Claude Code:

| Hook | Evento | Que hace |
|------|--------|----------|
| `config-protection.sh` | PreToolUse (Edit/Write) | Bloquea ediciones a `pyproject.toml`, `ruff.toml`, `.pre-commit-config.yaml` |
| `quality-gate.sh` | PostToolUse (Edit/Write) | Corre `ruff check` async en `.py` files editados |

---

## Ralph — Autonomous Agent Framework

Ralph es un framework para ejecutar Claude Code en un loop automatizado con rate limiting, ejecucion por fases, auto-tracking de progreso, circuit breaker, y session persistence.

Hay dos ubicaciones relevantes:

| Ubicacion | Proposito |
|-----------|-----------|
| `vendor/ralph/` | **Implementacion real** — scripts ejecutables, lib/, setup |
| `templates/ralph/` | **Boilerplate** — templates para nuevos proyectos |

### Implementacion — `vendor/ralph/`

```
vendor/ralph/
├── ralph_loop.sh          # Loop principal con rate limiting y session management
├── ralph_enable.sh        # Setup interactivo de Ralph en un proyecto
├── ralph_enable_ci.sh     # Setup no-interactivo para CI
├── ralph_import.sh        # Importa Ralph a un proyecto existente
├── ralph_monitor.sh       # Monitor de estado en tiempo real
├── setup.sh               # Instalacion de symlinks
├── migrate_to_ralph_folder.sh  # Migracion desde estructura legacy
├── claude-stdio           # Wrapper Claude Code stdio (streaming live output)
├── lib/
│   ├── circuit_breaker.sh # Detiene el loop si no hay progreso (3 fallos = STOP)
│   ├── date_utils.sh      # Rate limiting — ventana de 1 hora
│   ├── timeout_utils.sh   # Timeout por llamada a Claude
│   ├── response_analyzer.sh  # Parsea ---RALPH_STATUS--- blocks
│   ├── task_sources.sh    # Carga tareas desde fix_plan.md o PROMPT.md
│   ├── wizard_utils.sh    # CLI interactivo para ralph_enable.sh
│   └── enable_core.sh     # Core logic compartido entre enable/enable_ci
└── templates/             # Boilerplate para nuevos proyectos (AGENT.md, PROMPT.md, fix_plan.md)
```

### Quick Start

```bash
# 1. Instalar ralph (crea symlinks en ~/.local/bin/)
cd vendor/ralph && ./setup.sh

# 2. Inicializar Ralph en un proyecto
ralph-enable --target /path/to/your/project       # interactivo
ralph-enable-ci --target /path/to/your/project \  # non-interactivo (CI)
  --name my-app --type python --workdir src/

# 3. Configurar .ralph/PROMPT.md y .ralph/fix_plan.md

# 4. Ejecutar el loop
ralph-loop                                         # desde el directorio del proyecto
```

### `ralph-init` — desde scripts/

Si usas `ralph-init` (en `scripts/`), scaffoldea un proyecto desde `templates/ralph/`:

```bash
ralph-init --target /path/to/your/project
ralph-init --target . --force   # overwrite si ya existe .ralph/
```

**Rellena automaticamente:** `PROJECT_NAME`, `PROJECT_TYPE`, `PROJECT_ROOT`, `WORKING_DIRECTORY`, `PROJECT_DESCRIPTION` en todos los templates.

### `ralph_loop.sh` — caracteristicas clave

| Feature | Detalle |
|---------|---------|
| **Rate limiting** | `MAX_CALLS_PER_HOUR` (default 100). Espera si se alcanza el limite |
| **Session persistence** | `--continue` entre loops para mantener contexto entre llamadas |
| **Circuit breaker** | 3 loops sin progreso → STOP automatico (lib/circuit_breaker.sh) |
| **Timeout por llamada** | `CLAUDE_TIMEOUT_MINUTES` (default 15) via lib/timeout_utils.sh |
| **Live output** | `LIVE_OUTPUT=true` — streaming en tiempo real via SSE parser (`llm/sse.py`) |
| **Status tracking** | `---RALPH_STATUS---` blocks machine-readable en cada respuesta |
| **Tool profiles** | `CLAUDE_ALLOWED_TOOLS` configurable por proyecto |

### 3 Archivos Semanticos

Ralph lee estos tres archivos **antes de cualquier accion**:

| Archivo | Quien lo crea | Proposito |
|---------|--------------|-----------|
| `.ralph/fix_plan.md` | Humano | Checklist de tareas por fase `[ ]` / `[x]` |
| `.ralph/specs/ai_learning.md` | Ralph (se auto-actualiza) | Conocimiento acumulado del dominio |
| `.ralph/specs/schema_reference.md` | Humano (read-only) | Referencia del dominio (esquemas, APIs, reglas) |

### Perfiles de herramientas

| Perfil | Tools | Caso de uso |
|--------|-------|-------------|
| Solo lectura | `Read,Glob,Grep` | Analisis/auditoria |
| Modificacion | `Write,Read,Edit,Glob,Grep` | Default — modificar codigo |
| Acceso completo | `Write,Read,Edit,Glob,Grep,Bash` | Build, test, deploy |

---

## Workflow completo

```
                    LOCAL ($0)                          CLOUD (on-demand)
                    tools OFF                           tools ON
               +------------------+              +-------------------+
               |                  |              |                   |
  [Humano] --> | 1. AI_CONTEXT.md |              |                   |
               |    (manual)      |              |                   |
               +--------+---------+              |                   |
                        |                        |                   |
                        v                        |                   |
               +------------------+              |                   |
               | 2. cc-scan       |              |                   |
               |    -> *.analysis |              |                   |
               +--------+---------+              |                   |
                        |                        |                   |
                        v                        |                   |
               +------------------+              |                   |
               | 3. cc-plan       |              |                   |
               |    -> AI_PLAN.md |              |                   |
               |    STATUS: DRAFT |              |                   |
               +--------+---------+              |                   |
                        |                        |                   |
                        v                        |                   |
               +------------------+              |                   |
  [Humano] --> | 4. REVIEW        |              |                   |
               |    STATUS:       |              |                   |
               |    REVIEWED      |              |                   |
               |    Reviewed-by:  |              |                   |
               |    <nombre>      |              |                   |
               +--------+---------+              |                   |
                        |                        |                   |
                        +----------------------->+                   |
                                                 | 5. cc-agent-cloud |
                                                 |    (valida plan)  |
                                                 |    (ejecuta)      |
                                                 +--------+----------+
                                                          |
                                                          v
                                                 +-------------------+
                                                 | 6. Resultados en  |
                                                 |    ai-notes/      |
                                                 +-------------------+
                                                          |
                                                          v
                                                 +-------------------+
                                  [Agente/Humano] | 7. Actualizar     |
                                                 |    AI_LEARNING.md |
                                                 +-------------------+
```

### Pasos detallados

**1. Crear AI_CONTEXT.md**
```bash
cp templates/AI_CONTEXT.template.md ai-notes/AI_CONTEXT.md
# Editar: definir ticket, objetivo, inputs, outputs, comandos permitidos
```

**2. Scan de archivos relevantes**
```bash
cc-scan src/auth/login.py
cc-scan src/api/routes.py
# Genera: ai-notes/login.py.analysis.md, ai-notes/routes.py.analysis.md
```

**3. Generar plan**
```bash
cc-plan
# Genera: ai-notes/AI_PLAN.md con STATUS: DRAFT
```

**4. Revisar plan (humano)**
```bash
# Abrir ai-notes/AI_PLAN.md
# Cambiar STATUS: DRAFT -> STATUS: REVIEWED
# Agregar Reviewed-by: tu-nombre
```

**5. Ejecutar con agente cloud**
```bash
cc-agent-cloud
# Valida plan REVIEWED -> ejecuta con tools ON via proxy cloud
```

**6-7. Capturar learnings**
```bash
# El agente (o tu) actualiza ai-notes/AI_LEARNING.md con:
# - Que funciono
# - Que fallo
# - Decisiones tomadas
```

---

## Desarrollo del proxy

### Hot-reload

El proxy monta `vendor/claude-code-proxy/` como volumen Docker. Uvicorn corre con `--reload`, asi que cualquier cambio en el codigo se aplica automaticamente sin rebuild.

```bash
# Editar el proxy
vim vendor/claude-code-proxy/server.py

# Los cambios se aplican solos. Verificar logs:
docker logs -f ai-tooling-proxy_cloud-1
```

### Arquitectura del proxy — Transformer Pipeline

El proxy usa un patrón de **pipeline de transformers** (refactor mayor desde la v1 monolítica). Cada transformer tiene una sola responsabilidad y escribe/lee de un `TransformContext` compartido.

```
Request (Anthropic format)
    |
    v
[server.py] POST /v1/messages
    |
    v
━━━ PHASE 1: Request Pipeline (Anthropic format) ━━━━━━━━━━━━━━━━
    |
    |-- IntentClassifierTransformer  → intent: CHAT|PLANNING|BUILDING
    |                                  phase: EXPLORE|PLAN|EXECUTE
    |-- IntentEnforcementTransformer → valida compliance con intent
    |-- GuardrailTransformer         → inyecta policy note en system
    |-- DeferredToolsTransformer     → inyecta <available-deferred-tools> (plan mode)
    |-- TokenCapTransformer          → checa/aplica MAX_INPUT_TOKENS
    |-- ToolAllowlistTransformer     → filtra tools segun TOOL_ALLOWLIST
    |-- AdaptiveContextTransformer   → routing adaptivo basado en historial
    |-- ModelRouterTransformer       → mapea modelo segun intent + config
    |
    v
[convert_anthropic_to_litellm()]  → convierte al formato del proveedor
    |
    v
━━━ PHASE 2: LiteLLM Pipeline ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    |
    |-- CompressionTransformer       → comprime contexto si excede ventana
    |-- ProviderQuirksTransformer    → fixes especificos por proveedor
    |-- CredentialTransformer        → inyecta API key/base_url
    |
    v
         ┌──────────────────────────────────────────┐
         │  4 EXECUTION PATHS (auto-detectados)     │
         │                                          │
         │  A. LiteLLM non-stream                   │
         │  B. LiteLLM stream (SSE)                 │
         │  C. Passthrough non-stream *             │
         │  D. Passthrough stream (SSE) *           │
         │                                          │
         │  * Passthrough: Anthropic-compatible     │
         │    endpoints (Z.AI) sin conversión,      │
         │    enviado directamente via httpx         │
         └──────────────────────────────────────────┘
    |
    v
━━━ RESPONSE Pipeline (AGNOSTIC — todos los paths) ━━━━━━━━━━━━━━
    |
    |-- ReasoningHandlingTransformer    → limpia thinking blocks
    |-- UniversalToolExtractionTransformer → extrae tool calls de XML
    |-- GroundingValidatorTransformer   → valida evidencia de claims
    |-- ModelFeedbackTransformer        → scoring y feedback al modelo
    |-- QualityRecorderTransformer      → registra metricas de calidad
    |
    v
Response (Anthropic format) → Claude Code CLI
```

#### Archivos clave del proxy

```
vendor/claude-code-proxy/
|-- server.py                    # FastAPI — endpoints, pipeline bootstrap
|-- config.py                    # ProxyConfig — toda la configuracion tipada
|-- proxy/proxy.py               # build_request_pipeline, build_response_pipeline, run_messages
|-- llm/
|   |-- pipeline.py              # Transformer, Pipeline, TransformContext (ABC + dataclass)
|   |-- converters.py            # convert_anthropic_to_litellm, convert_litellm_to_anthropic
|   |-- passthrough.py           # PassthroughClient — httpx directo a Anthropic-compatible APIs
|   |-- compressor.py            # Context compression + grounding hop tracking
|   |-- streaming.py             # SSE streaming handler
|   |-- sse.py                   # SSE parser para live output en ralph
|   |-- schemas.py               # Pydantic models (MessagesRequest, content blocks)
|   +-- transformers/
|       |-- intent_classifier.py    # CHAT | PLANNING | BUILDING + EXPLORE|PLAN|EXECUTE
|       |-- intent_enforcement.py   # Valida compliance con intent clasificado
|       |-- guardrail.py            # Policy note injection en system prompt
|       |-- deferred_tools.py       # Plan mode DX: inyecta available-deferred-tools
|       |-- token_cap.py            # MAX_INPUT_TOKENS enforcement
|       |-- tool_allowlist.py       # TOOL_ALLOWLIST filtering
|       |-- adaptive_context.py     # Adaptive routing basado en model quality history
|       |-- model_router.py         # Model selection segun intent + config
|       |-- compression.py          # CompressionTransformer
|       |-- provider_quirks.py      # Fixes por proveedor (Gemini schema, etc.)
|       |-- credential.py           # API key/base_url injection
|       |-- reasoning_handling.py   # Limpieza de thinking blocks
|       |-- universal_tool_extraction.py  # Tool extraction de XML (model-agnostic)
|       |-- grounding_validator.py  # Evidencia de claims, citation map
|       |-- model_feedback.py       # Scoring y feedback
|       +-- quality_recorder.py     # Metricas de calidad
|-- router/
|   |-- llm_router.py            # Intent classifier LLM (con regex fallback)
|   +-- model_mapper.py          # Claude alias → modelo real del proveedor
+-- utils/
    |-- metrics.py               # RequestLog, metricas por sesion
    +-- utils.py                 # Token cache, scale_tokens
```

### Endpoints del proxy

| Endpoint | Metodo | Descripcion |
|----------|--------|-------------|
| `/v1/messages` | POST | Endpoint principal. Recibe Anthropic format, rutea y responde |
| `/v1/messages/count_tokens` | POST | Conteo de tokens (usa LiteLLM con fallback heuristico) |
| `/health` | GET | Status, provider, modelos configurados |
| `/api/logs` | GET | Ultimos N request logs (`?n=20`) |
| `/api/stats` | GET | Metricas de sesion (tokens, intents, latencias) |

---

## 🛠️ Critical Fixes & Debugging

### Problemas Críticos Resueltos

#### 1. Thinking Blocks (HTTP 422 Error)
**Problema:** OpenAI/Gemini no soportan `thinking` blocks de Anthropic, causando HTTP 422.
**Solución:** `ReasoningHandlingTransformer` (response pipeline) strip thinking blocks antes de devolver la respuesta. Schemas Pydantic actualizados en `schemas.py` para parsear todos los tipos de content blocks.
- `vendor/claude-code-proxy/llm/transformers/reasoning_handling.py`
- `vendor/claude-code-proxy/llm/schemas.py`

#### 2. Single-Quote Tool Calls (Tool Execution Fix)
**Problema:** deepseek-reasoner outputs `<tool_call name='X'>` con SINGLE quotes + Python dict syntax `{'key': 'val'}`.
**Solución:** `UniversalToolExtractionTransformer` maneja todos los formatos via `utils/tool_extraction_patterns.py`. `json_repair` convierte Python dict → JSON.
- `vendor/claude-code-proxy/llm/transformers/universal_tool_extraction.py`
- `vendor/claude-code-proxy/utils/tool_extraction_patterns.py`

#### 3. Token Counting
**Problema:** Heurística chars/4 no es precisa.
**Solución:** Reemplazado con `litellm.token_counter()` (determinístico) + chars/3 fallback en compressor.

#### 4. Bare Regex Fallback
**Problema:** Algunos modelos devuelven JSON directamente en `<tool_call>` sin tags internos.
**Solución:** Agregado 3er nivel de regex para tool calls sin inner tags.

### Cómo Debuggear el Proxy

#### Verificar logs del proxy:
```bash
docker-compose logs -f proxy_cloud
docker logs -f ai-tooling-proxy_cloud-1
```

#### Health check detallado:
```bash
cc-health --json | jq
```

#### Verificar configuración activa:
```bash
# Ver variables de entorno del contenedor
docker exec ai-tooling-proxy_cloud-1 env | grep -E "(MODEL|PROVIDER|CLASSIFIER)"
```

#### Testear el proxy directamente:
```bash
curl -X GET http://localhost:8083/health
curl -X POST http://localhost:8083/v1/messages/count_tokens \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-4.7","messages":[{"role":"user","content":"test"}]}'
```

#### Hot-reload debugging:
```bash
# Editar código del proxy
vim vendor/claude-code-proxy/llm/converters.py

# Verificar que los cambios se aplicaron
docker logs --tail 10 ai-tooling-proxy_cloud-1 | grep "reload"
```

### Common Issues & Solutions

| Problema | Posible Causa | Solución |
|----------|---------------|----------|
| **HTTP 422** | Thinking blocks no removidos | Verificar `converters.py` y `schemas.py` |
| **Tool calls fallan** | Single quotes en XML | Verificar regexes en `converters.py` |
| **Modelo incorrecto** | Intent classification falló | Check logs: `docker logs ai-tooling-proxy_cloud-1 | grep -i intent` |
| **API key inválida** | .env incorrecto | Verificar `profile-envs/cloud.*.env` |
| **Timeout** | Classifier LLM lento | Aumentar `CLASSIFIER_TIMEOUT` o usar regex |

---

## FAQ

**Q: Como cambio de modelo sin reiniciar nada?**
Dentro de una sesion de Claude Code: `/model sonnet` o `/model opus`. Para cambiar permanentemente: `cc-switch sonnet`.

**Q: Puedo correr dos proveedores a la vez?**
Si, con diferentes puertos: `BIND_PORT=9090 cc-proxy-up cloud-provider-ymls/docker-compose.groq.override.yml`

**Q: El proxy soporta streaming?**
Si, detecta `stream: true` en el request y devuelve SSE.

**Q: Que pasa si el classifier LLM esta lento o falla?**
Timeout configurable (`CLASSIFIER_TIMEOUT`). Si falla, el proxy continua con regex fallback. Si `CLASSIFIER_MODEL` esta vacio, siempre usa regex.

**Q: Como se si el proxy esta usando mi proveedor correcto?**
`cc-health --json` muestra el provider y modelos activos.

**Q: Donde van las API keys?**
En los archivos `profile-envs/cloud.*.env`. Estan en `.gitignore` para no subirlas a GitHub.
