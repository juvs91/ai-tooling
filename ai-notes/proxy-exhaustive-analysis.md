# Claude Code Proxy - Analisis Exhaustivo

**Fecha**: 2026-02-17
**Ruta**: `vendor/claude-code-proxy/`
**Score de completitud**: 92/100

---

## 1. Resumen Ejecutivo

El Claude Code Proxy es un servidor **FastAPI** que intercepta solicitudes de la API Anthropic, las convierte a formato **LiteLLM/OpenAI**, y las enruta a multiples proveedores de LLM (Z.AI, Groq, Gemini, DeepSeek, Ollama) con cadenas de fallback.

### Caracteristicas unicas
- **Simulacion XML de herramientas** para modelos sin function calling nativo
- **Clasificacion de intencion** (LLM + regex) para routing inteligente
- **Compresion de contexto** para conversaciones largas
- **Cadena de fallback multi-proveedor** con retry exponencial
- **Reparacion JSON en streaming** con deteccion de artefactos
- **Enforcement de herramientas** para requests de analisis
- **Metricas y observabilidad** completas

### Flujo de arquitectura
```
Claude Code Request
  -> FastAPI (server.py)
    -> Intent Classification (llm_router.py)
    -> Policy & Routing (proxy.py)
      -> Model Mapping (model_mapper.py)
      -> Tool Allowlist Filtering (utils.py)
      -> Guardrail Injection (proxy.py)
    -> Format Conversion (converters.py)
    -> Context Compression (compressor.py) [si excede ventana]
    -> Provider Call + Retry (proxy.py)
      -> Fallback Chain [si falla primario]
    -> Response Conversion (converters.py / streaming.py)
    -> Metrics Recording (metrics.py)
  -> Anthropic Response to Claude Code
```

---

## 2. Analisis Modulo por Modulo

---

### 2.1 server.py (412 lineas)

**Rol**: Aplicacion FastAPI principal, punto de entrada de requests.

#### Variables de entorno cargadas (~30)

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `OPENAI_API_KEY` | "" | API key para OpenAI/compatible |
| `OPENAI_BASE_URL` | None | URL base (Z.AI, Groq, etc.) |
| `ANTHROPIC_API_KEY` | None | API key Anthropic |
| `GEMINI_API_KEY` | None | API key Google Gemini |
| `VERTEX_PROJECT` | "unset" | Proyecto Vertex AI |
| `VERTEX_LOCATION` | "unset" | Region Vertex AI |
| `USE_VERTEX_AUTH` | False | Usar autenticacion Vertex |
| `TOOL_ALLOWLIST` | "" | Lista de herramientas permitidas |
| `POLICY_NOTE_IN_SYSTEM` | "1" | Inyectar notas de politica |
| `MAX_INPUT_TOKENS` | 0 | Limite global de tokens (0=ilimitado) |
| `HARD_BLOCK_OVERSIZE` | "0" | Bloquear requests oversized |
| `PREFERRED_PROVIDER` | "openai" | Proveedor preferido |
| `SMALL_MODEL` | "cc-local:chat" | Modelo para CHAT |
| `BIG_MODEL` | SMALL_MODEL | Modelo para PLANNING |
| `BUILDING_MODEL` | BIG_MODEL | Modelo para BUILDING |
| `MODEL_CONTEXT_WINDOW` | 0 | Ventana de contexto del modelo |
| `CLASSIFIER_MODEL` | "" | Modelo LLM para clasificacion |
| `CLASSIFIER_API_KEY` | "" | API key del clasificador |
| `CLASSIFIER_BASE_URL` | None | URL base del clasificador |
| `CLASSIFIER_TIMEOUT` | "3.0" | Timeout del clasificador (s) |
| `CACHE_ENABLED` | "0" | Cache de respuestas |
| `CACHE_TTL` | "60" | TTL del cache (s) |
| `ANALYSIS_ENFORCEMENT` | "0" | Enforcement de herramientas para analisis |
| `MAX_RETRIES` | "5" | Max reintentos por proveedor |
| `RETRY_BASE_DELAY` | "1.0" | Delay base exponencial (s) |
| `TOOL_UPGRADE_THRESHOLD` | "5" | # tools que fuerza BUILDING intent |
| `FALLBACK_N_*` | varies | Cadena de fallback (1-9) |

#### Funciones

**`_load_fallback_providers() -> list[ProviderConfig]`**
- Carga proveedores fallback de env vars `FALLBACK_1_*` a `FALLBACK_9_*`
- Se detiene en el primer gap (sin PROVIDER o API_KEY)
- Retorna lista de `ProviderConfig`

**`_classify_llm_error(e: Exception) -> tuple[int, str]`**
- Mapea excepciones a codigos HTTP + detalle
- 3 niveles: excepciones tipadas LiteLLM -> atributo status_code -> heuristicas string
- Retorna (400-504, mensaje truncado a 300 chars)

#### Endpoints

**`POST /v1/messages`** - `create_message(request, raw_request)`
- Endpoint principal. Flujo completo:
  1. Clasifica intent (LLM o regex)
  2. Override: tools >= threshold -> BUILDING
  3. Detecta requests de analisis
  4. Aplica policy y routing
  5. Ejecuta con fallback chain
  6. Registra metricas
  7. Retorna response Anthropic (stream o JSON)

**`POST /v1/messages/count_tokens`** - `count_tokens_endpoint(request)`
- Cuenta tokens estilo Anthropic
- Mapea alias, convierte formato, usa cache + litellm token_counter + fallback chars/4

**`GET /health`** - `health_check()`
- Estado de salud con configuracion actual (provider, modelos, classifier, fallbacks)

**`GET /api/stats`** - `get_stats()`
- Metricas agregadas del proxy

**`GET /api/logs`** - `get_logs(n=50)`
- Ultimos N logs de requests (max 200)

---

### 2.2 proxy/proxy.py (461 lineas)

**Rol**: Logica central del proxy - politicas, routing, fallback chain.

#### Constantes

- **`_DEFAULT_GUARD`**: Guardrail base que previene fabricacion sin acceso a herramientas
- **`BASE_GUARD_SYSTEM`**: Guardrail base + archivo externo (si `GUARDRAILS_FILE` existe)

#### Funciones

**`_load_guard_system() -> str`**
- Carga guardrails desde archivo si `GUARDRAILS_FILE` esta seteado
- Concatena default + archivo externo

**`_build_tool_enforcement_prompt(tools: list | None) -> str`**
- Construye prompt dinamico desde las herramientas del request
- Lista nombres de tools y fuerza su uso
- Se inyecta cuando `ANALYSIS_ENFORCEMENT=1` + `is_analysis_request()`

**`is_ollama_base(base_url: Optional[str]) -> bool`**
- Detecta Ollama buscando "11434" en la URL

**`system_chars(system_field: Any) -> int`**
- Cuenta caracteres totales del system prompt (str o lista de bloques)

**`provider_cap_for_base_url(base_url: Optional[str]) -> int`**
- Caps especificos: Groq=5500, Ollama=25000, otros=0

**`apply_policy_and_routing(...) -> Tuple[int, list[str]]`**
- **Funcion central de politicas**. 5 pasos:
  1. Inyecta guardrail system note
  2. Inyecta tool enforcement (si analisis)
  3. Verifica caps por proveedor y global
  4. Filtra tools por allowlist
  5. Mapea modelo (alias -> target) + seleccion por intent
- Retorna (approx_tokens, dropped_tool_names)

**`_call_provider(request_obj, litellm_request) -> Tuple[bool, Any]`**
- Ejecuta una llamada LiteLLM (stream o sync)
- Para streaming: valida primer chunk antes de commit
- Trackea cache hits

**`_is_retryable_error(error: Exception) -> bool`**
- **Retryable**: rate limits, timeouts, conexion, errores server transitorios
- **No retryable**: context window exceeded, bad request, auth

**`_call_provider_with_retry(...) -> Tuple[bool, Any]`**
- Retry con backoff exponencial: 1s, 2s, 4s, 8s, 16s
- Trackea metricas de retry

**`_inject_credentials(litellm_request, ...) -> None`**
- Inyecta API keys y URLs segun prefijo del modelo
- Soporta: openai/, gemini/ (con Vertex opcional), anthropic/

**`run_messages(...) -> Tuple[bool, Any, str]`**
- **Funcion principal de ejecucion**:
  1. Convierte Anthropic -> LiteLLM
  2. Comprime contexto si necesario
  3. Inyecta credenciales
  4. Intenta proveedor primario con retry
  5. Fallback chain si falla
- Retorna (is_streaming, response, provider_name)

---

### 2.3 llm/converters.py (675 lineas)

**Rol**: Conversion bidireccional Anthropic <-> LiteLLM/OpenAI.

#### Funciones

**`_bget(block, key, default=None) -> Any`**
- Accessor unificado para Pydantic models y dicts

**`_safe_json(obj, ensure_ascii=False) -> str`**
- json.dumps con fallback a str()

**`_extract_tool_fields(block) -> tuple[str, str, Any]`**
- Extrae (name, id, input) de bloques tool_use/server_tool_use

**`clean_gemini_schema(schema) -> Any`**
- Sanitizador para schemas de Gemini/Vertex
- Elimina keywords no soportados (anyOf, oneOf, $ref, etc.)
- Funciones internas: `_merge_dict`, `_normalize_type`, `_rewrite_const`, `_rewrite_exclusive_bounds`, `_drop_unsupported_keys`, `_clean`

**`clean_gemini_schema_cached(schema) -> Any`**
- Wrapper memoizado de `clean_gemini_schema` (cache por SHA256)

**`_convert_tool_cached(tool_dict, is_gemini) -> dict`**
- Convierte tool Anthropic -> OpenAI con cache por nombre+schema hash

**`_system_to_text(system) -> str`**
- Extrae texto del campo system (str, lista de bloques, None)

**`_content_blocks_to_text(content) -> str`**
- Convierte content blocks Anthropic a texto plano
- Maneja: text, tool_result, tool_use, server_tool_use, thinking (strip), image, server_tool_result

**`_tool_result_content_to_str(content) -> str`**
- Normaliza contenido de tool_result (str, list, dict, None) a string

**`_convert_assistant_blocks(blocks) -> List[Dict]`**
- Convierte bloques assistant a formato OpenAI
- text -> content, tool_use -> tool_calls, thinking/redacted -> stripped

**`_convert_user_blocks(blocks) -> List[Dict]`**
- Convierte bloques user a formato OpenAI
- tool_result -> role:"tool" messages, text -> role:"user", server_tool_result -> text

**`_convert_message_blocks(msg) -> List[Dict]`**
- Router: despacha a `_convert_assistant_blocks` o `_convert_user_blocks`

**`convert_anthropic_to_litellm(request, model_context_window=0) -> Dict`**
- **Conversion principal Anthropic -> LiteLLM**
- Maneja: system, messages, tools (con cache), tool_choice, max_tokens cap
- Para no-tools models: inyecta XML prompt, reescribe historial
- Cap dinamico de max_tokens segun context window

**`convert_litellm_to_anthropic(response, request, model_context_window=0) -> MessagesResponse`**
- **Conversion principal LiteLLM -> Anthropic**
- Extrae: content, reasoning, tool_calls
- JSON repair para tool arguments malformados
- Para no-tools: extrae XML tool calls del texto
- Logica stop_reason: tool_use solo si JSON valido, max_tokens si corrupto

---

### 2.4 llm/schemas.py (163 lineas)

**Rol**: Modelos Pydantic para contratos API.

#### Clases

| Clase | Tipo | Campos principales |
|-------|------|-------------------|
| `ProviderConfig` | dataclass | name, provider_prefix, api_key, big_model, small_model, base_url, building_model, context_window. Metodo: `get_litellm_model(intent)` |
| `ContentBlockText` | BaseModel | type="text", text |
| `ContentBlockImage` | BaseModel | type="image", source |
| `ContentBlockToolUse` | BaseModel | type="tool_use", id, name, input |
| `ContentBlockToolResult` | BaseModel | type="tool_result", tool_use_id, content (Union), is_error |
| `ContentBlockThinking` | BaseModel | type="thinking", thinking, signature |
| `ContentBlockRedactedThinking` | BaseModel | type="redacted_thinking", data |
| `ContentBlockServerToolUse` | BaseModel | type="server_tool_use", id, name, input |
| `ContentBlockServerToolResult` | BaseModel | type="server_tool_result", tool_use_id, content |
| `SystemContent` | BaseModel | type="text", text |
| `Message` | BaseModel | role (user/assistant), content (str o Union de blocks) |
| `Tool` | BaseModel | name, description, input_schema |
| `ThinkingConfig` | BaseModel | enabled=True |
| `MessagesRequest` | BaseModel | model, max_tokens, messages, system, tools, etc. Validator: `preserve_original_model` |
| `TokenCountRequest` | BaseModel | model, messages, system, tools. Validator: `preserve_original_model_token` |
| `TokenCountResponse` | BaseModel | input_tokens |
| `Usage` | BaseModel | input_tokens, output_tokens, cache_creation/read |
| `MessagesResponse` | BaseModel | id, model, role, content, stop_reason, usage |

---

### 2.5 llm/compressor.py (245 lineas)

**Rol**: Compresion de contexto para modelos con ventana limitada.

#### Funciones

**`_count_message_tokens(messages, model="") -> int`**
- Cuenta tokens con litellm tokenizer, fallback chars/3

**`estimate_tools_tokens(tools) -> int`**
- Estima overhead de tokens de definiciones de tools (chars/4)

**`_serialize_messages_for_summary(messages, max_chars=50000) -> str`**
- Serializa mensajes para el compresor, trunca mensajes > 3000 chars

**`compress_messages_if_needed(...) -> tuple[list[dict], bool]`**
- **Funcion principal de compresion**
- Trigger: tokens > 85% de context_window
- Separa: system, old (antes de keep_recent), recent (ultimos 15)
- Intenta: compresion LLM -> fallback: trimming simple
- Retorna: (messages, was_compressed)

**`_llm_compress(old_messages, model, api_key, api_base) -> Optional[str]`**
- Llama al LLM compresor para resumir mensajes antiguos
- Usa prompt con reglas de preservacion (paths, tools, errors, code)
- Retorna resumen o None si falla

**`_reassemble_with_summary(system_msg, summary, recent) -> list[dict]`**
- Reensambla: [system] + [summary como user] + [ack assistant] + [recent]

**`_reassemble_trimmed(system_msg, recent) -> list[dict]`**
- Fallback: [system] + [notice truncation] + [ack] + [recent]

---

### 2.6 llm/streaming.py (557 lineas)

**Rol**: Conversion de streaming LiteLLM a SSE Anthropic.

#### Funciones

**`_close_json_brackets(text) -> str`**
- Calcula sufijo minimo para cerrar brackets/braces/strings abiertos en JSON
- Maneja escape de strings y anidamiento

**`_has_truncation_artifacts(json_str) -> bool`**
- Detecta si JSON reparado tiene artefactos de truncamiento
- Criterio: todas las strings de un objeto son vacias

**`_compute_repair_suffix(accumulated, tool_index) -> str | None`**
- Intenta reparar JSON truncado con 2 estrategias:
  1. `json_repair` library (solo si preserva prefijo)
  2. Cierre manual de brackets

**`_warn_empty_tool_values(name, input_dict) -> None`**
- Warnings de calidad para herramientas con valores vacios
- Checks especificos: TodoWrite (items vacios), Write (content vacio), Edit (strings vacios)

**`_emit_tool_use_block(name, input_dict, block_index) -> list[str]`**
- Genera eventos SSE para un bloque tool_use (spec Anthropic)
- Emite: content_block_start, content_block_delta (vacio + args), content_block_stop

**`handle_streaming(response_generator, original_request, model_context_window=0)`**
- **Funcion principal de streaming** (async generator)
- Maquina de estados que maneja:
  - Deltas de texto -> SSE text_delta
  - Deltas de tool_calls -> SSE tool_use blocks con JSON repair
  - reasoning_content -> buffered, emitido solo si no hay tool calls
  - XML tool simulation via XmlToolBuffer
  - Flush de buffer al final del stream
  - Logica de stop_reason (tool_use vs max_tokens vs end_turn)
  - Recovery de tool calls truncados via `recover_incomplete_tool_call`
  - Deteccion de tool calls en reasoning_content (deepseek-reasoner quirk)

---

### 2.7 llm/tool_prompting.py (893 lineas)

**Rol**: Simulacion XML de herramientas para modelos sin function calling nativo.

#### Regex Patterns (4)

| Pattern | Proposito |
|---------|----------|
| `_TOOL_CALL_RE` | Primary: `<tool_call name="X"><input>JSON</input></tool_call>` |
| `_TOOL_CALL_FALLBACK_RE` | Fallback: cualquier tag interno (`<textarea>`, etc.) |
| `_TOOL_CALL_BARE_RE` | Last-resort: JSON directo sin tags internos |
| `_PARTIAL_TOOL_RE` | Para recovery de XML truncado |

Todos usan `_NAME_ATTR = r"""name=["']([^"']+)["']"""` para aceptar comillas simples Y dobles.

#### Funciones

**`_build_valid_tool_names(tools) -> set[str]`**
- Extrae set de nombres validos de tools

**`validate_tool_name(name, valid_names) -> bool`**
- Valida nombre contra allowlist. True si no hay allowlist (backward compat)

**`_load_no_tools_models() -> FrozenSet[str]`** (cached)
- Carga `NO_TOOLS_MODELS` de env var, separado por comas

**`is_no_tools_model(model) -> bool`**
- Verifica si modelo requiere simulacion XML (pattern matching)

**`_format_schema_properties(input_schema, depth=0, max_depth=2) -> str`**
- Formatea propiedades de JSON schema recursivamente
- Incluye: tipo, requerido, descripcion, enum values, items de arrays

**`_build_tool_quick_reference(tools) -> str`**
- Referencia compacta: `TodoWrite(todos=[{content, status(pending/in_progress/completed), activeForm}])`

**`build_tool_prompt(tools) -> str`**
- **Construye prompt XML completo** con:
  - Formato exacto XML requerido
  - Reglas criticas (DOUBLE QUOTES, <input> tags, valid JSON)
  - Lista de nombres validos
  - Ejemplo generico
  - Quick reference de todos los tools
  - Secciones detalladas por tool

**`_merge_consecutive_messages(messages) -> list[dict]`**
- Fusiona mensajes consecutivos del mismo rol

**`rewrite_messages_without_tools(messages) -> list[dict]`**
- Reescribe historial para no-tools models:
  - tool_calls -> texto XML
  - role:"tool" -> user con `<tool_result>` XML
  - Fusiona mensajes consecutivos

**`_strip_inner_xml_tags(raw) -> str`**
- Elimina tags XML envolventes (`<input>...</input>` -> contenido)

**`_safe_parse_tool_input(raw_input, tool_name, tools=None) -> dict`**
- Parsea input JSON con 3 fallbacks: JSON directo -> json_repair -> wrap como raw
- Nunca lanza excepciones

**`extract_tool_calls_from_text(text, valid_tool_names=None, tools=None) -> tuple`**
- **Extractor principal de XML tool calls**
- 3 niveles de regex: primary -> fallback -> bare
- Filtra nombres alucinados contra valid_tool_names
- Retorna (tool_blocks, remaining_text)

**`_repair_tool_input(name, input_dict, tools) -> dict`**
- Reparacion guiada por schema: rewrap `{"value": X}` al campo correcto
- Estrategias: single required array, single required property

**`_get_tool_required_fields(tool_name, tools) -> set[str]`**
- Extrae campos requeridos del schema de un tool

**`recover_truncated_deterministic(partial_xml, tools) -> list[dict] | None`**
- Recuperacion deterministica (sin LLM) de XML truncado
- json_repair + validacion de campos requeridos + deteccion de truncamiento

**`recover_incomplete_tool_call(partial_xml, tools, model, api_key, ...) -> list[dict] | None`**
- Recuperacion en 2 pasos: deterministica -> LLM retry
- Desactivable via `DISABLE_TOOL_RECOVERY=1`

#### Clase XmlToolBuffer

**`XmlToolBuffer(valid_tool_names=None, tools=None)`**
- Maquina de estados para detectar `<tool_call>` XML en streaming

| Metodo | Descripcion |
|--------|-------------|
| `feed(text) -> list[dict]` | Alimenta chunk de texto, retorna segmentos ordenados |
| `flush() -> list[dict]` | Flush del buffer al final del stream |
| `_drain() -> list[dict]` | Procesa buffer y extrae segmentos completos |
| `_try_extract_text() -> dict | None` | Extrae texto antes de `<tool_call>` |
| `_try_extract_tool() -> dict | None` | Extrae bloque `</tool_call>` completo |
| `_is_backtick_quoted(idx) -> bool` | Detecta falsos positivos en codigo |
| `_parse_tool_xml(xml) -> dict` | Parsea XML completo con 3 niveles regex |
| `_safe_text_end() -> int` | Evita cortar `<tool_call` parcial |

Buffer overflow protection: `_MAX_TOOL_BUFFER = 16,000` chars.

---

### 2.8 router/llm_router.py (277 lineas)

**Rol**: Clasificacion de intencion y seleccion de modelos.

#### Regex Patterns (3)

| Pattern | Idiomas | Detecta |
|---------|---------|---------|
| `PLANNING_RE` | EN/ES | plan, design, architect, analyz, strategy, roadmap... |
| `BUILDING_RE` | EN/ES | implement, fix, refactor, test, deploy, docker... |
| `ANALYSIS_RE` | EN/ES | analyse code, audit, exhaustive, comprehensive, list features... |

#### Funciones

**`is_analysis_request(text) -> bool`**
- Detecta requests de analisis/auditoria usando `ANALYSIS_RE`

**`_regex_fallback_intent(text) -> str`**
- Clasificacion regex: BUILDING > PLANNING > CHAT
- Si ambos match: CHAT (ambiguo)

**`classify_intent(text, *, model, api_key, api_base, timeout_s) -> str`**
- Clasifica con LLM barato (trunca a 1000 chars, max_tokens=5, temp=0)
- Fallback a regex en error/timeout
- Trackea metricas: llm_success vs regex_fallback

**`content_to_rough_text(content) -> str`**
- Aplana content blocks Anthropic a texto para heuristicas de routing
- Maneja: text, tool_result (con nesting), tool_use, image

**`get_last_user_text(messages) -> str`**
- Busca ultimo mensaje user en reverse, extrae texto (max 8000 chars)

**`choose_local_model(...) -> str`**
- **Scoring determinista para Ollama/local**:
  - messages>10: +2 big
  - tokens>6000: +3 big
  - system>4000: +1 big
  - max_out>900: +2 build, +1 big
  - tools>0: +2 big
  - intent PLANNING: +3 big
  - intent BUILDING: +3 build
  - score_build>=3: building_model
  - score_big>=3: big_model
  - else: small_model

---

### 2.9 router/model_mapper.py (62 lineas)

**Rol**: Mapea aliases Claude a modelos reales del proveedor.

#### Funciones

**`has_provider_prefix(model) -> bool`**
- Verifica si tiene prefijo conocido (openai/, anthropic/, gemini/)

**`strip_provider_prefix(model) -> str`**
- Remueve prefijo de proveedor

**`_provider_prefix(preferred_provider) -> str`**
- Mapea: "google" -> "gemini/", "anthropic" -> "anthropic/", default -> "openai/"

**`map_claude_alias_to_target(model, *, preferred_provider, big_model, small_model) -> str`**
- **Mapeo principal**:
  - "haiku" en nombre -> small_model
  - "sonnet"/"opus" -> big_model
  - Ya con prefijo -> respeta
  - Sin alias -> prefijo + modelo tal cual

---

### 2.10 utils/utils.py (161 lineas)

**Rol**: Utilidades compartidas.

#### Funciones

**`parse_allowlist(raw) -> Set[str]`**
- Parsea allowlist: "" -> set(), "*" -> {"*"}, "a,b" -> {"a", "b"}

**`approx_tokens_from_bytes(b) -> int`**
- Estimacion rapida: len(bytes) // 6

**`scale_tokens(raw_count, model_context_window) -> int`**
- Escala tokens para que Claude Code (asume 200K) trigger correctamente en ventanas menores
- Formula: `raw * (200000 / model_context_window)`

**`ensure_system_note(request_obj, note, system_content_cls=None) -> None`**
- Inyecta nota en system con deduplicacion
- Soporta: str, lista de bloques, None

**`filter_tools_allowlist(tools, allow) -> tuple`**
- Filtra tools por allowlist. "*" permite todo
- Retorna (kept_tools, dropped_names)

**`normalize_tool_choice(tool_choice, kept_tools)`**
- Valida tool_choice contra tools filtradas
- Fallback a "auto" si tool especifica fue filtrada

**`_hash_content(messages, model, system=None) -> str`**
- SHA256 hash para cache de token counting

**`cached_token_count(messages, model, system=None) -> int | None`**
- Busca en cache (LRU manual, max 256 entries)

**`store_token_count(messages, model, count, system=None)`**
- Almacena en cache con eviction LRU

---

### 2.11 utils/metrics.py (97 lineas)

**Rol**: Observabilidad y tracking de requests.

#### Clases

**`RequestLog`** (dataclass)
- Campos: timestamp, intent, model_requested, model_used, provider, input_tokens, output_tokens, latency_ms, is_fallback, is_stream, is_analysis, error

**`ProxyMetrics`** (thread-safe con Lock)
- Contadores: total_requests, total_errors, total_fallbacks, total_input/output_tokens
- Por proveedor: counts, errors, latency_sum
- Por intent: counts
- Cache: hits, misses
- Retries: total, successes
- Classifier: llm_success, regex_fallback
- Analysis: enforcements

| Metodo | Retorna | Descripcion |
|--------|---------|-------------|
| `record(log)` | None | Registra request, actualiza todos los contadores |
| `get_stats()` | dict | Stats agregadas con avg_latency por proveedor |
| `get_recent(n=50)` | list[dict] | Ultimos N logs como dicts |

**Singleton**: `metrics = ProxyMetrics()`

---

## 3. Algoritmos Clave

### 3.1 Simulacion XML de Herramientas

**Problema**: Modelos como deepseek-reasoner no tienen function calling nativo.
**Solucion**: Pipeline completo REQUEST -> RESPONSE.

**Request path**:
1. Detecta modelo no-tools via `NO_TOOLS_MODELS` env var
2. Construye prompt XML con `build_tool_prompt()` (definiciones + reglas + ejemplos)
3. Inyecta prompt en system message
4. Reescribe historial: tool_calls -> XML text, tool results -> XML text
5. Fusiona mensajes consecutivos

**Response path** (non-streaming):
1. `extract_tool_calls_from_text()` con 3 niveles de regex
2. Filtra nombres alucinados contra allowlist
3. Suprime reasoning_content cuando hay tool calls (previene crash de CC)

**Response path** (streaming):
1. `XmlToolBuffer` state machine procesa chunks incrementalmente
2. Detecta `<tool_call` tags, acumula hasta `</tool_call>`
3. Parsea XML completo con 3 niveles regex
4. Al final: flush + recovery de truncados (deterministic -> LLM)

### 3.2 Cadena de Fallback Multi-Proveedor

```
Primary (con retry exponencial: 1s,2s,4s,8s,16s)
  -> FALLA
Fallback_1 (con retry)
  -> FALLA
Fallback_2 (con retry)
  -> ...
Fallback_9
  -> FALLA: "All providers failed"
```

Clasificacion de errores:
- **Retryable**: RateLimit, Timeout, Connection, ServiceUnavailable, InternalServer
- **No retryable**: ContextWindowExceeded, BadRequest (no se reintenta, mismo payload siempre falla)

### 3.3 Clasificacion de Intencion

```
Input: ultimo mensaje del usuario (max 1000 chars)
  |
  v
CLASSIFIER_MODEL configurado?
  |-- SI: LLM classify (cheap model, 5s timeout)
  |     |-- Respuesta valida: PLANNING/BUILDING/CHAT
  |     |-- Error/timeout: -> regex fallback
  |-- NO: -> regex fallback
  |
  v
Post-processing:
  - tools >= TOOL_UPGRADE_THRESHOLD? -> override a BUILDING
  - ANALYSIS_ENFORCEMENT=1 + ANALYSIS_RE match? -> inyecta tool enforcement
```

### 3.4 Compresion de Contexto

```
Tokens estimados > 85% context_window?
  |-- NO: sin compresion
  |-- SI:
      Separar: [system] + [old] + [recent=15]
      old < 3 msgs? -> sin compresion
      |
      v
      LLM compress (cheap model):
        - Prompt con reglas de preservacion
        - Max 2048 tokens de resumen
        |
        v
      Exito? -> [system] + [summary] + [ack] + [recent]
      Fallo? -> [system] + [truncation notice] + [ack] + [recent]
```

### 3.5 Reparacion JSON en Streaming

```
Tool arguments incompletos (finish_reason=length)?
  |
  v
Estrategia 1: json_repair library
  - Solo si preserva prefijo exacto (no modifica medio)
  - Valida resultado
  |
  v
Estrategia 2: Cierre manual de brackets
  - Calcula sufijo minimo para cerrar {, [, "
  - Valida resultado
  |
  v
Post-validacion:
  - Repaired + length + all-empty-strings? -> artefacto de truncamiento -> INVALIDO
  - JSON valido? -> tool_use con stop_reason="tool_use"
  - JSON invalido? -> stop_reason="max_tokens" (CC no ejecuta basura)
```

### 3.6 Enforcement de Uso de Herramientas

```
ANALYSIS_ENFORCEMENT=1?
  |-- NO: nada
  |-- SI:
      is_analysis_request(last_text)? (regex EN/ES)
        |-- NO: nada
        |-- SI:
            Construye prompt dinamico:
              "You have N tools: [names]"
              "You MUST use these tools"
              "Do NOT answer from memory"
            Inyecta en system message
```

---

## 4. Correcciones Criticas Aplicadas

### 4.1 Thinking Blocks (fix 422)
- **Problema**: Claude Code envia bloques `thinking` y `redacted_thinking` que LiteLLM no entiende
- **Fix**: Modelos `ContentBlockThinking`, `ContentBlockRedactedThinking`, `ContentBlockServerToolUse`, `ContentBlockServerToolResult` en `schemas.py`. Bloques thinking se stripean en `converters.py:_convert_assistant_blocks()`

### 4.2 Comillas Simples en XML (fix tool execution)
- **Problema**: deepseek-reasoner genera `<tool_call name='X'>` con comillas simples + Python dict syntax
- **Fix**: `_NAME_ATTR = r"""name=["']([^"']+)["']"""` en todas las regex (primary, fallback, bare, partial). `json_repair` maneja Python dict -> JSON

### 4.3 Token Counting (precision)
- **Problema**: Heuristica chars/4 era imprecisa
- **Fix**: `litellm.token_counter()` (local, determinista) con fallback chars/3 en `compressor.py`

### 4.4 Bare Regex Fallback (3er nivel)
- **Problema**: Algunos modelos generan `<tool_call name="X">{"key":"val"}</tool_call>` sin tags internos
- **Fix**: `_TOOL_CALL_BARE_RE` como tercer nivel de fallback

---

## 5. Estructura de Tests

```
tests/
  conftest.py        - Fixtures pytest
  test_server.py     - Tests de endpoints
  test_router.py     - Tests de clasificacion e intent
  test_converters.py - Tests de conversion de formatos
  test_metrics.py    - Tests de metricas
  test_fallback.py   - Tests de cadena de fallback
  test_utils.py      - Tests de utilidades
```

---

## 6. Dependencias Clave

| Paquete | Uso |
|---------|-----|
| `fastapi` | Framework web |
| `litellm` | Abstraccion multi-proveedor LLM |
| `pydantic` | Validacion request/response |
| `json-repair` | Reparacion JSON malformado |
| `python-dotenv` | Variables de entorno |
| `uvicorn` | Servidor ASGI |
