# AI_LEARNING
> Aprendizajes iterativos del proyecto. Actualizar despues de cada sesion.

## Ultima actualizacion
- Fecha: 2026-03-05
- Por: claude-sonnet-4-6 + jeguzman

---

## Session 2026-03-05: MCP Servers Configuration and Validation

### MCP Setup Completion
Configurado y validado 7 servidores MCP en `.mcp.json`:

| MCP | Estado | Propósito |
|-----|--------|---------|
| alloydb | ✅ | PostgreSQL queries (localhost:5435/ods) |
| atlassian | ✅ | Jira/Confluence/Bitbucket |
| squit | ✅ | Legacy SP search (Deacero) |
| cloudsql | ✅ | CloudSQL PostgreSQL wrapper |
| context7 | ✅ | Upstash documentation search |
| serper | ✅ | Web search and scraping |
| playwright | ✅ | Browser automation (Chromium) |

### Archivos Creados
- `MCP_VALIDATION_REPORT.md` - Validación completa de todos los MCPs
- `scripts/serper-mcp.sh` - Launcher bash para Serper
- `scripts/serper-mcp.py` - Launcher Python multi-plataforma
- `scripts/serper-mcp.md` - Guía de Serper
- `MCPs_CONFIGURADOS.md` - README de MCPs actualizado
- `MCP_SETUP.md` - Guía general de MCPs
- `CLOUDSQL_MCP_SETUP.md` - Guía de CloudSQL

### Fixes Aplicados
1. **CloudSQL script** - `scripts/cloudsql-mcp.sh` actualizado para usar path correcto de postgres-mcp (`~/.nvm/versions/node/v20.20.0/lib/node_modules/postgres-mcp/dist/index.js`) en lugar del binary global corrupto
2. **Playwright** - Configurado con `PLAYWRIGHT_BROWSERS=chromium` para Apple Silicon (WebKit/Safari no soportado)
3. **Environment variables** - Credentials en `.env`: `ALLOYDB_PASSWORD`, `SQUIT_API_KEY`, `ATLASSIAN_*_TOKEN/KEY` — deben estar definidas con valores reales para que los MCPs funcionen

### Patrones Aprendidos
- **postgres-mcp global binary corrupto** - Usar path directo al package instalado en lugar de `npx postgres-mcp` cuando el binary global falla
- **Environment variable precedence** - MCPs usan variables de entorno desde `.mcp.json` env section, no del `.env` del proyecto (excepto cuando scripts los exportan explícitamente)
- **URL-encoding en passwords** - Passwords con caracteres especiales necesitan URL-encoding en connection strings (usar `encodeURIComponent` de node o sed manual fallback)

### Comandos de MCP
```bash
# Test AlloyDB
timeout 10s node ~/.nvm/versions/node/v20.20.0/lib/node_modules/postgres-mcp/dist/index.js

# Test CloudSQL
bash scripts/cloudsql-mcp.sh

# Test Serper
SERPER_API_KEY="[REDACTED]" npx -y serper-search-scrape-mcp-server

# Test Playwright
PLAYWRIGHT_BROWSERS="chromium" npx -y @executeautomation/playwright-mcp-server

# Switch CloudSQL environment
nano .cloudsql-env  # Cambiar WPC_ENV=dev|qa|prod
# Reload Window en VS Code
```

---

## Session 2026-03-04 (cont): tool_prompting.py Production Audit + XmlToolBuffer Hardening

### Fixes aplicados (878/878 tests pasan)

1. **BLOCKER — line 1154** ([tool_prompting.py](../vendor/claude-code-proxy/llm/tool_prompting.py)): `recover_incomplete_tool_call()` llamaba `extract_tool_calls_from_text(content)` SIN `valid_tool_names` → hallucinated tools bypassaban filtrado. Fix: construir `_recovery_valid_names = _build_valid_tool_names(tools)` y pasarlos.

2. **HARDENING — line 1441**: `self.buffer.find(_TOOL_CALL_OPEN, 1)` matcheaba `<tool_call_backup>` → falso restart. Fix: validar `after_char not in ('>', ' ', '\t', '\n', '\r')` antes de hacer restart.

3. **HARDENING — line 1392**: `isalpha()` rechazaba tool names con prefijo `_` (MCP tools). Fix: `isalpha() or == '_'`.

4. **WHITESPACE BUG — line 1377** (`_try_extract_text`): `isalpha()` en `buffer[name_start]` rechazaba `<tool_call>\nBash`. Fix: whitespace-skipping loop antes del check.

5. **WHITESPACE BUG — line 1268** (`_has_plausible_tool_call`): mismo problema en `rest[0].isalpha()`. Fix: `rest.lstrip(' \t\n\r')` antes del check.

6. **DOCUMENTATION — line 433** (`_TOOL_DILUTED_RE`): mismatched tags `<args>...</arguments>` son intencionales — agregar comentario explicativo.

### fire-test-cc Validación (audit-hardening-20260304-195032)
- **Quality: 0.85/1.0** ✅ PASS (4 py_refs, 0 ts_refs, arch análisis completo, code blocks)
- **[no-tools] WARNING → FALSE POSITIVE**: GLM citó `<tool_call>` dentro de un code block Python en su análisis de source code. Los 6 regexes correctamente rechazaron este fragmento (no es un tool call real).
- **GLM respondió sin usar tools**: respondió desde contexto, no invocó herramientas (esperado en sesión con historial largo)
- **quality-report.txt faltante**: Python heredoc [6/6] falló por razón desconocida pero calidad verificada manualmente = 0.85 PASS

### XmlToolBuffer — regexes confirmados como CORRECTOS (no dead code)
- `_TOOL_CALL_GREEDY_RE` (line 394): usado en `_parse_tool_xml()` lines 1570-1584 para XML embebido
- `_TOOL_CALL_ARGKV_LOOSE_RE (?:...|$)`: `$` solo al final de string (sin MULTILINE) — no false positives
- Catastrophic backtracking: bounded `{0,8000}/{0,2000}` + responses < 10KB → safe

---

## Session 2026-03-05: Passthrough XML + fire-test-cc Validation

### Implementado
1. **passthrough_xml_tool_extraction()** ([streaming.py](../vendor/claude-code-proxy/llm/streaming.py)) — extrae argkv `<tool_call>` XML del stream SSE de passthrough antes de enviarlo a CC
2. **extract_xml_tools_from_passthrough_response()** ([converters.py](../vendor/claude-code-proxy/llm/converters.py)) — extrae XML de respuestas non-streaming passthrough
3. **stream_quality.py**: skip refinement cuando `analysis_phase == "READ"` — evita timeout de 91s en refinamiento intermedio
4. **fire-test-cc.sh**: fix `NameError: DURATION` (Python f-string vs shell variable)

### fire-test-cc Validación (22 gen-requests, 13 count_tokens)
- **Calidad real: 1.00/1.0** ✅ (5 py_refs, 0 ts_refs, arquitectura, 6 code blocks con fixes)
- **Routing correcto**: GLM-4.7 para READ/PLAN (32/35), MiniMax-M2.5 para BUILD/EXECUTE (3/35)
- **Cost**: $0.073 / 35 requests (22 gen + 13 count_tokens)

### Patrones Observados en GLM-4.7 via Passthrough
1. **Double-prefix malformation**: `<tool_call>G<tool_call>Glob` (27 chars, buffer flushed as incomplete)
   - Ocurre en primeras non-streaming requests con contexto corto
   - Actualmente: handled gracefully (incomplete_tool_call, CC ignora)
   - PENDIENTE: recovery en XmlToolBuffer (ver plan abajo)
2. **ConnectTimeout en passthrough streaming**: Z.AI falla 1 de cada 3 streaming requests
   - Fallback a LiteLLM funciona correctamente (12 tool_use blocks generados)
3. **Refinement catastrophic failure**: 0.40 → 0.00 score en refinamiento pasado a non-streaming passthrough
   - Fix: skip refinement cuando `analysis_phase == "READ"`
4. **SYNTHESIZING nunca se activa**: threshold=20 reads pero CC alterna read/write cada ~6 requests
   - Consecuencia: deepseek-reasoner (NO_TOOLS) nunca se usa para síntesis
   - No es crítico porque GLM produce síntesis de calidad igual

### Proxies Count-tokens
- 13/35 requests son `POST /v1/messages/count_tokens?beta=true` (0 output, 0 cost)
- CC CLI los envía regularmente — inflan el contador de requests pero no generan costo

---

## Session 2026-03-04: Bug Fixes + Quality Validation

### Bugs Críticos Fixeados

1. **Compression race condition** ([compressor.py:300-306](../vendor/claude-code-proxy/llm/compressor.py))
   - **Problema**: `timestamp` se calculaba antes del lock y se reusaba después de la llamada LLM (2-5s)
   - **Fix**: Recalcular `timestamp` dentro del lock justo antes de crear `_CompressionCache`

2. **Streaming resource leak** ([streaming.py:837-846](../vendor/claude-code-proxy/llm/streaming.py))
   - **Problema**: El generador `response_generator` nunca se cerraba en excepciones
   - **Fix**: Agregar bloque `finally` con `response_generator.aclose()`

3. **Retry logic incorrecto** ([proxy.py:155](../vendor/claude-code-proxy/proxy/proxy.py))
   - **Problema**: `attempt < max_retries` permitía un reintento extra que nunca se ejecutaba
   - **Fix**: Cambiar a `attempt < max_retries - 1`

### Validación
- **791 tests pasando** (unit tests)
- **Fire test: 85% quality score** (target: 80%)
- Intent: READ ✅, Phase: PLAN ✅
- Cost: $0.006 (target: <$0.50) ✅

---

---

## Patrones que funcionan

### Arquitectura
- Proxy como abstraccion total: Claude Code no sabe que habla con otro proveedor. Un `.env` cambia todo el backend.
- Hot-reload con bind mount + uvicorn --reload: cambios en `vendor/claude-code-proxy/` aplican sin rebuild

### Herramientas
- Docker bind mount de vendor/ para desarrollo sin rebuild
- `curl http://127.0.0.1:8083/api/stats | jq .` para observabilidad
- `curl "http://127.0.0.1:8083/api/logs?n=20" | jq .` para logs de requests

---

## Anti-patrones / Errores comunes

### Proceso
- MEMORY.md puede sesgar analisis si el agente lo usa como atajo en vez de leer codigo
- Siempre verificar claims contra el codigo actual antes de hacer aserciones

---

## Comandos utiles del proyecto

```bash
# Levantar proxy cloud
cd /Users/jeguzman/ai-tooling && docker compose up proxy_cloud -d

# Ver logs del proxy
docker logs ai-tooling-proxy_cloud-1 --tail 30 -f

# Health check
curl http://127.0.0.1:8083/health | jq .

# Stats del proxy (observabilidad)
curl http://127.0.0.1:8083/api/stats | jq .

# Ultimos 20 request logs
curl "http://127.0.0.1:8083/api/logs?n=20" | jq .

# Test rapido del proxy
curl -X POST http://127.0.0.1:8083/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":30,"messages":[{"role":"user","content":"hi"}]}'
```

---

---

## Session 2026-03-05 → 2026-03-10: Proxy Pipeline Hardening

### What was built
- `universal_tool_extraction.py` — migrated + consolidated from `tool_prompting.py`
- Response pipeline: quality gate + grounding validator + stream buffering
- `STREAM_BUFFER_QUALITY=true` path: `accumulate_stream` → quality score → optional re-request
- `DeferredToolsTransformer` — injects CC workflow tools (`EnterPlanMode`, `ExitPlanMode`) from `<available-deferred-tools>` system prompt block into `request.tools`
- `_CC_WORKFLOW_TOOLS` frozenset bypass in stream quality pipeline (prevents refinement on plan mode tool calls)

### Key learnings
- `accumulate_stream` must have try/except — upstream timeout returns partial chunks. Without it: clean client disconnect, user sees truncated response mid-plan
- `STREAM_BUFFER_QUALITY` should only apply to `ctx.is_analysis` (ANALYZING/SYNTHESIZING), not PLANNING — full buffering causes silent wait + timeout risk for long plans. Fix: change `ctx.intent != "CHAT"` → `ctx.is_analysis` in server.py lines 274+343
- `docker restart` does NOT re-read env files — use `docker compose --force-recreate` to pick up env var changes
- `PASSTHROUGH_TIMEOUT=120` is too short for GLM-4.7 on large contexts; use 300s across all profile-envs
- `anthropic/` prefix required in `BIG_MODEL`/`SMALL_MODEL`/`BUILDING_MODEL` for passthrough path when `PASSTHROUGH_REQUIRE_PREFIX=1` — bare model names (`glm-4.7`) fall through to LiteLLM and plan tab won't activate in VSCode

### Plan mode enforcement for non-Claude models
- Non-Claude models (GLM-4.7) skip `EnterPlanMode`/`ExitPlanMode` — they see these as regular tools, no training to treat them as workflow signals
- Root cause of "model executes during plan mode": ALL tools (incl. Edit/Write) are forwarded to model in `request.tools` with no phase check — `proxy.py:312` has no `ctx.phase` guard
- Intent enforcement prompt must name SPECIFIC forbidden tools by exact name — GLM-4.7 ignores vague "don't write code"
- Write/Edit to plan files (`.claude/plans/*.md`) IS allowed during plan mode — the model needs to write the plan file itself; only source code edits are forbidden

---

## Notas de sesiones anteriores

> Backups disponibles en:
> - `MEMORY.md.bak-20260302` (memoria completa)
> - `AI_LEARNING.md.bak-20260302` (aprendizajes completos)
> Restaurar despues de la prueba limpia.
