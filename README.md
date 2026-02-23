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
./install.sh        # crea symlinks en ~/.local/bin + scaffolds ai-notes/
source ~/.zshrc     # agrega ~/.local/bin al PATH
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
|-- scripts/                     # CLI tools (symlinked a ~/.local/bin/)
|-- profile-envs/                # Un .env por proveedor (API keys, modelos)
|-- cloud-provider-ymls/         # Docker compose overrides por proveedor
|-- profiles/                    # Perfiles JSON para Claude Code CLI
|-- templates/                   # Plantillas para AI_CONTEXT, AI_PLAN, AI_LEARNING, GUARDRAILS
|-- ai-notes/                    # Artefactos de sesion (scans, contextos, planes, learnings)
|-- .env                         # Variables globales (policy defaults)
|-- docker-compose.yml           # Servicios Docker (proxy_local + proxy_cloud)
|-- install.sh                   # Instalador de symlinks y scaffolding
|-- CLAUDE.md                    # Instrucciones mandatorias para agentes Claude
+-- CHECKLIST.md                 # Tracker de implementacion
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

Todos los scripts se instalan como symlinks en `~/.local/bin/` via `install.sh`.

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

### Nivel 4: Enforcement automatico (pendiente de implementar)

Ideas para reforzar mas:

1. **Hook de pre-tool en Claude Code**: Usar Claude Code hooks para interceptar cada llamada a tool y validar contra el plan antes de ejecutar
2. **Proxy-side tool gating**: El proxy podria rechazar tool_use blocks que no esten en un allowlist dinamico basado en el plan
3. **Session-aware guardrails**: El proxy podria leer AI_PLAN.md al inicio y solo permitir acciones listadas en el plan
4. **Sistema de permisos por archivo**: Solo modificar archivos listados explicitamente en AI_CONTEXT.md

---

## Templates y ai-notes

### Templates (en `templates/`)

Las plantillas son **inmutables** — nunca se modifican directamente. Se copian a `ai-notes/` para crear instancias de trabajo.

| Template | Proposito | Cuando se usa |
|----------|-----------|---------------|
| `AI_CONTEXT.template.md` | Define scope, inputs, outputs permitidos, comandos | El humano lo llena al inicio de cada tarea |
| `AI_PLAN.template.md` | Estructura del plan con pasos atomicos, riesgos, validacion | `cc-plan` lo usa como guia para generar el plan |
| `AI_LEARNING.template.md` | Estructura para capturar patrones, errores, decisiones | Se scaffoldea en `ai-notes/` via `install.sh` |
| `GUARDRAILS.template.md` | Reglas core de seguridad y feedback loop | Agentes lo leen al inicio de cada sesion |

### ai-notes/ (artefactos de sesion)

**Todo** lo que generan los agentes y humanos va aqui. Nunca a `.claude/` ni a la memoria privada del agente.

| Archivo | Quien lo crea | Quien lo consume |
|---------|--------------|-----------------|
| `AI_CONTEXT.md` | Humano (desde template) | `cc-plan`, `cc-agent-cloud` |
| `AI_PLAN.md` | `cc-plan` (STATUS: DRAFT) | Humano revisa -> REVIEWED. `cc-agent-cloud` valida |
| `AI_LEARNING.md` | Agentes + humanos al final de sesion | Todos los agentes al inicio de sesion |
| `*.analysis.md` | `cc-scan` | Humano, `cc-plan` (referencia en AI_CONTEXT) |
| `GUARDRAILS.md` | `install.sh` (copia de template) | Agentes al inicio de sesion |

### Como pasar esto a Claude Code

Para que Claude Code respete los guardrails y templates, hay **tres mecanismos**:

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

### Arquitectura del proxy

```
Request (Anthropic format)
    |
    v
[server.py] POST /v1/messages
    |
    |-- get_last_user_text() -> extrae texto del ultimo mensaje
    |-- classify_intent() -> CHAT | PLANNING | BUILDING
    |
    v
[proxy/proxy.py] apply_policy_and_routing()
    |
    |-- Valida TOOL_ALLOWLIST
    |-- Checa MAX_INPUT_TOKENS
    |-- Mapea modelo segun intent:
    |     CHAT -> SMALL_MODEL
    |     PLANNING -> BIG_MODEL
    |     BUILDING -> BUILDING_MODEL
    |-- Inyecta POLICY_NOTE si activo
    |
    v
[llm/converters.py] convert_anthropic_to_litellm()
    |
    |-- Strips thinking blocks (OpenAI no los soporta)
    |-- Limpia schemas para Gemini (clean_gemini_schema)
    |-- Convierte content blocks (text, tool_use, tool_result, image)
    |
    v
[LiteLLM] -> Proveedor API
    |
    v
[llm/converters.py] convert_litellm_to_anthropic()
    |
    v
Response (Anthropic format) -> Claude Code CLI
```

### Endpoints del proxy

| Endpoint | Metodo | Descripcion |
|----------|--------|-------------|
| `/v1/messages` | POST | Endpoint principal. Recibe Anthropic format, rutea y responde |
| `/v1/messages/count_tokens` | POST | Conteo de tokens (usa LiteLLM con fallback heuristico) |
| `/health` | GET | Status, provider, modelos configurados |

---

## 🛠️ Critical Fixes & Debugging

### Problemas Críticos Resueltos

#### 1. Thinking Blocks (HTTP 422 Error)
**Problema:** OpenAI/Gemini no soportan `thinking` blocks de Anthropic, causando HTTP 422.
**Solución:** Agregados `ContentBlockThinking`, `ContentBlockRedactedThinking`, `ContentBlockServerToolUse`, `ContentBlockServerToolResult` a `schemas.py`. Thinking blocks son removidos en `converters.py` al convertir a OpenAI format.
- `vendor/claude-code-proxy/llm/schemas.py`
- `vendor/claude-code-proxy/llm/converters.py`

#### 2. Single-Quote Tool Calls (Tool Execution Fix)
**Problema:** deepseek-reasoner outputs `<tool_call name='X'>` con SINGLE quotes + Python dict syntax `{'key': 'val'}`.
**Solución:** Actualizados todos los regexes (`_TOOL_CALL_RE`, `_TOOL_CALL_FALLBACK_RE`, `_TOOL_CALL_BARE_RE`, `_PARTIAL_TOOL_RE`) para aceptar ambos estilos de quotes via `_NAME_ATTR` pattern. `json_repair` convierte Python dict → JSON.
- `vendor/claude-code-proxy/llm/converters.py`

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
