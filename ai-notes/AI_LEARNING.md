# AI_LEARNING
> Aprendizajes iterativos del proyecto. Actualizar despues de cada sesion.

## Ultima actualizacion
- Fecha: 2025-02-07
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
