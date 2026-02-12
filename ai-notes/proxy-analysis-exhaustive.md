# Analisis Exhaustivo del Claude Code Proxy

> Fecha: 2026-02-11
> Generado por: claude-opus-4-6

## Arquitectura General

El proxy intercepta requests de Claude Code (formato Anthropic API) y los traduce al formato OpenAI para rutearlos a providers alternativos (Z.AI, Groq, Gemini, DeepSeek, Ollama). Claude Code no sabe que esta hablando con otro modelo.

```
Claude Code (Anthropic API)
    │
    ▼
┌─────────────────────────────────────────────────┐
│  server.py (FastAPI)                            │
│  Endpoints: /v1/messages, /v1/messages/count_tokens │
│             /health, /api/stats, /api/logs      │
├─────────────────────────────────────────────────┤
│  1. Intent Classification (llm_router.py)       │
│  2. Policy & Routing (proxy.py)                 │
│  3. Anthropic→OpenAI Conversion (converters.py) │
│  4. Context Compression (compressor.py)         │
│  5. Provider Call + Retry + Fallback (proxy.py)  │
│  6. OpenAI→Anthropic Response (converters.py    │
│     / streaming.py)                             │
└─────────────────────────────────────────────────┘
    │
    ▼
Provider (Z.AI / Groq / Gemini / DeepSeek / Ollama)
```

---

## Modulos y Funcionalidades

### 1. server.py — Punto de Entrada FastAPI

**Archivo:** `vendor/claude-code-proxy/server.py`

#### Endpoints:

| Endpoint | Metodo | Funcion |
|----------|--------|---------|
| `/v1/messages` | POST | Endpoint principal — recibe requests Anthropic, rutea, convierte, responde |
| `/v1/messages/count_tokens` | POST | Conteo de tokens con cache y escalado |
| `/health` | GET | Health check con info de provider, modelos, clasificador, fallbacks |
| `/api/stats` | GET | Metricas agregadas: requests, latencia, fallback rate, cache hits |
| `/api/logs` | GET | Ultimos N request logs (ring buffer de 200) |

#### Funcionalidades en server.py:

**F1. Clasificacion de errores tipados (`_classify_llm_error`)**
- Mapea excepciones LiteLLM a HTTP status codes
- Orden: especifico → general (ContextWindowExceededError antes de BadRequestError)
- Fallback a string heuristics para excepciones no-LiteLLM
- Previene el generico "500 Internal Server Error" que ocultaba el error real

**F2. Cadena de fallback providers (`_load_fallback_providers`)**
- Lee FALLBACK_1_*, FALLBACK_2_*, ... FALLBACK_9_* del env
- Cada fallback tiene: provider_prefix, api_key, base_url, big/small/building_model, context_window
- Se carga una sola vez al startup

**F3. Response cache LiteLLM**
- `CACHE_ENABLED=1` activa cache in-memory con TTL configurable
- Usa `litellm.Cache(type="local")` — reduce API calls duplicados en retries/bursts

**F4. Intent classification dispatch**
- Si `CLASSIFIER_MODEL` esta configurado: usa LLM (deepseek-chat)
- Si no: regex fallback (`_regex_fallback_intent`)

**F5. Metricas y observabilidad**
- Cada request se registra como `RequestLog` (intent, model, provider, tokens, latencia)
- Thread-safe via Lock + ring buffer de 200 entries

---

### 2. llm/schemas.py — Modelos Pydantic (Anthropic API)

**Archivo:** `vendor/claude-code-proxy/llm/schemas.py`

#### Content Block Types soportados:

| Tipo | Clase | Rol | Descripcion |
|------|-------|-----|-------------|
| `text` | ContentBlockText | user/assistant | Texto plano |
| `image` | ContentBlockImage | user | Imagenes (base64/url) |
| `tool_use` | ContentBlockToolUse | assistant | Llamada a herramienta (id, name, input) |
| `tool_result` | ContentBlockToolResult | user | Resultado de herramienta (tool_use_id, content) |
| `thinking` | ContentBlockThinking | assistant | Extended thinking (thinking, signature) |
| `redacted_thinking` | ContentBlockRedactedThinking | assistant | Thinking redactado (data) |
| `server_tool_use` | ContentBlockServerToolUse | assistant | Tool use del servidor (web_search, etc.) |
| `server_tool_result` | ContentBlockServerToolResult | user | Resultado de server tool |

#### Modelos de request/response:

- **MessagesRequest**: model, max_tokens, messages, system, tools, tool_choice, thinking, stream, temperature, etc.
- **MessagesResponse**: id, model, content (text + tool_use), stop_reason, usage
- **TokenCountRequest/Response**: para /v1/messages/count_tokens
- **ProviderConfig**: configuracion de un provider (primary o fallback)
- **Usage**: input_tokens, output_tokens, cache_creation/read tokens

#### Validacion Claude Code:
- `original_model` se preserva via `@field_validator` para devolver el alias original en responses
- El Union type en `Message.content` cubre TODOS los content block types de la Anthropic API
- Si falta un tipo → Pydantic rechaza con 422 ANTES de llegar al converter

---

### 3. llm/converters.py — Conversion Bidireccional

**Archivo:** `vendor/claude-code-proxy/llm/converters.py`

#### F6. Anthropic → LiteLLM/OpenAI (`convert_anthropic_to_litellm`)

Convierte el request completo:
- **System prompt**: str o lista de bloques → string plano
- **Messages**: cada mensaje Anthropic puede expandirse a multiples mensajes OpenAI
  - `assistant` con `tool_use` → `assistant` con `tool_calls` array
  - `user` con `tool_result` → mensajes `role:"tool"` individuales
  - `thinking`/`redacted_thinking` → STRIPEADOS (providers OpenAI no los entienden)
  - `server_tool_use` → texto fallback `[ServerTool: name (ID: id)]`
  - `server_tool_result` → texto fallback
  - `image` → `[Image content omitted]`
- **Tools**: Anthropic `input_schema` → OpenAI `function.parameters`
- **tool_choice**: `auto`→`auto`, `any`→`required`, `tool{name}`→`{type:"function",function:{name}}`
- **max_tokens**: cap a 16384 para `openai/` non-reasoning models. Sin cap para reasoning y gemini

#### F7. Tools conversion con cache (`_convert_tool_cached`)
- Key compuesto: `name:g/o:sha256(schema)[:16]`
- Separacion gemini vs no-gemini del mismo tool
- Evita reconvertir las mismas ~15 tools en cada request

#### F8. Gemini schema sanitization (`clean_gemini_schema` / `clean_gemini_schema_cached`)
- Gemini/Vertex rechaza keywords avanzadas de JSON Schema
- Limpia: `$ref`, `allOf`, `anyOf`, `oneOf`, `additionalProperties`, `const`, exclusive bounds, etc.
- Normaliza `type: ["string", "null"]` → `type: "string"`
- Reescribe `const` → `enum: [valor]`
- Memoizado con SHA-256

#### F9. LiteLLM → Anthropic (`convert_litellm_to_anthropic`)
- Convierte response OpenAI a formato MessagesResponse
- **tool_calls**: genera `tool_use` blocks con ID `toolu_*` (formato Anthropic)
- **JSON repair**: si arguments es JSON malformado → `repair_json` → fallback `{"raw": ...}`
- **reasoning_content**: se surfacea como `<reasoning>...</reasoning>` text block
- **finish_reason mapping**: `length`→`max_tokens`, `tool_calls`→`tool_use`, otros→`end_turn`
- **XML tool extraction**: para no-tools models, parsea `<tool_call>` XML del texto

---

### 4. llm/streaming.py — Streaming SSE Anthropic

**Archivo:** `vendor/claude-code-proxy/llm/streaming.py`

#### F10. Conversion de stream OpenAI a SSE Anthropic (`handle_streaming`)

Genera Server-Sent Events en formato Anthropic:

**Secuencia de eventos:**
```
event: message_start      → {type: "message_start", message: {id, role, model, content:[], usage}}
event: content_block_start → {type: "content_block_start", index: 0, content_block: {type: "text"}}
event: ping               → {type: "ping"}
event: content_block_delta → {type: "content_block_delta", index: 0, delta: {type: "text_delta", text: "..."}}
  ... (multiples deltas de texto) ...
event: content_block_stop  → {type: "content_block_stop", index: 0}

  (si hay tool calls:)
event: content_block_start → {type: "content_block_start", index: 1, content_block: {type: "tool_use", id: "toolu_*", name: "..."}}
event: content_block_delta → {type: "content_block_delta", index: 1, delta: {type: "input_json_delta", partial_json: ""}}
event: content_block_delta → {type: "content_block_delta", index: 1, delta: {type: "input_json_delta", partial_json: "..."}}
event: content_block_stop  → {type: "content_block_stop", index: 1}

event: message_delta       → {type: "message_delta", delta: {stop_reason: "end_turn"/"tool_use"/"max_tokens"}, usage: {output_tokens: N}}
event: message_stop        → {type: "message_stop"}
data: [DONE]
```

**Manejo de estados:**
- `tool_index`: tracking del tool_call actual (por index de OpenAI)
- `text_block_closed`: evita emitir text deltas despues de cerrar el bloque
- `text_sent`: tracking de si ya se envio texto
- `tool_args_buffer`: acumula argumentos JSON parciales por tool para repair

#### F11. JSON repair en streaming (`_compute_repair_suffix`)
- Al cerrar un tool block, verifica si el JSON acumulado es valido
- Si no: `repair_json` para generar un suffix que complete el JSON truncado
- Se emite como un `input_json_delta` extra antes del `content_block_stop`

#### F12. XML tool buffer en streaming
- Para no-tools models, el `XmlToolBuffer` procesa texto en chunks
- Detecta `<tool_call` parciales y espera a que se complete
- Cuando detecta un tool_call completo: cierra text block, emite tool_use block
- En flush(): si hay `<tool_call` incompleto, intenta recovery via LLM

---

### 5. llm/tool_prompting.py — Simulacion XML de Tools

**Archivo:** `vendor/claude-code-proxy/llm/tool_prompting.py`

#### F13. Deteccion de modelos sin tools (`is_no_tools_model`)
- Lee `NO_TOOLS_MODELS` del env (comma-separated, e.g. "deepseek-reasoner")
- `@lru_cache(maxsize=1)` + `frozenset` para config inmutable
- Matching por substring case-insensitive

#### F14. Prompt builder XML (`build_tool_prompt`)
- Convierte definiciones de tools Anthropic a un prompt XML legible
- Formato: `<tool_call name="..."><input>{JSON}</input></tool_call>`
- Incluye reglas estrictas: usar exactamente `<input>`, JSON valido, no anidar
- Descriptions truncadas a 200 chars para reducir prompt size

#### F15. Reescritura de historial (`rewrite_messages_without_tools`)
- `assistant` con `tool_calls` → texto con `<tool_call>` XML
- `role:"tool"` → `role:"user"` con `<tool_result>` XML
- `_merge_consecutive_messages`: fusiona mensajes adyacentes con el mismo role

#### F16. Parser de respuesta XML (`extract_tool_calls_from_text`)
- Regex primario: acepta `<input>`, `<textarea>`, `<arguments>`, `<params>`, `<json>`, `<content>`, `<parameters>`
- Regex fallback: acepta cualquier tag `<(\w+)>...</\1>`
- `_safe_parse_tool_input`: 3 niveles de fallback: `json.loads` → `repair_json` → `{"raw_input": ...}`
- NUNCA lanza excepciones

#### F17. Recovery de tool calls truncados (`recover_incomplete_tool_call`)
- Si el stream se corta mid-`<tool_call>`, usa el modelo clasificador para completar
- Prompt: "Complete this truncated XML tool call"
- Usa `extract_tool_calls_from_text` sobre la respuesta del LLM
- Fallback: emite como texto plano

#### F18. XmlToolBuffer — State machine de streaming
- **_try_extract_text**: busca `<tool_call` en buffer, emite texto safe
- **_try_extract_tool**: espera `</tool_call>`, parsea XML completo
- **_safe_text_end**: evita emitir texto que podria ser inicio de `<tool_call` parcial
- **_parse_tool_xml**: regex primario + fallback, siempre retorna algo (tool_call o text)

---

### 6. llm/compressor.py — Compresion de Contexto

**Archivo:** `vendor/claude-code-proxy/llm/compressor.py`

#### F19. Compresion inteligente de contexto (`compress_messages_if_needed`)

**Trigger:** tokens estimados > 85% del context window del modelo

**Proceso:**
1. Separa: system_msg + old_messages + recent_messages (ultimos N)
2. Serializa old_messages a texto (con truncado de mensajes largos a 3000 chars)
3. Llama a LLM barato (COMPRESSOR_MODEL) con prompt de sumarizacion
4. Reensamblado: [system] + [summary como user msg] + [ack como assistant msg] + [recent msgs]
5. **Fallback**: si LLM falla → trimming simple (descartar old, mantener recent)

**Config:**
- `COMPRESSOR_MODEL` / `COMPRESSOR_API_KEY` / `COMPRESSOR_BASE_URL`
- Fallback a `CLASSIFIER_*` vars (zero-config si clasificador ya esta configurado)
- `COMPRESSOR_KEEP_RECENT=15` (configurable)

**Prompt de compresion:**
- PRESERVA: file paths, tool names, function names, error messages, key decisions, code snippets
- REMUEVE: verbose tool outputs, repetitive explanations, intermediate reasoning
- Limite: ~2000 tokens
- Formato: bullet points con issues pendientes

---

### 7. proxy/proxy.py — Policy Engine + Routing + Fallback

**Archivo:** `vendor/claude-code-proxy/proxy/proxy.py`

#### F20. Guardrails injection (`_load_guard_system`, `BASE_GUARD_SYSTEM`)
- Siempre inyecta guardrail base: "si no tienes acceso a tools, NO inventes"
- Opcionalmente carga guardrails extra desde `GUARDRAILS_FILE`

#### F21. Policy and routing (`apply_policy_and_routing`)

**Pipeline en orden:**
1. **Guardrail injection**: inserta nota en system prompt
2. **Provider cap check**: limita tokens por provider (Groq: 5500, Ollama: 25000)
3. **Hard cap**: `MAX_INPUT_TOKENS` con opcion `HARD_BLOCK_OVERSIZE` (413 error)
4. **Tool allowlist**: filtra tools por `TOOL_ALLOWLIST` ("*" = todas, "" = ninguna)
5. **Model mapping**: `map_claude_alias_to_target` (haiku→small, sonnet/opus→big)
6. **Intent-based routing**:
   - Ollama: scoring heuristico complejo (tokens, system_chars, tools, max_out, intent)
   - Cloud: 3-way routing (CHAT→small, BUILDING→building, PLANNING→big)

#### F22. Provider call con first-chunk validation (`_call_provider`)
- Streaming: consume primer chunk ANTES de commitear (valida conexion)
- Non-streaming: llamada sincrona `litellm.completion`
- Trackea cache hits/misses de LiteLLM

#### F23. Retry con exponential backoff (`_call_provider_with_retry`)
- `MAX_RETRIES=5`, `RETRY_BASE_DELAY=1.0` (delays: 1s, 2s, 4s, 8s, 16s)
- **Retryable**: rate limits, timeouts, connection errors, server errors
- **No retryable**: context window exceeded, bad request, auth errors
- Metricas: total_retries, retry_successes

#### F24. Fallback chain (`run_messages`)
- Intenta primary provider con retry
- Si falla: itera por FALLBACK_PROVIDERS en orden
- Cada fallback: re-convierte request, inyecta sus credenciales, retry
- Si todos fallan: excepcion con lista de todos los errores
- Retorna 3-tuple: `(is_stream, response, provider_name)` para observabilidad

#### F25. Credential injection (`_inject_credentials`)
- `openai/*` → api_key + optional base_url
- `gemini/*` → api_key o vertex_project/location
- Otros → anthropic api_key

---

### 8. router/model_mapper.py — Mapeo de Aliases

**Archivo:** `vendor/claude-code-proxy/router/model_mapper.py`

#### F26. Mapeo de aliases Claude (`map_claude_alias_to_target`)
- `claude-haiku-*` → `{provider_prefix}/{small_model}`
- `claude-sonnet-*` / `claude-opus-*` → `{provider_prefix}/{big_model}`
- Si ya tiene prefijo (`openai/`, `gemini/`, `anthropic/`) → se respeta
- Si no es alias Claude → `{provider_prefix}/{model_as_is}`

---

### 9. router/llm_router.py — Intent Classification + Local Routing

**Archivo:** `vendor/claude-code-proxy/router/llm_router.py`

#### F27. Clasificador de intent con LLM (`classify_intent`)
- Modelo barato (deepseek-chat) con prompt de clasificacion
- 3 categorias: PLANNING, BUILDING, CHAT
- Timeout configurable (default 3-5s)
- Trunca mensaje a 1000 chars para velocidad
- Fallback a regex si LLM falla o timeout

#### F28. Regex fallback (`_regex_fallback_intent`)
- `PLANNING_RE`: plan, design, architect, evaluate, compare, etc. (ingles + español)
- `BUILDING_RE`: implement, fix, refactor, test, deploy, etc. (ingles + español)
- Si matchea ambos → CHAT (ambiguo)

#### F29. Routing local/Ollama (`choose_local_model`)
- Sistema de scoring heuristico:
  - messages > 10 → +2 big
  - tokens > 6000 → +3 big
  - system_chars > 4000 → +1 big
  - max_out > 900 → +2 build, +1 big
  - tools > 0 → +2 big
  - PLANNING intent → +3 big
  - BUILDING intent → +3 build
- score_build >= 3 → building_model
- score_big >= 3 → big_model
- Else → small_model

---

### 10. utils/utils.py — Utilidades

**Archivo:** `vendor/claude-code-proxy/utils/utils.py`

#### F30. Token scaling (`scale_tokens`)
- Claude Code asume 200K context window
- Si modelo tiene menos (e.g. 128K), escala proporcionalmente
- Formula: `raw_count * (200000 / model_context_window)`
- Efecto: Claude Code activa auto-compactacion mas temprano

#### F31. Token count cache
- SHA-256 hash de messages + model + system
- FIFO eviction a 256 entries
- Elimina ~80% de llamadas a token_counter en sesiones multi-turno

#### F32. Tool allowlist (`parse_allowlist`, `filter_tools_allowlist`)
- `""` → drop all tools
- `"*"` → allow all
- `"tool1,tool2"` → solo esas

#### F33. System note injection (`ensure_system_note`)
- Dedupe: no inyecta si el texto ya esta presente
- Soporta system como string o lista de bloques

#### F34. Tool choice normalization (`normalize_tool_choice`)
- Si tool_choice refiere a tool que fue droppeada → fallback a "auto"

---

### 11. utils/metrics.py — Observabilidad

**Archivo:** `vendor/claude-code-proxy/utils/metrics.py`

#### F35. Sistema de metricas (`ProxyMetrics`)
- Thread-safe (Lock)
- Ring buffer de 200 `RequestLog` entries
- Contadores: requests, errors, fallbacks, tokens, retries, cache hits/misses, classifier stats
- Per-provider: requests, errors, avg_latency
- Per-intent: counts (PLANNING/BUILDING/CHAT)

---

## Compatibilidad con Claude Code Tool Format

### Lo que Claude Code espera (formato Anthropic SSE):

1. **Tool definitions** en request:
```json
{
  "tools": [{
    "name": "mcp__server__tool",
    "description": "...",
    "input_schema": {"type": "object", "properties": {...}}
  }]
}
```

2. **Tool use** en response (content blocks):
```json
{
  "type": "tool_use",
  "id": "toolu_...",
  "name": "mcp__server__tool",
  "input": {"path": "/...", "data": "..."}
}
```

3. **Streaming tool use** (SSE events):
```
event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_...","name":"...","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"path\":\"/foo\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}
```

4. **stop_reason** must be `"tool_use"` when tools are invoked

### Lo que el proxy genera:

| Aspecto | Esperado por CC | Generado por Proxy | Status |
|---------|-----------------|-------------------|--------|
| Tool IDs | `toolu_*` (24 hex chars) | `toolu_{uuid.hex[:24]}` | OK |
| Tool use blocks | `{type:"tool_use", id, name, input}` | Exacto | OK |
| Streaming tool start | `content_block_start` con type/id/name/input:{} | Exacto | OK |
| Streaming tool delta | `input_json_delta` con `partial_json` | Exacto | OK |
| Empty initial delta | `partial_json: ""` | Emitido en linea 186 | OK |
| stop_reason | `"tool_use"` | `finish_reason == "tool_calls"` → `"tool_use"` | OK |
| stop_reason text | `"end_turn"` | `finish_reason != "length"/"tool_calls"` → `"end_turn"` | OK |
| stop_reason max | `"max_tokens"` | `finish_reason == "length"` → `"max_tokens"` | OK |
| message_start | `{type:"message_start", message:{id, type, role, model, content:[], usage}}` | Exacto | OK |
| message_delta | `{type:"message_delta", delta:{stop_reason, stop_sequence}, usage:{output_tokens}}` | Exacto | OK |
| message_stop | `{type:"message_stop"}` | Exacto | OK |
| ping | `{type:"ping"}` | Emitido despues de content_block_start | OK |
| `[DONE]` | `data: [DONE]` | Emitido al final | OK |
| model en response | Alias original (e.g. `claude-opus-4-6`) | `original_model` preservado | OK |

### Posibles gaps de compatibilidad:

1. **`input_tokens` en message_start**: El proxy emite `input_tokens: 0` en message_start. La spec Anthropic envia el conteo real. Claude Code parece no validar estrictamente esto.

2. **`cache_creation_input_tokens` / `cache_read_input_tokens`**: Emitidos como 0. Claude Code los usa para prompt caching metrics pero no afecta funcionalidad.

3. **No hay `content_block_start` de tipo `thinking`**: Los bloques thinking se stripean. Esto es correcto porque el provider destino no genera thinking blocks.

4. **`message_delta.usage` solo tiene `output_tokens`**: Segun spec Anthropic, message_delta solo requiere output_tokens. OK.

---

## Variables de Entorno (Configuracion Completa)

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `OPENAI_API_KEY` | "" | API key del provider OpenAI-compatible |
| `OPENAI_BASE_URL` | None | Base URL override (Z.AI, Groq, DeepSeek, etc.) |
| `ANTHROPIC_API_KEY` | None | Si proxying directo a Anthropic |
| `GEMINI_API_KEY` | None | Google AI Studio key |
| `PREFERRED_PROVIDER` | "openai" | Provider principal (openai/google/anthropic) |
| `BIG_MODEL` | =SMALL_MODEL | Modelo para PLANNING + default |
| `SMALL_MODEL` | "cc-local:chat" | Modelo para CHAT intent |
| `BUILDING_MODEL` | =BIG_MODEL | Modelo para BUILDING intent |
| `MODEL_CONTEXT_WINDOW` | 0 | Context window del modelo target (0=no scaling) |
| `CLASSIFIER_MODEL` | "" | Modelo para intent classifier (e.g. "openai/deepseek-chat") |
| `CLASSIFIER_API_KEY` | "" | API key del clasificador |
| `CLASSIFIER_BASE_URL` | None | Base URL del clasificador |
| `CLASSIFIER_TIMEOUT` | 3.0 | Timeout del clasificador en segundos |
| `COMPRESSOR_MODEL` | =CLASSIFIER_MODEL | Modelo para compresion de contexto |
| `COMPRESSOR_API_KEY` | =CLASSIFIER_API_KEY | API key del compresor |
| `COMPRESSOR_BASE_URL` | =CLASSIFIER_BASE_URL | Base URL del compresor |
| `COMPRESSOR_KEEP_RECENT` | 15 | Mensajes recientes a mantener intactos |
| `CACHE_ENABLED` | "0" | Activar cache de respuestas LiteLLM |
| `CACHE_TTL` | 60 | TTL del cache en segundos |
| `MAX_RETRIES` | 5 | Max intentos con backoff |
| `RETRY_BASE_DELAY` | 1.0 | Delay base del backoff exponencial |
| `MAX_INPUT_TOKENS` | 0 | Cap de tokens de input (0=sin limite) |
| `HARD_BLOCK_OVERSIZE` | "0" | Rechazar con 413 si excede cap |
| `TOOL_ALLOWLIST` | "" | Tools permitidas ("*"=todas, ""=ninguna) |
| `POLICY_NOTE_IN_SYSTEM` | "1" | Inyectar notas de policy en system prompt |
| `NO_TOOLS_MODELS` | "" | Modelos sin native function calling (comma-separated) |
| `GUARDRAILS_FILE` | "" | Archivo con guardrails extra |
| `FALLBACK_N_*` | — | Cadena de fallback (N=1..9): PROVIDER, API_KEY, BASE_URL, BIG_MODEL, SMALL_MODEL |

---

## Flujo Completo de un Request

```
1. Claude Code envia POST /v1/messages (formato Anthropic)
   └─ Body: {model: "claude-opus-4-6", messages: [...], tools: [...], stream: true}

2. Pydantic valida (schemas.py)
   └─ Preserva original_model = "claude-opus-4-6"

3. Intent classification (llm_router.py)
   └─ LLM: "Arregla el bug de auth" → BUILDING
   └─ Regex fallback si LLM timeout/error

4. Policy & Routing (proxy.py)
   ├─ Inyecta guardrail en system
   ├─ Checa provider cap + max_input_tokens
   ├─ Filtra tools por allowlist
   ├─ Mapea modelo: "claude-opus-4-6" → "openai/glm-4.7"
   └─ Intent routing: BUILDING → "openai/glm-4.7" (building_model)

5. Conversion Anthropic → OpenAI (converters.py)
   ├─ System: str/list → string plano
   ├─ Messages: tool_use→tool_calls, tool_result→role:tool, thinking→stripped
   ├─ Tools: Anthropic schema → OpenAI function format (cached)
   └─ (NO_TOOLS_MODELS): tools→XML prompt, history→XML text

6. Context compression (compressor.py)
   └─ Si tokens > 85% window → LLM summarize old msgs → fallback trim

7. Provider call (proxy.py)
   ├─ Primary con retry (exponential backoff, max 5)
   ├─ Si falla → fallback_1, fallback_2, ... con retry cada uno
   └─ Streaming: valida primer chunk antes de commitear

8. Response conversion
   ├─ Non-streaming (converters.py): OpenAI response → MessagesResponse
   │   ├─ tool_calls → tool_use blocks (toolu_* IDs)
   │   ├─ JSON repair si arguments truncados
   │   └─ (NO_TOOLS_MODELS): XML extraction → tool_use blocks
   └─ Streaming (streaming.py): OpenAI deltas → SSE Anthropic
       ├─ message_start → content_block_start → ping
       ├─ text deltas → content_block_delta (text_delta)
       ├─ tool_calls → content_block_start (tool_use) + input_json_delta
       ├─ JSON repair suffix al cerrar tool blocks
       ├─ (NO_TOOLS_MODELS): XmlToolBuffer state machine
       └─ message_delta (stop_reason) → message_stop → [DONE]

9. Metricas registradas (metrics.py)
   └─ RequestLog: intent, model, provider, tokens, latency, fallback flag
```
