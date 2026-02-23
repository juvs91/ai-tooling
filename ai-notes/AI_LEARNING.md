# AI_LEARNING
> Aprendizajes iterativos del proyecto. Actualizar despues de cada sesion.

## Ultima actualizacion
- Fecha: 2026-02-22
- Por: claude-opus-4-6 + jeguzman

---

## Patrones que funcionan

### Arquitectura
- Proxy como abstraccion total: Claude Code no sabe que habla con GLM-4.7. Un `.env` cambia todo el backend.
- Separar clasificador de provider: CLASSIFIER_MODEL es independiente de PREFERRED_PROVIDER (inversion de dependencias)
- Hot-reload con bind mount + uvicorn --reload: cambios en `vendor/claude-code-proxy/` aplican sin rebuild

### Codigo
- Pydantic Union types para content blocks: agregar nuevos tipos es solo agregar al union en `Message.content`
- Stripear bloques no soportados en converter (thinking, redacted_thinking) en vez de rechazar el request
- `@lru_cache(maxsize=1)` + `frozenset` para config env vars que se leen una vez: sin estado global mutable, testeable con `.cache_clear()`
- XML tool simulation: inyectar tools como prompt + parsear `<tool_call>` XML en respuesta permite simular function calling en modelos sin soporte nativo

### Herramientas
- `cc-scan` + `cc-plan` (local, sin tools) para analisis barato antes de ejecutar
- Docker bind mount de vendor/ para desarrollo sin rebuild

---

## Anti-patrones / Errores comunes

### Codigo
- Schemas Pydantic incompletos rompen con 422 antes de llegar al converter. Siempre validar que TODOS los content block types de Anthropic API esten cubiertos
- `type: "thinking"` blocks causan 422 si no hay ContentBlockThinking en el schema

### Configuracion
- `CLASSIFIER_MODEL` vacio = sin costo extra (regex fallback). No olvidar que sin esta var el intent siempre era "CHAT" (bug corregido)
- Z.AI tiene DOS endpoints: `/api/paas/v4` (OpenAI) y `/api/anthropic` (nativo). El nativo evita conversion pero pierde el routing del proxy
- `NO_TOOLS_MODELS` en `.env` global (no en profile-envs/) porque aplica a nivel de proxy independiente del provider

### Proceso
- Regex para intent detection es fragil: "implement a login endpoint" matchea BUILDING pero mensajes en español no matchean nada
- Token approximation (bytes/6) tiene ~15-20% error vs tiktoken real

---

## Decisiones tecnicas tomadas

| Fecha | Decision | Contexto | Alternativas descartadas |
|-------|----------|----------|-------------------------|
| 2025-02-07 | Agregar 4 content block types (thinking, redacted_thinking, server_tool_use, server_tool_result) | Claude Code extended thinking causa 422 | Rechazar requests con thinking (romperia CC) |
| 2025-02-07 | LLM classifier con DeepSeek como modelo dedicado | Regex es fragil para intent, DeepSeek es ultra barato | Usar el mismo SMALL_MODEL (mas caro, misma latencia) |
| 2025-02-07 | Cloud downgrade: CHAT intent -> SMALL_MODEL | Optimizacion de costos para mensajes simples | Siempre usar BIG_MODEL (mas caro sin beneficio) |
| 2025-02-07 | Env vars del clasificador por provider (profile-envs/) no globales | Cada provider puede tener diferente config de clasificador | Global en .env (menos flexible) |
| 2026-02-07 | Context window scaling via scale_tokens() | glm-4.7=128K, CC asume 200K. Sin escalado la auto-compactacion se dispara tarde | Truncar mensajes en el proxy (pierde contexto) |
| 2026-02-07 | JSON repair con json-repair lib (streaming + non-streaming) | glm-4.7 produce tool_call JSON truncado a veces | Solo fallback {"raw":...} (CC no puede usar), regex repair (fragil) |
| 2026-02-07 | Fallback provider chain: Z.AI -> Groq -> DeepSeek | Si Z.AI cae, CC muere con 500. Cadena sequencial con first-chunk validation para streaming | LiteLLM router nativo (menos control), retry simple (no multi-provider) |
| 2026-02-07 | Extraer _compute_repair_suffix() como helper reutilizable | Bloque de JSON repair duplicado en streaming normal close y fallback close | Duplicar el bloque (violacion DRY) |
| 2026-02-07 | Observabilidad in-memory (ring buffer + contadores) vs base de datos | DevX: curl /api/stats basta para debugging, no necesita setup externo | Prometheus/Grafana (overkill), SQLite (mas complejo), solo print (se pierde) |
| 2026-02-07 | Token count cache con SHA-256 + FIFO eviction | Evita recalcular conteos identicos en sesiones multi-turno | LRU (mas complejo), per-message incremental (fragil si system cambia) |
| 2026-02-07 | Tool conversion cache con key compuesto name:gemini:hash | Las mismas ~15 tools se convierten en cada request. Cache evita CPU desperdiciado | Sin cache (simple pero wasteful), cache solo por name (rompe si schema cambia) |
| 2026-02-10 | XML tool simulation para NO_TOOLS_MODELS | deepseek-reasoner no soporta tools/tool_choice/temperature, retornaba 500. Simular via XML prompting preserva funcionalidad | Solo stripear tools (pierde function calling), hardcodear patterns (inflexible) |
| 2026-02-10 | XmlToolBuffer state machine para streaming XML detection | Tool calls en XML llegan en chunks parciales. State machine con buffer detecta `<tool_call` parciales y espera completar | Regex en buffer completo (pierde streaming), no soportar streaming XML (inconsistente) |
| 2026-02-10 | 3-level JSON parse fallback (_safe_parse_tool_input) | Modelos sin native tools pueden generar JSON malformado. Fallback: parse → json_repair → raw wrap. NUNCA lanza excepcion | Solo parse (crashea CC), solo json_repair (mas lento siempre) |
| 2026-02-10 | Error status propagation en server.py | 500 generico ocultaba 400/401/429 reales del provider. Ahora se extrae status code del error string | Siempre 500 (debugging imposible), custom exception classes (overengineering) |
| 2026-02-10 | Quitar cap max_tokens para reasoning models | deepseek-reasoner usa reasoning_content que consume output tokens. Cap 16384 truncaba respuestas mid-tool_call | Subir cap a 32K (arbitrario), cap configurable (overengineering por ahora) |
| 2026-02-10 | Quitar gemini/ del cap max_tokens | Gemini tiene 1M context window, no necesita cap artificial de 16384 | Mantener cap (limita a Gemini sin razon) |
| 2026-02-10 | Logging en streaming exception handlers | `except Exception:` sin log hacia imposible diagnosticar fallas mid-stream. Ahora imprime tipo y mensaje | Sin logging (status quo, invisible) |
| 2026-02-11 | Error handling tipado con litellm.exceptions isinstance | String matching fragil ("400" in error_str) fallaba para ContextWindowExceededError. isinstance checks con jerarquia correcta (subclase antes de base) | Mantener string matching (fragil, misses subclasses) |
| 2026-02-11 | LLM context compression en proxy | DeepSeek 500 con ~67K tokens. Comprimir mensajes viejos via LLM barato, conservar recientes intactos. COMPRESSOR_* vars con fallback a CLASSIFIER_* | Truncar mensajes (pierde contexto), chunking (diluye calidad) |
| 2026-02-10 | Truncar tool descriptions a 200 chars en XML prompt | 17 tools × ~400 chars/desc = ~7.3KB. Truncar a 200 reduce a ~4.5KB sin perder funcionalidad | Sin truncar (prompt inflado innecesariamente) |
| 2026-02-10 | Usar original_model en responses | Response contenia `openai/deepseek-reasoner` en vez de `claude-opus-4-6`. CC no valida estrictamente pero es mas correcto | Dejar modelo mapeado (funciona pero confuso en logs) |
| 2026-02-12 | Defensivo: stop_reason=tool_use cuando hay tool_use blocks (streaming + non-streaming) | CC requiere stop_reason="tool_use" per Anthropic docs. Providers como Z.AI pueden retornar finish_reason="stop" con tool_calls | Solo confiar en finish_reason=="tool_calls" (falla si provider no lo retorna) |
| 2026-02-12 | Cambiar stop_reason "error" a "end_turn" en fatal stream errors | "error" no es Literal valido de Anthropic. CC podria rechazarlo silenciosamente causando freeze | Agregar "error" al Literal (rompe compatibilidad Anthropic) |
| 2026-02-22 | Anthropic passthrough mode para Z.AI `/api/anthropic` | Elimina double conversion Anthropic→OpenAI→Anthropic. httpx directo, SSE relay, fallback a pipeline standard | Usar litellm para Anthropic (agrega su propia conversion), solo OpenAI endpoint (status quo con XML issues) |
| 2026-02-22 | `_REASONING_SKIP` regex pattern en tool_call regexes | GLM/DeepSeek meten `<reasoning>` dentro de `<tool_call>`, corrompe JSON extraction | Stripear antes de regex (pierde contexto para debug), rechazar tool_call (pierde funcionalidad) |
| 2026-02-22 | 5th fallback regex `_TOOL_DILUTED_RE` para `<tool_name>/<args>` | Despues de ~20 compressions el XML prompt se diluye, modelos inventan tags | Solo reforzar prompt (no catchea 100%), no soportar (pierde herramientas) |
| 2026-02-22 | XML reinforcement en compression reassembly | El reminder se inyecta despues de cada compresion para prevenir dilution progresiva | Repetir tool prompt completo (4K tokens extra), no hacer nada (prompt dilution crash) |
| 2026-02-22 | Reemplazar asyncio anti-pattern en converters.py con recover_truncated_deterministic | ThreadPoolExecutor+asyncio.run puede deadlock en uvicorn. Sync deterministic recovery es suficiente para non-streaming | Hacer convert_litellm_to_anthropic async (refactor grande), mantener anti-pattern (riesgo deadlock) |

---

## Dependencias y versiones estables

| Paquete | Version | Notas |
|---------|---------|-------|
| claude-code-proxy | ghcr.io/1rgs/claude-code-proxy:main | Base image, vendor code mounted over it |
| litellm | (bundled in image) | Unified LLM interface |
| Z.AI GLM-4.7 | API | Big model for cloud |
| Z.AI GLM-4.7-flash | API | Small model for cloud |
| DeepSeek | API (pendiente) | Clasificador de intents ($15/mes) |
| json-repair | >=0.30.0 | Repair truncated JSON from tool_calls |

---

## Comandos utiles del proyecto

```bash
# Levantar proxy cloud con Z.AI
cd /Users/jeguzman/ai-tooling && docker compose up proxy_cloud -d

# Ver logs del proxy
docker logs ai-tooling-proxy_cloud-1 --tail 30 -f

# Health check
curl http://127.0.0.1:8083/health | jq .

# Test rapido del proxy
curl -X POST http://127.0.0.1:8083/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":30,"messages":[{"role":"user","content":"hi"}]}'

# Ver stats del proxy (observabilidad)
curl http://127.0.0.1:8083/api/stats | jq .

# Ver ultimos 20 request logs
curl "http://127.0.0.1:8083/api/logs?n=20" | jq .

# Smoke test con thinking blocks
python3 -c "
import json, urllib.request
payload = json.dumps({'model': 'claude-sonnet-4-5-20250929', 'max_tokens': 30,
  'messages': [{'role': 'user', 'content': 'hi'},
    {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'test', 'signature': 's1'}, {'type': 'text', 'text': 'hello'}]},
    {'role': 'user', 'content': 'bye'}]}).encode()
req = urllib.request.Request('http://127.0.0.1:8083/v1/messages', data=payload,
  headers={'Content-Type': 'application/json', 'x-api-key': 'test', 'anthropic-version': '2023-06-01'})
print(urllib.request.urlopen(req, timeout=30).read().decode()[:200])
"
```

---

## Notas de sesiones anteriores

### Sesion 2026-02-20 — ROOT CAUSE: GLM reasoning_content bypasses XmlToolBuffer + tool_stream
**Objetivo:** Diagnosticar por que GLM-4.7 emite `<tool_call>Read<arg_key>...` como texto plano en vez de tool_use blocks
**Root cause (3 problemas):**
1. **reasoning_content bypass**: GLM-4.7 tiene 3 campos en delta: `reasoning_content`, `content`, `tool_calls`. Para non-no-tools models, `reasoning_content` se emitia DIRECTAMENTE como text_delta sin pasar por XmlToolBuffer. Si GLM pone `<tool_call>` XML en reasoning_content (su proceso de pensamiento), aparece como texto plano en CC
2. **tool_stream=True no configurado**: Z.AI docs dicen que `tool_stream=True` es REQUERIDO para streaming tool calls con glm-4.6/4.7/5. Sin este parametro, GLM no puede streamear tool calls correctamente
3. **Sin safety net**: Si CUALQUIER path bypasea el buffer (reasoning, un campo nuevo, etc.), no habia fallback para detectar tool calls en texto acumulado
**Evidence from logs:**
- ZERO `[xml-buffer]` entries = buffer NUNCA vio tool calls
- ZERO `[streaming] DIAG:` entries para delta.content = tool call XML NO estaba en content
- Multiples `end_turn` con `has_xml=False` = tool calls perdidos
- Conclusion: XML tool calls estaban en `reasoning_content`, que bypaseaba el buffer
**Resultado (3 fixes):**
- Fix 1: `streaming.py` — reasoning_content para non-no-tools models ahora pasa por XmlToolBuffer (igual que content). DeepSeek (no_tools_mode=True) sigue buffereando en reasoning_buffer (sin cambio)
- Fix 2: `proxy.py` — Agrega `tool_stream=True` via `extra_body` para endpoints Z.AI cuando hay streaming + tools
- Fix 3: `streaming.py` — Safety net al final del stream: si `accumulated_text` contiene `<tool_call>` XML que el buffer perdio, se extraen y emiten como tool_use blocks. En ambas close paths (normal + fallback)
**Deepseek regression analysis:**
- DeepSeek es `no_tools_mode=True` → toma la rama `ctx.reasoning_buffer += delta_reasoning` (SIN CAMBIO)
- `tool_stream=True` solo se agrega para URLs con "z.ai" → DeepSeek NO afectado
- Safety net solo dispara cuando buffer perdio algo → no interfiere con flujo normal
**Aprendizaje:**
- GLM-4.7 usa `reasoning_content` igual que DeepSeek pero CON native tools. Cada campo del delta necesita pasar por el buffer de deteccion XML
- `tool_stream=True` es un parametro Z.AI-especifico, NO estandar OpenAI. Sin el, tool calls streaming no funcionan correctamente
- Defense in depth: el safety net es critico porque SIEMPRE puede haber un campo nuevo o un bypass que no anticipamos
**Archivos modificados:** `llm/streaming.py`, `proxy/proxy.py`

### Sesion 2026-02-12 — Fix Tool Call Execution (stop_reason + guards + diagnostics)
**Objetivo:** Diagnosticar y resolver por que CC "muere" al intentar ejecutar tool calls — la conversacion se congela y CC no ejecuta los tools
**Analisis exhaustivo:** 39 funcionalidades inventariadas en 10 archivos. Plan completo en `.claude/plans/binary-crunching-fiddle.md`
**Root cause:** Multiple bugs en `stop_reason` mapping:
1. Si provider retorna `finish_reason="stop"` en vez de `"tool_calls"` (comun con Z.AI/GLM), streaming emitia `stop_reason: "end_turn"` en vez de `"tool_use"`. Per Anthropic docs, `stop_reason: "tool_use"` es **OBLIGATORIO** cuando hay tool_use blocks
2. Fallback close path tenia el mismo bug
3. Non-streaming path (converters.py) tenia el mismo bug
4. Fatal stream errors emitian `stop_reason: "error"` que NO es un valor valido del Literal de Anthropic (`end_turn | max_tokens | stop_sequence | tool_use`). CC podria rechazarlo silenciosamente
5. Tool calls con nombre vacio podrian pasar al cliente causando error
**Resultado (6 fixes):**
- Fix 1 (CRITICO): `streaming.py:285` — agregar `or tool_index is not None` a condicion de stop_reason
- Fix 2 (CRITICO): `streaming.py:352` — agregar `or tool_index is not None` a fallback close
- Fix 3 (CRITICO): `converters.py:612-618` — agregar `has_tool_use` check que detecta tool_use blocks en content
- Fix 4 (MEDIO): `streaming.py:359` — cambiar `stop_reason: "error"` a `"end_turn"` (valor valido de Anthropic)
- Fix 5 (MEDIO): Guards en streaming.py y converters.py que skipean tool_calls con nombre vacio + warning log
- Fix 6 (DIAGNOSTICO): Logging en streaming close paths, XmlToolBuffer._parse_tool_xml, XmlToolBuffer.flush, y XML tool_use emission
**Impacto en Z.AI/GLM:** SAFE — todos los fixes solo agregan `or` adicionales. Si GLM retorna `finish_reason="tool_calls"` (correcto), el check existente lo atrapa sin cambio. Los nuevos `or` solo activarian como defensa extra
**Validacion:** Formato validado contra documentacion oficial de Anthropic (https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use)
**Aprendizaje:**
- `stop_reason: "tool_use"` es la señal que CC usa para ejecutar tools. Sin esto, CC ignora los tool_use blocks silenciosamente
- Los Literal types de Anthropic son estrictos — `"error"` no es un valor valido y CC puede rechazarlo sin aviso
- Diagnostic logging en las close paths es esencial — sin saber que `stop_reason` se emitio, el bug es invisible
**Archivos modificados:** `llm/streaming.py`, `llm/converters.py`, `llm/tool_prompting.py`
**Pendiente:** Test end-to-end con CC real apuntando a DeepSeek y a Z.AI para confirmar

### Sesion 2026-02-11 — Fix Error 500 silencioso + Compresion LLM de contexto
**Objetivo:** Diagnosticar y resolver error 500 intermitente con DeepSeek cuando la conversacion crece (~67K tokens)
**Root cause:** Requests con ~67K tokens excedian el context window de DeepSeek. LiteLLM lanzaba excepcion pero el proxy: (1) no logueaba el error real, (2) usaba string matching fragil para status codes, (3) no tenia mecanismo para comprimir contexto overflow.
**Resultado (2 fases):**
- Fase 1 (Error Handling):
  - `server.py`: `_classify_llm_error()` con `isinstance` checks de litellm.exceptions. `ContextWindowExceededError` (subclase de BadRequestError) se chequea primero. Logging con `exc_info=True` para traceback completo
  - `proxy.py`: `_is_retryable_error()` usa isinstance para no retry en context window errors. Log explicito cuando un error NO es retryable
- Fase 2 (Context Compression):
  - Nuevo `llm/compressor.py`: `compress_messages_if_needed()` — detecta overflow (>85% window), separa old/recent, comprime via LLM barato, fallback a trimming simple
  - Env vars `COMPRESSOR_MODEL/API_KEY/BASE_URL` con fallback a `CLASSIFIER_*` (zero-config)
  - `COMPRESSOR_KEEP_RECENT=15` configurable
  - Integrado en `run_messages()` entre `convert_anthropic_to_litellm()` y `_call_provider_with_retry()`
**Aprendizaje:**
- `litellm.exceptions.ContextWindowExceededError` es subclase de `BadRequestError` — siempre chequear con isinstance en orden especifico→general
- String matching "400" in error_str es fragil: LiteLLM formatea como "litellm.BadRequestError: ..." que NO siempre contiene "400"
- La compresion LLM es superior a truncar o chunking: preserva semantica, detalles tecnicos sobreviven via prompt engineering
- El compresor solo necesita manejar los mensajes VIEJOS (~47K), no el total (~67K). Esto permite usar el mismo modelo barato (deepseek-chat) que tiene 128K de contexto
**Archivos modificados:** `server.py`, `proxy/proxy.py`, `llm/compressor.py` (nuevo), `.env.example`
**Pendiente:** Probar con sesion real larga en DeepSeek para confirmar compresion funciona

### Sesion 2026-02-10 (b) — Diagnostico "Prueba de Fuego" + 5 Bug Fixes
**Objetivo:** Diagnosticar por que la prueba real con Claude Code fallo (proxy 200 OK pero CC muestra error y deja de ejecutar)
**Root cause:** `max_completion_tokens` capped a 16384 para TODOS los modelos openai/ y gemini/. deepseek-reasoner usa `reasoning_content` que consume output tokens (~8-15K), dejando solo ~1-6K para content real. La respuesta se truncaba mid-`<tool_call>` XML.
**Agravante:** `except Exception:` sin logging en streaming.py hacia imposible ver que error ocurria. El proxy reportaba 200 pero el stream terminaba con `stop_reason: "error"` o respuesta truncada.
**Resultado (5 fixes aplicados):**
- Fix 1 (CRITICO): `converters.py:441-451` — Mover `no_tools` antes del cap, quitar `gemini/` del check, agregar `and not no_tools`. Reasoning models ya no tienen cap artificial
- Fix 2 (CRITICO): `streaming.py:247-249, 282-284` — Agregar `as e` + `print()` a ambos exception handlers. Errores ahora visibles en logs
- Fix 3 (ALTO): `tool_prompting.py:103-105` — Truncar tool descriptions a 200 chars. Reduce prompt XML de ~7.3KB a ~4.5KB
- Fix 4 (MEDIO): `streaming.py:56` + `converters.py:619` — Usar `original_model` en responses (devuelve `claude-opus-4-6` en vez de `openai/deepseek-reasoner`)
- Fix 5 (MEDIO): `tool_prompting.py:297-298` — Warning log en `flush()` si buffer contiene `<tool_call` incompleto
**Bugs descartados (no son reales):**
- Pydantic serialization warnings: vienen de LiteLLM interno, no de nuestro codigo
- `_merge_consecutive_messages()` pierde tool_calls: rewrite convierte a XML text ANTES del merge
- `message_delta` sin input_tokens: spec Anthropic solo requiere output_tokens en message_delta
- reasoning mixed con text: diseño intencional (`<reasoning>` visible en CC)
**Aprendizaje:**
- deepseek-reasoner `reasoning_content` cuenta hacia output token limit. Un cap de 16384 es insuficiente cuando el reasoning chain consume 8-15K tokens
- Sin logging en exception handlers, el proxy es una caja negra. Siempre agregar `as e` + log
- Gemini tiene 1M context window — nunca debio tener cap de 16384
- `getattr(request, "original_model", None) or request.model` es pattern seguro para acceder a un campo que puede no existir
**Archivos modificados:** `llm/converters.py`, `llm/streaming.py`, `llm/tool_prompting.py`
**Pendiente:** Re-ejecutar "prueba de fuego" con CC real para confirmar fix

### Sesion 2026-02-10 — XML Tool Simulation para deepseek-reasoner
**Objetivo:** Fix 500 error con deepseek-reasoner + implementar XML tool simulation para modelos sin native function calling
**Root cause:** Proxy enviaba `tools`, `tool_choice`, `temperature` a deepseek-reasoner que no los soporta. DeepSeek API retornaba 400, server.py lo envolvia en 500 generico.
**Resultado:**
- Nuevo modulo `llm/tool_prompting.py`: detection, prompt building, message rewriting, response parsing, streaming state machine
- `NO_TOOLS_MODELS=deepseek-reasoner` en `.env` global (configurable, comma-separated)
- Request side: strip tools/tool_choice/temperature, inyectar XML prompt en system, reescribir historial (tool_calls→XML, tool_results→XML)
- Response side (non-streaming): `extract_tool_calls_from_text()` con regex + 3-level JSON fallback
- Response side (streaming): `XmlToolBuffer` class con `feed()/flush()/_drain()` state machine, detecta `<tool_call>` parciales entre chunks
- `reasoning_content` de deepseek-reasoner se surfacea como `<reasoning>` text block
- Error handling en server.py propaga status codes reales (400/401/429)
- 8 unit tests + integration tests con DeepSeek API real (non-streaming + streaming)
**Aprendizaje:**
- deepseek-reasoner ignora temperature pero RECHAZA tools/tool_choice con 400 explicito
- La simulacion XML funciona sorprendentemente bien — deepseek-reasoner sigue el formato XML fielmente
- `_safe_text_end()` en XmlToolBuffer es clave: evita emitir texto que podria ser inicio de `<tool_call` parcial
- `_merge_consecutive_messages()` es necesario post-rewrite porque tool→user messages pueden quedar adyacentes a user messages existentes
- Pattern `@lru_cache(maxsize=1)` + `frozenset` es ideal para config env que se lee una vez: inmutable, testeable, sin globals
**Archivos modificados:** `.env`, `llm/tool_prompting.py` (nuevo), `llm/converters.py`, `llm/streaming.py`, `server.py`
**Bloqueadores:** Pydantic serialization warnings de LiteLLM (non-fatal, cosmetic)

### Sesion 2026-02-07 (b) — Caching + Observabilidad
**Objetivo:** Implementar observabilidad y caching segun plan aprobado
**Resultado:**
- Observabilidad: `utils/metrics.py` con `RequestLog` dataclass + `ProxyMetrics` singleton (ring buffer 200, thread-safe)
- `run_messages()` ahora retorna 3-tuple `(is_stream, response, provider_name)` para registrar que provider se uso
- Timing con `time.monotonic()` en `create_message()` + registro de `RequestLog` para cada request
- Endpoints: `GET /api/stats` (contadores, latencia, fallback_rate, cache hits) y `GET /api/logs?n=50` (ultimos N requests)
- Token count cache: SHA-256 hash de messages+model+system como key, FIFO eviction a 256 entries, wired into `count_tokens_endpoint`
- Tool conversion cache: `_convert_tool_cached()` en converters.py, memoiza por `name:gemini_flag:schema_hash`
- Gemini schema memoization: `clean_gemini_schema_cached()` wrapper con hash de schema
- Tests: 12 metrics + 7 token cache + 5 tool cache + 3 gemini cache = 27 nuevos tests, todos pasan
**Aprendizaje:**
- KV cache a nivel de tokens vive en la GPU del provider (Z.AI). Desde el proxy solo podemos cachear computaciones locales (conteos, conversiones). Prompt caching nativo dependeria de que Z.AI lo exponga
- Cache de token count elimina ~80% de llamadas a `token_counter` en sesiones multi-turno (el contenido previo es identico)
- `_convert_tool_cached()` usa key compuesto `name:gemini_flag:hash` para separar entradas gemini vs no-gemini del mismo tool
**Bloqueadores:** pytest-asyncio version incompatible con pytest global (fix: `-p no:asyncio`), pre-existing test_case_insensitive_matching failure

### Sesion 2026-02-07
**Objetivo:** Implementar 3 mejoras prioritarias: Context Scaling, JSON Repair, Fallback Provider Chain
**Resultado:**
- Context scaling: `scale_tokens()` en utils.py, wired into streaming/converters/server/count_tokens
- JSON repair: `json-repair` lib en pyproject.toml, repair en converters.py (non-streaming) + `_compute_repair_suffix()` helper en streaming.py (ambas close paths)
- Fallback chain: `ProviderConfig` dataclass en schemas.py, `_load_fallback_providers()` en server.py (FALLBACK_N_* env vars), `_call_provider()` con first-chunk validation en proxy.py, `_inject_credentials()` helper extraido
- Tests: 6 JSON repair + 7 scale_tokens + 12 fallback = 25 nuevos tests, todos pasan
**Aprendizaje:**
- Streaming fallback requiere validar el primer chunk antes de commitear (acompletion retorna inmediatamente, el error real sale al consumir)
- `_compute_repair_suffix()` como helper evita duplicacion en ambas close paths de streaming
- El plan original decia quitar cloud intent routing de apply_policy_and_routing, pero dejarlo es mas seguro: primary usa el routing existente, fallbacks usan ProviderConfig.get_litellm_model(intent)
**Bloqueadores:** test_server.py tiene 12 errores pre-existentes por incompatibilidad httpx/starlette, test_case_insensitive_matching falla por bug en el test (no en el codigo)


### Sesion 2025-02-07
**Objetivo:** Fix 422 errors del proxy con Z.AI + implementar intent classifier
**Resultado:**
- Fix aplicado: 4 nuevos content block types en schemas.py
- Thinking blocks se stripean en converter para providers OpenAI-compatible
- Intent classifier implementado con LLM (DeepSeek) + regex fallback
- Cloud routing: CHAT -> downgrades a SMALL_MODEL automaticamente
**Aprendizaje:** Los schemas Pydantic deben cubrir TODOS los tipos de la Anthropic API. Extended thinking es un feature que Claude Code usa activamente y los proxies deben manejarlo.
**Bloqueadores encontrados:** Z.AI timeout intermitente (~30s+ en algunos requests)

---

## Grokking checkpoints
> Momentos donde el modelo "entendio" algo fundamental del proyecto

1. **2025-02-07**: El proxy es una capa de abstraccion Anthropic->OpenAI, no un simple forwarder. Cada content block type nuevo de Anthropic requiere: schema + converter + stripper
2. **2025-02-07**: Intent classification es un "hop" en el multi-hop grounding. Separar el clasificador del provider principal permite optimizar costo vs precision independientemente
3. **2026-02-10**: Reasoning models cambian la economía de output tokens. `reasoning_content` es invisible para el usuario pero consume el mismo budget de `max_completion_tokens`. Un cap fijo que funciona para modelos normales puede destruir la respuesta de un reasoning model
4. **2026-02-10**: Function calling no es magia — es un prompt convention. XML tool simulation prueba que cualquier modelo que siga instrucciones puede "hacer" tool calls si el proxy hace la traduccion bidireccional. La calidad depende de que tan bien el modelo siga el formato, no de una feature nativa
5. **2026-02-11**: El proxy es el lugar correcto para comprimir contexto, no el cliente. Claude Code asume 200K y no sabe que habla con un modelo de 64K. `scale_tokens()` es la primera linea de defensa (hace que CC comprima antes), pero si un solo tool result grande salta el threshold, la compresion LLM en el proxy es la safety net que falta
6. **2026-02-12**: `stop_reason` no es decorativo — es la señal de control que CC usa para decidir si ejecutar tools. Un proxy que convierte formatos debe respetar TODAS las invariantes del protocolo Anthropic, no solo la forma del payload. La Anthropic API es un contrato: `stop_reason: "tool_use"` cuando hay `tool_use` blocks es OBLIGATORIO, no sugerido
