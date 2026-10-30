# AI_LEARNING
> Aprendizajes iterativos del proyecto. Actualizar despues de cada sesion.

## Ultima actualizacion
- Fecha: 2026-08-03
- Por: claude-sonnet-5 + jeguzman

---

## Session 2026-08-03: State-Assertion Verification Framework (ADR-0036/0037/0038/0039/0040)

**Contexto:** F-001 (`docs/findings/FINDINGS.md`) documentaba a Kimi K2 fallando en
orquestación de tools en espacios de búsqueda amplios (no reconoce deferred tools, acepta
aserciones textuales de plan-mode sin verificar). 6 direcciones de solución bocetadas, sin
decidir. Diseño hecho en plan mode con dos agentes Plan en paralelo (perspectiva "cheap-fix-first
por fases" vs "framework unificado desde el día uno") + síntesis con el usuario.

**Decisión de arquitectura:** framework unificado (`llm/state_assertion.py`) — un core puro
(`AssertionFinding`, `RULES_REGISTRY`, `evaluate_rules(phase, ...)`) + dos transformers delgados
(`state_assertion_request.py`/`state_assertion_response.py`) que se registran en
`build_request_pipeline`/`build_response_pipeline` (`proxy/proxy.py`). Las reglas concretas viven
en `llm/state_assertion_rules/*.py` y se auto-registran al importarse (patrón plugin) — un solo
punto de activación (`import llm.state_assertion_rules` en `proxy.py`).

**5 ADRs, en este orden de implementación:**
1. **ADR-0037** (independiente, sin depender del framework): para modelos frágiles
   (`is_fragile_orchestration_model`, env `FRAGILE_ORCHESTRATION_MODELS`, default "kimi"),
   `EnterPlanMode`/`ExitPlanMode` nunca se difieren — Step 4b nuevo en `deferred_tools.py`
   garantiza su presencia incondicional, exento del strip final de ADR-0012.
2. **ADR-0036**: el core, con `RULES_REGISTRY` vacío (plomería pura, cero cambio de
   comportamiento hasta que se registra la primera regla).
3. **ADR-0038** (`deferred_denial`): detecta cuando el modelo niega tener una tool que el proxy
   sabe que existe (catálogo estático + cache de sesión + regex `mcp__`), inyecta nudge de
   `ToolSearch`.
4. **ADR-0039** (`no_progress`): **no reimplementa nada** — descubrí que
   `llm/transformers/guardrail.py` YA tiene `_detect_consistently_failing_tools`/
   `_detect_stuck_tool_calls`, agnósticas de familia de tool (no limitadas a Read, a diferencia
   de H16 en `utils/quality.py`, que sí lo es y es lo que el finding original describía). La
   regla solo espeja esos hallazgos al audit trail del framework con `severity="log"`.
5. **ADR-0040** (`exploration_grounding`): mismo patrón de verificación previa — descubrí que
   la dirección 4 (checkpoint de orientación forzado) YA existe (`intent_classifier.py` Override
   D + `intent_enforcement.py` PLAN MODE NUDGE). Solo implementé la dirección 3 (chequeo de
   solapamiento de tokens entre el razonamiento de la respuesta y el contenido real leído del
   archivo editado — cierra el gap "binario y topic-blind" que ADR-0033 admite explícitamente).

**Corrección real durante implementación (no en el diseño original):**
`grounding_validator.py` llama `ensure_system_note(ctx, ...)` (pasando el `TransformContext`, no
el `request`) para los nudges de completion/generality-claims de ADR-0031/0033 —
`TransformContext` no tiene atributo `system`, así que esa llamada es un no-op documentado (crea
un atributo que nada lee). El canal real que SÍ funciona es `ctx.grounding_issues`, que
`quality_refinement.py` consume para armar el feedback de re-request. Los transformers nuevos
usan ese canal real, no repiten el no-op. No se arregló el bug preexistente (fuera de alcance).

**Severity como control de comportamiento del shell:** `"log"` = solo registrar en el audit
trail (algo más ya actuó, ej. `GuardrailTransformer`); cualquier otro valor = inyectar nudge real
(`ensure_system_note`/`ctx.grounding_issues`). Sin este gate, ADR-0039 habría duplicado el nudge
que `GuardrailTransformer` ya inyecta para el mismo fallo.

**Verificado:** suite completa sin regresión en cada paso — 1216 (ADR-0037) → 1243 (ADR-0036) →
1259 (ADR-0038) → 1272 (ADR-0039) → 1282 (ADR-0040). 66 tests nuevos en total para todo el
framework + 3 reglas.

**Principio (aplica dos veces en esta sesión):** antes de escribir una regla nueva para un
"gap" descrito en un finding/triage, verificar si el mecanismo ya existe en otro archivo del
pipeline con otro nombre — el finding original describía el gap correctamente pero no siempre
apuntaba al lugar correcto del código (ej. "loop-guard limitado a Read" describía H16 en
`quality.py`, no `guardrail.py`, que ya era agnóstico). Reimplementar sin verificar primero
habría creado un segundo mecanismo paralelo y potencialmente inconsistente sobre la misma
historia de `tool_result`.

**Fix post-review crítico (mismo día, tras pregunta directa del usuario sobre riesgo):**
`evaluate_rules()` (`llm/state_assertion.py`) NO tenía aislamiento de fallos por regla — un
`rule.evaluate()` que lanzara excepción se propagaba sin atenuar. Confirmado con trace completo:
`Pipeline.process()` (`llm/pipeline.py`) loguea y **relanza** cualquier excepción de un
transformer; en el camino no-streaming (donde corre `StateAssertionResponseTransformer`) NO hay
fallback intermedio — la excepción sube hasta el handler genérico de `server.py`, que la
convierte en un HTTP error, **descartando la respuesta real que el modelo ya había generado**
para ese turno. No es solo "se pierde el nudge", es "el turno completo falla". Fix: cada llamada
a `rule.evaluate()` dentro del loop de `evaluate_rules()` ahora está en su propio try/except —
una regla que revienta se loguea y se saltea, sin afectar a las demás ni al request. Verificado
con `TestEvaluateRulesFaultIsolation` (4 tests: no propaga, no bloquea otras reglas, orden-
independiente, distintos tipos de excepción).

**Limpieza tras el mismo review:** `count_recent_assertion_events` (helper de escalación en
`llm/session/state_assertion_cache.py`) se agregó especulativamente en ADR-0036 pero ninguna de
las 3 reglas reales (`deferred_denial`/`no_progress`/`exploration_grounding`) lo llamaba nunca —
se re-exportaba en `compressor.py` sin consumidor real. Eliminado (YAGNI): si una regla futura
necesita contar ocurrencias previas, puede filtrar `session_snapshot["assertion_events"]`
directamente (ya se lo pasan los shells), sin necesitar una función async nueva.

**Principio:** cuando alguien pregunta "¿esto es correcto?" sobre código que "se ve" sin usar,
verificar con grep exhaustivo ANTES de responder — no asumir que un import/re-export tiene un
propósito oculto. En este caso la observación era 100% correcta: era código muerto, no una
abstracción con consumidor futuro documentado que solo no se veía a simple vista.

**Segundo fix post-review (mismo día): registro de reglas por side-effect → explícito.**
El usuario notó que `DeferredDenialRule`/`NoProgressRule`/`ExplorationGroundingRule` solo se
instancian en un lugar de producción: la última línea de su propio archivo
(`RULES_REGISTRY.append(XRule())`, efecto de import). Preocupación válida: (1) si la cadena de
import se rompe (ej. alguien borra `import llm.state_assertion_rules` de `proxy.py`),
`RULES_REGISTRY` queda vacío para siempre sin ningún error ni log — falla 100% silenciosa; (2)
"qué reglas están activas" no era grep-able desde un solo lugar. Fix: `llm/state_assertion_rules/
__init__.py` ahora expone `ACTIVE_RULE_CLASSES` (tupla declarativa, única fuente de verdad) +
`register_rules()` (call site único y explícito, invocado por `proxy.py`). Las reglas ya no
tocan `RULES_REGISTRY` en absoluto. `register_rules()` es idempotente (limpia antes de
reconstruir) y NO deja que una regla rota tumbe el arranque completo del proxy — aísla el fallo
de construcción por clase (log ERROR + sigue con las demás), y loguea WARNING si el conteo final
no matchea lo esperado, o INFO con los IDs exactos si todo salió bien. Verificado con un boot
real (`python -c "import proxy.proxy"`): loguea `registered 3 rule(s): [...]` — ya no es un
supuesto, es observable.

**Principio:** "estado global mutado por side-effect de import" es un patrón de plugin-registry
común pero tiene un costo real de auditabilidad — cuando alguien lo señala como preocupante, la
respuesta correcta no es defender el patrón, es hacerlo explícito (una lista declarativa + una
función de activación con logging, en vez de imports dispersos con `# noqa: F401`).

**Tercer fix, mismo hilo (el usuario empujó un paso más): de "explícito" a "sin global en
absoluto".** El fix anterior (`register_rules()` + `ACTIVE_RULE_CLASSES`) resolvió la falla
silenciosa y la falta de un lugar único para ver qué reglas están activas, pero `RULES_REGISTRY`
seguía siendo una lista mutable a nivel de módulo que `evaluate_rules()` leía implícitamente —
cualquier test o código futuro podía importarla y mutarla. Refactor a dependency injection
completa: `RULES_REGISTRY` se eliminó por completo. `build_active_rules()` (renombrado de
`register_rules()`) ahora es una factory pura que retorna una lista nueva sin tocar nada
compartido. `evaluate_rules()` recibe `rules` como parámetro explícito. Los dos transformers
(`StateAssertionRequestTransformer`/`StateAssertionResponseTransformer`) ganaron constructor
(`__init__(self, rules)`). `proxy.py` construye la lista UNA VEZ a nivel de módulo
(`_STATE_ASSERTION_RULES = build_active_rules()`) y la pasa al construir ambos pipelines —
importante detalle descubierto al investigar: `build_response_pipeline()` NO está cacheada, se
reconstruye en CADA request (`_run_response_pipeline` es "el call site canónico compartido",
sin cache) — pasar la MISMA lista de reglas (no reconstruirla) evita instanciar las 3 reglas en
cada request. Verificado con boot real: `request_transformer._rules is _STATE_ASSERTION_RULES`
→ `True` (identidad, no solo igualdad). Efecto colateral positivo: se eliminó el fixture
snapshot/clear/restore de `RULES_REGISTRY` de TODOS los archivos de test que lo usaban — ahora
cada test arma su propia lista local, cero estado compartido que limpiar.

**Principio:** cuando alguien empuja de "explícito pero sigue siendo un global" a "sin global,
inyección de dependencias real", vale la pena seguir — no es sobre-ingeniería, es remover una
categoría entera de fixtures de limpieza y hacer que las funciones sean puras respecto a sus
inputs. Antes de cambiar la firma de una función/constructor, grep todos los call sites primero
(en este caso: 2 en producción, ~20 en tests) para dimensionar el refactor con precisión, no a
ojo.

## Session 2026-03-05: MCP Servers Configuration and Validation

### MCP Setup Completion
Configurado y validado 7 servidores MCP en `.mcp.json`:

| MCP | Estado | Propósito |
|-----|--------|---------|
| alloydb | ✅ | PostgreSQL queries (localhost:5435/ods) |
| atlassian | ✅ | Jira/Confluence/Bitbucket |
| squit | ✅ | Legacy SP search (Company) |
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

---

## Session 2026-06-24: GitOps Monorepo + Trunk-Based Development

### What was built
- `docs/adr/ADR-0007-gitops-monorepo-trunk-based.md` — prerequisito ADR gate
- `scripts/release.sh` — reescritura completa del script propuesto con 7 bugs corregidos + nuevos comandos (`status`, `add`, `drop`, `promote`, `versions`, `init`, `init-multi`)
- `CODEOWNERS` — mapping de paths críticos del repo a `@jeguzman`
- `.agents/skills/infrastructure/gitops-monorepo/SKILL.md` — skill exportable separado de `gitops-expert`
- `templates/gitops/bitbucket-pipelines.yml.template` — 4 patrones de tag → ambiente
- `templates/gitops/CODEOWNERS.template` y `.pre-commit-config.yaml.template`
- `scripts/gitops-init.sh` — bootstrap de 5 pasos para distribuir la estrategia GitOps a otros repos

### Bugs críticos corregidos en release.sh propuesto original
- `grep -oP` no funciona en macOS/Alpine → POSIX `grep -o | sed` (sin `-P`)
- `cmd_cherry` solo procesaba el último hotfix (variable sobreescrita en loop) → array + iterar todos en orden ascendente
- Sort incorrecto para `hotfix.10` vs `hotfix.2` → `sed 's/.*\.//' | sort -n` extrae solo el número final
- Tags anotados: `git rev-parse tag` devuelve SHA del objeto tag → usar `^{commit}` en todos los `rev-parse`
- Sin validación de rama duplicada en hotfix → `git rev-parse --verify` antes de `checkout -b`
- Router pasaba CMD en `$@` → funciones usaban `${2:-}` frágil → `shift` antes del dispatch

### Bugs en gitops-init.sh durante validación
- `local` fuera de función (en cuerpo principal): `local py_paths="..."` → eliminar `local`, usar asignación simple
- Lógica de `py_paths` invertida: `[[ -d vendor ]] || py_paths="^src/"` sobreescribía con la condición errónea → invertir a `[[ -d vendor ]] && py_paths="^vendor/"` con prioridad ascendente
- `sed` multiline en macOS (BSD sed): `s|PLACEHOLDER|${MULTILINE_VAR}|g` falla con "unescaped newline inside substitute pattern" → reemplazar toda la lógica de sed por heredoc directo en bash

### Patrones que funcionan
- Heredoc en bash para generar archivos con sustituciones multi-línea: `cat > file << EOF` — evita problemas con `sed` en macOS y Alpine
- Prioridad ascendente para detección de directorios: primero default, luego `projects/`, `src/`, `vendor/` (el más específico último gana)
- `GITOPS_REMOTE` / `GITOPS_TRUNK_BRANCH` como env vars: permite adopción incremental sin forzar renombrar remotes ni branches
- `^{commit}` en todos los `git rev-parse` para tags: obligatorio en repos que usan annotated tags (la mayoría)
- `bash -n script.sh` para syntax check rápido antes de test funcional

### Patrones que fallaron
- `sed -e "s|PLACEHOLDER|$var|g"` con `$var` multi-línea en macOS: siempre usar heredoc
- `sort -t'.' -k2,2n` en strings semver como `proyecto@1.4.2-hotfix.1`: no extrae el campo correcto; la solución es `sed 's/.*\.//' | sort -n`

### Exportabilidad del GitOps
- `sync_skills.sh` solo copia `SKILL.md` — para exportar scripts ejecutables: embeber el script como código en SKILL.md (el agente lo genera en destino con Write)
- `gitops-init.sh --target <repo> [--skip-precommit] [--dry-run] [--trunk <branch>] [--scope <@org>]` es el bootstrap completo para cualquier repo nuevo

### Dependencias externas requeridas en repos destino
- `pre-commit` (pip install) — para instalar hooks
- `ruff` (pip install) — solo stacks Python
- `npx eslint` / `npx prettier` — solo stacks Node
- `python tools/check_adr_gate.py` — copiado por el script desde ai-tooling

### Validation results (2026-06-24)
- Python repo (`vendor/`): ✓ ruff con `files: ^vendor/`, ADR gate con `^(vendor/.*\.py)$`
- TypeScript repo (`projects/`): ✓ eslint+prettier, sin ruff, ADR gate con `^(projects/.*\.(ts|py|go))$`
- Multi-stack (`master` trunk): ✓ ruff+eslint+prettier, `no-commit-to-branch: master`
- CODEOWNERS blocked by config-protection.sh hook → crear en IDE o actualizar el hook

---

## Session 2026-07-09/10: Kimi K2 (Moonshot AI) — Análisis Post-Mortem

### Sesión analizada
`ahora-exhaustivamente-analizame-toda-glistening-sprout` en `school-system`, rama `docs/migrate-to-ai-notes`.  
Commit `26d5fd3` — 68 archivos, 46 errores nuevos en producción + 203 errores TS en tests.

---

### [K2-001] Tool calls con constraints oneOf violados

**Observado:** Kimi K2 generó `EnterWorktree(path="...", name="...")` — viola oneOf constraint (solo se permite uno de los dos parámetros, no ambos). Claude Code rechazó el tool call.

**Consecuencia:** Kimi cayó al fallback de `sed -i` sin worktree isolation, edits en disco sin coordinación.

**Fix (no resoluble con hooks):** Ejemplos negativos explícitos en system prompt. Es comportamiento del modelo.

**Patrón para futuros modelos:** Si un modelo genera tool calls inválidos repetidamente para la misma operación, es señal de que no entiende el schema — intervenir antes de que haga fallback destructivo.

---

### [K2-002] `sed -i 's/jest/vi/g'` destruye tipos TypeScript

**Observado:** Kimi ejecutó `sed -i '' 's/jest/vi/g'` en masa sobre 9 archivos de test, convirtiendo `jest.Mock` → `vi.Mock`. Resultado: 203 errores `TS2503: Cannot find namespace 'vi'`.

**Causa raíz técnica:** `vi.Mock` en posición de tipo requiere que `vi` sea un namespace TypeScript, no una constante. Incluso con `"types": ["vitest/globals"]` en tsconfig, `vi` se declara como `let vi: VitestUtils` — una constante, no un namespace.

**Fix real:** Agregar `import { vi, type Mock } from "vitest"` a cada test file y reemplazar `vi.Mock` con `Mock` en posiciones de tipo. La opción `"types": ["vitest/globals"]` en tsconfig.json es INSUFICIENTE para uso como namespace.

**Hook implementado:** `ts-quality-gate.sh` (PostToolUse) + `ts-enforce.sh` (PreToolUse) — bloquea siguiente edit TS si hay errores pendientes.

---

### [K2-003] Drift de scope: 68 archivos cuando scope era 3-5

**Observado:** Tarea era refactorizar tests (jest→vi). Kimi editó 68 archivos incluyendo 29 archivos de producción fuera del scope.

**Errores producidos en producción:**
- `SortAscending` (no existe en lucide-react) → reemplazar con `ArrowUpDown`
- Imports faltantes: `Separator`, `Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle`
- `updated_at` en FinalGrade (campo no existe, es `modified_at`)
- `Student` exportado desde `@/lib/types/grades` (no existe, está en `models`)
- Inicializaciones de estado con `{}` en lugar de `[]`
- `SelectTrigger size="sm"` prop inválida

**Hook implementado:** `scope-gate.sh` — bloquea edits fuera del scope definido en `.claude/task-scope.json`. El modelo actualiza el scope dinámicamente por subtarea.

**Protocolo en CLAUDE.md:** Antes de editar, crear `.claude/task-scope.json`. Actualizar `current_step` al avanzar. Eliminar al completar.

---

### [K2-004] Sin verificación TypeScript post-edit durante toda la sesión

**Observado:** Kimi no corrió `npx tsc --noEmit` en ningún momento. Los 46 errores de producción habrían sido detectables después del primer archivo editado.

**Hook implementado:** `edit-drift-detector.sh` — rastrea edits desde el último test run. Avisa en 8, 15, y 25+ edits sin verificación. Emite quality checkpoint al detectar test run.

**Patrón:** Sin enforcement externo, Kimi (y modelos similares) optimizan para "cantidad de archivos procesados" en lugar de "calidad del resultado".

---

### [K2-005] Evaluación general de Kimi K2

| Dimensión | Sin hooks | Con hooks (estimado) |
|-----------|-----------|----------------------|
| Tareas técnicas complejas | 7/10 | 8/10 |
| Disciplina TypeScript | 2/10 | 7/10 |
| Respeto de scope | 3/10 | 7/10 |
| Verificación post-edit | 0/10 | 7/10 (forzado) |
| Tool calls correctas | 7/10 | 7/10 (hooks no ayudan con schema) |

**Conclusión:** Kimi K2 executa bien las tareas técnicas cuando las hace, pero tiene drift sistemático sin enforcement. El sistema de hooks compensa los gaps de disciplina. Score sin hooks: ~4/10. Con hooks: ~7.5/10.

**Backend no tocado:** Commit `d09cedf` (Jul 6, 112 archivos .py) fue 100% cosmético — Black/PEP8 reformatting. Zero cambios de lógica. Kimi K2 NO modificó el backend en ningún momento de la sesión.

---

### Hooks anti-drift implementados (2026-07-10)

| Hook | Tipo | Matcher | Acción |
|------|------|---------|--------|
| `ts-quality-gate.sh` | PostToolUse | Edit\|Write | Corre tsc, guarda estado de errores |
| `ts-enforce.sh` | PreToolUse | Edit\|Write | Bloquea si hay errores TS pendientes |
| `scope-gate.sh` | PreToolUse | Edit\|Write | Bloquea si archivo fuera de task-scope.json |
| `edit-drift-detector.sh` | PostToolUse | Edit\|Write\|Bash | Cuenta edits, avisa a 8/15/25 sin test |
| `worktree-isolation-gate.sh` | PreToolUse | Workflow | WARN (no bloquea) — agentes paralelos con posibles writes |

---

## Session 2026-07-14/15: Kimi K2 proxy — ANALYSIS_MODEL fix + hybrid routing

### Root cause investigado y resuelto

**Bug: `ANALYSIS_MODEL=kimi-k2` (bare) + `PASSTHROUGH_REQUIRE_PREFIX=1` → 3 síntomas**

Con `PASSTHROUGH_REQUIRE_PREFIX=1`, `_is_passthrough_compatible("kimi-k2")` retorna `False`  
(sin prefijo `anthropic/`). Caía a LiteLLM → BadRequestError → fallback DeepSeek.

**Tres síntomas simultáneos explicados:**
1. `BadRequestError: LLM Provider NOT provided. model=kimi-k2` — LiteLLM no reconoce nombre bare
2. `analysis_thinking (generic fallback)` — `build_litellm_pipeline` tiene `analysis_thinking` param; passthrough NO
3. `analysis_refinements: 0` — DeepSeek retorna stream → early-return en `stream_response_pipeline` → `ctx.refinement_attempt` nunca se incrementa

**Fix inmediato:** `ANALYSIS_MODEL=anthropic/kimi-k2` en `cloud.kimi-coding.env`  
**Fix hardening:** Guard en `model_router.py:68-77` — si `analysis.model` es bare, aplica `build_model_name(preferred_provider, model)` automáticamente  
**ADR:** `docs/adr/ADR-0021-analysis-model-provider-prefix.md`

### Routing híbrido: DeepSeek primary + Kimi K2 para SYNTHESIZING

**Configuración final en `cloud.kimi-coding.env`:**
```
PREFERRED_PROVIDER=openai          # DeepSeek para tareas regulares
BIG_MODEL=deepseek-chat            # (era kimi-k2)
SMALL_MODEL=deepseek-chat          # (era kimi-k2)
BUILDING_MODEL=deepseek-chat       # (era kimi-k2)
ANALYSIS_MODEL=anthropic/kimi-k2   # Kimi K2 solo para SYNTHESIZING (reasoning)
MODEL_CONTEXT_WINDOW=65536         # DeepSeek-chat 64K (era 131072 Kimi)
ANALYSIS_CONTEXT_WINDOW=131072     # Kimi mantiene 128K para síntesis
ANALYSIS_MAX_REFINEMENTS=1         # explícito (era default)
STREAM_BUFFER_QUALITY=1            # explícito (era default)
GROUNDING_REFINEMENT=1             # explícito (era default)
```

**Por qué funciona:** `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY` (Kimi key) siguen presentes.  
El passthrough los usa directamente cuando `model=anthropic/kimi-k2`. `ANALYSIS_API_KEY` NO  
es necesario — `CredentialTransformer` solo aplica en LiteLLM pipeline, no en passthrough.

**Startup log de confirmación:**
```
provider: openai  big: deepseek-chat  small: deepseek-chat  building: deepseek-chat
[startup] Analysis: model=anthropic/kimi-k2 refinements=1
[startup] Passthrough: AUTO (anthropic endpoints → https://api.kimi.com)
```

### Lecciones clave

- **`docker restart` no recarga env vars** — usar `docker compose up -d --force-recreate`
- **Python code + env vars**: env changes solo necesitan `--force-recreate`; Python changes necesitan `docker compose build` (kimi-coding stack no tiene bind mount)
- **`PASSTHROUGH_REQUIRE_PREFIX=1` es estricto** — cualquier modelo sin `proveedor/modelo` cae a LiteLLM. Si LiteLLM tampoco lo conoce → BadRequestError. Siempre incluir prefijo explícito.
- **`analysis_thinking (generic fallback)`** en los logs = la request de SYNTHESIZING fue a LiteLLM, no a passthrough. Es la señal de diagnóstico.
- **`analysis_refinements: 0`** = el refinement loop nunca completó. Causas posibles: fallback retorna stream, ANALYSIS_MODEL no tiene prefijo, o ANALYSIS_API_KEY vacío en LiteLLM path.

### ts-quality-gate.sh: bug en grep detectado

`grep -n "useEffect"` matcheaba la línea de import `import React, { useEffect }`, haciendo  
`FIRST_USEF_LINE=1` → falsos positivos en todas las constantes. Fix: `grep -n "useEffect("` (con paréntesis).

---

## Session 2026-07-10: Beta→Admin migration + Kimi K2 debt cleanup

### Decisiones tomadas

**`worktree-isolation-gate.sh` revertido a warn-only:**
El hook estaba en `exit 2` (blocking) pero bloqueaba workflows legítimos de solo lectura. Revertido a warn porque el enforcement de conocimiento (ADR, learnings) no requiere bloquear — solo avisar. El regex `write|edit` también era demasiado amplio y matcheaba texto en prompts.

**Beta→Admin migration (ADR-0004):**
- 25 rutas `app/beta/` → `app/admin/` (git rename, preserva historia)
- 41 componentes `components/beta/` → `components/admin/`
- `app/beta/` y `components/beta/` eliminados completamente

### Errores encontrados y resueltos

**Patrón sistemático de Kimi K2 — hoisting de `useEffect`:**
Kimi escribe `useEffect` ANTES de declarar la función que llama. Esto es TDZ con `const`:
```tsx
// MAL (Kimi lo hacía así)
useEffect(() => { loadData(); }, []);
const loadData = async () => { ... };  // TDZ!

// BIEN
const loadData = async () => { ... };
useEffect(() => { loadData(); }, []);
```
Apareció en 13+ archivos. Siempre el mismo fix: mover la declaración antes del useEffect.

**Dependencia circular entre hooks — patrón `useRef`:**
`useBulkGrades` necesitaba `loadData` como callback, y `loadData` necesitaba `bulkActions` (resultado de `useBulkGrades`). Solución: `useRef` como puente para romper el ciclo sin cambiar semántica.

**Missing imports en archivos nunca compilados:**
`Badge`, `Package`, `Settings`, `Select` usados sin importar. Confirma que Kimi K2 no verifica compilación.

### Evaluación Kimi K2 en TypeScript/React: ~55/100
- Velocidad/volumetría: 9/10 — generó 60+ archivos rápido
- Arquitectura: 6/10 — estructura razonable, sigue convenciones Next.js
- Correctitud React: 4/10 — hooks violations sistemáticas
- Compilabilidad: 3/10 — missing imports, referencias a funciones inexistentes
- Testing/verificación: 1/10 — nunca corrió el código
- Python proxy: 7/10 — funcional y limpio

---

## Session 2026-07-15/17: Plan Mode — Fixes completos

### [P001] Window boundary tests usaban límites hardcodeados

**Observado:** `test_window_limits_scan` y `test_exit_plan_just_outside_window_is_not_found` tenían
`messages = [self._asst("ExitPlanMode")] + [self._asst("Read")] * 60` pero la función usa window=120.
Tests eran incorrectos desde el inicio (falsos negativos perpetuos).

**Fix:** Importar `_EXIT_PLAN_SCAN_WINDOW` como constante y computar límites relativos:
```python
from llm.transformers.deferred_tools import _exit_plan_already_called, _EXIT_PLAN_SCAN_WINDOW
messages = [self._asst("ExitPlanMode")] + [self._asst("Read")] * (_EXIT_PLAN_SCAN_WINDOW - 1)
```

**ADR:** `ADR-0024` (constant), `ADR-0025` (env-configurable).

---

### [P002] Plan guarantee tests fallaban silenciosamente

**Observado:** 4 tests llamaban `_make_ctx(phase="PLAN")` con `plan_mode_active=False` (default).
El Step 4 transformer guarda en `ctx.plan_mode_active=True`, no en `ctx.phase=="PLAN"` — por diseño
(evitar falsos positivos en SYNTHESIZING). Tests nunca ejercían el path real.

**Fix:** Pasar `plan_mode_active=True` explícitamente en los 4 test calls afectados.

---

### [P003] 422 Unprocessable Entity — role=system en messages array

**Observado:** CC beta envía mensajes con `role: "system"` dentro de `messages[]`. La Pydantic
`Message` model solo aceptaba `Literal["user", "assistant"]`.

**Fix:** Widened to `Literal["user", "assistant", "system"]` en `schemas.py:86`.  
**ADR:** `ADR-0026`.

---

### [P004] plan_mode_source como campo de TransformContext

**Problema:** EnterPlanMode en enforcement note era incorrecto para Signal 1 (CC toggle activo).
CC ya llama EnterPlanMode en el cliente — decirle al modelo que lo llame producía naming incorrecto.

**Solución:** Agregar `plan_mode_source: str = "cc"` a `TransformContext`. IntentClassifier lo
setea a `"cc"` para Signal 1 o `"proxy"` para Signal 2. `PlanModeEnforcementTransformer` elige
el enforcement note correcto según `ctx.plan_mode_source`.

**Dos notes:**
- `_PLAN_MODE_EXIT_NOTE` (cc): solo recuerda ExitPlanMode, no EnterPlanMode
- `_PLAN_MODE_PROXY_NOTE` (proxy): instrucciones Enter → explorar → escribir → Exit

**ADR:** `ADR-0027`.

---

### [P005] Plan preview dialog vacío — path conflict

**Síntoma:** "Accept this plan?" dialog aparecía pero sin contenido.

**Causa raíz:** CC inyecta en system prompt la ruta donde escribir el plan:
`"No plan file exists yet. You should create your plan at ~/.claude/plans/<name>.md"`.
El enforcement note del proxy instruía `".claude/plans/<nombre>.md"` (project-local).
El modelo seguía el proxy note → escribía en path incorrecto → CC buscaba en `~/.claude/plans/` → file not found → diálogo vacío.

**Evidencia:** Strings del CC binary (`~/.local/share/claude/versions/2.1.165`):
```
Custom directory for plan files, relative to project root. If not set, defaults to ~/.claude/plans/
planFilePath
The plan file path (injected by normalizeToolInput)
No plan file found at
```

**Fix:** Remover path hardcodeado de `_PLAN_MODE_EXIT_NOTE`. CC ya le dice al modelo el path correcto.
Para `_PLAN_MODE_PROXY_NOTE`: cambiar `.claude/plans/` → `~/.claude/plans/` (match CC default).

**ADR:** `ADR-0028`.

### [P006] scope-gate.sh bloqueaba rutas absolutas fuera del CWD

**Síntoma:** Complemento de P005. El scope-gate (mode=analysis) bloqueaba la escritura del
plan file a `~/.claude/plans/xyz.md` con `exit 2`.

**Causa raíz:** línea `RELATIVE="${FILE#$CWD/}"` — cuando el path es absoluto y no empieza con
el CWD, `RELATIVE == FILE`. Los patterns `case "$RELATIVE" in .claude/plans/*)` esperan rutas
relativas y nunca hacen match con un path absoluto. El hook bloquea la escritura.

**Bug confirmado:**
```bash
echo '{"tool_name":"Write","tool_input":{"file_path":"/Users/jeguzman/.claude/plans/test.md","content":"x"},"cwd":"/Users/jeguzman/Documents/school-system"}' | \
  bash scope-gate.sh
# scope-gate[analysis]: '/Users/jeguzman/.claude/plans/test.md' is outside analysis scope.
# exit: 2
```

**Fix (3 líneas):** Guard inmediatamente después de `[ "$RELATIVE" = ".claude/task-scope.json" ]`:
```bash
# Paths outside the project directory are outside scope-gate's jurisdiction.
[ "$RELATIVE" = "$FILE" ] && exit 0
```

**Aplicado en:** `.claude/hooks/scope-gate.sh` (ai-tooling fuente) y en school-system (copia instalada).

**Principio:** scope-gate protege archivos del proyecto, no paths externos. Rutas fuera del CWD
(`~/.claude/`, `/tmp/`) no son project files y no deben ser controladas por scope-gate.

### Resumen: diagrama del bug compuesto "plan preview vacío"

```
CC → system prompt: "write plan to ~/.claude/plans/xyz.md"
proxy note → "write to .claude/plans/<nombre>.md"  ← P005: path incorrecto
Kimi sigue proxy note (última instrucción gana)
Kimi → Write tool: file_path=".claude/plans/goofy-shell.md"
scope-gate: .claude/plans/* → ALLOWED (pero era el path equivocado para CC)
CC → ExitPlanMode → busca ~/.claude/plans/xyz.md → NOT FOUND → diálogo vacío

Si Kimi hubiera seguido CC (file_path="~/.claude/plans/xyz.md"):
scope-gate: RELATIVE == FILE (ruta absoluta) → BLOCKED ← P006: segundo bug
```

**Fixes aplicados:**
1. P005 (ADR-0028): enforcement note ya no hardcodea `.claude/plans/`
2. P006: scope-gate ahora permite paths fuera del CWD

### Patrón general: Enforcement notes no deben sobreescribir instrucciones de CC

Si CC ya inyecta una instrucción en el system prompt (path, tool schema, constraint), el proxy
NO debe repetirla con valores diferentes. El modelo prioriza la última instrucción visible en el
contexto — y nuestro note (inyectado al final vía `ensure_system_note`) ganará al de CC.

**Regla futura:** Cuando el proxy agregue un enforcement note que intersecta con algo que CC
también inyecta, verificar el CC binary con `strings` para validar que los valores coinciden.

---

## Session 2026-07-22: 93 tests fallando en el proxy — masking de un crash oculta 3 bugs reales

**Contexto:** al arreglar el import circular de `test_converters.py`, correr la suite completa
reveló `93 failed, 1098 passed`. Confirmado por `git stash`/`git stash pop`: preexistente, no
causado por cambios de la sesión.

### [P007] Un solo constructor roto puede enmascarar N bugs reales distintos

**Síntoma:** 88 de 93 fallos mostraban el mismo error superficial:
`TypeError: ClassifierConfig.__init__() missing 2 required positional arguments`.
Parecía "un" bug (helper de test desactualizado tras agregarse circuit-breaker a
`ClassifierConfig` — `config.py:106-107`, sin defaults).

**Causa raíz real:** al arreglar el constructor (agregar `max_consecutive_errors=3,
circuit_reset_seconds=60.0` a `_classifier_cfg()` en `tests/test_intent_classifier.py` y a
4 sitios inline en `tests/test_plan_mode_guard.py`), 84→5 fallos — pero 5 tests que antes
"fallaban por lo mismo" siguieron fallando por razones **completamente distintas y reales**:
no estaban rotos por el mismo bug, estaban crasheando ANTES de llegar a su propia aserción.

**Principio:** cuando muchos tests fallan con el MISMO stack trace superficial (un crash de
construcción/fixture), arreglar esa causa común y volver a correr la suite — no asumir que el
conteo bajó a 0 hasta confirmarlo. El crash compartido puede estar enmascarando bugs reales
independientes que nunca llegaron a ejecutar su lógica.

### [P008] Signal 4 (Implicit ExitPlanMode) no podía inferir `plan_mode_source` sin session_id

**Síntoma:** 4 tests de `TestSignal4ImplicitExitPlanMode` seguían fallando tras P007. El
escenario (real, reportado con Kimi K2): usuario hace `/plan`, modelo llama `EnterPlanMode`,
escribe el plan, CC cambia a Autoedit sin llamar `ExitPlanMode` — el desbloqueo implícito
(P0_UNLOCK, ADR-0008) nunca disparaba.

**Causa raíz:** `llm/transformers/intent_classifier.py:532-547` — `_pm_source` solo se leía de
`get_session_plan_mode_source(ctx.session_id)` (cache de sesión). Sin `session_id` (o cache
vacío, ej. primera clasificación de una sesión completa en una sola llamada), `_pm_source`
quedaba `None` para siempre, y Signal 4 nunca podía confirmar `source == "cc"` — aunque
`EnterPlanMode` apareciera explícitamente en el historial de mensajes de la misma request.

**Fix:** si `_pm_source is None` y `"EnterPlanMode" in _recent_tools` (ya computado para otro
uso, ventana de 120 tool calls), inferir `_pm_source = "cc"` desde el historial, no solo desde
cache/system-text del turno actual. 3 líneas, sin tocar ADR-0010 (proxy-initiated sigue
bloqueado igual, ya que ahí `EnterPlanMode` nunca aparece).

**Verificado:** los 8 tests de la clase (4 "fires" + 4 "blocked") pasan — confirmé explícitamente
que los 4 "blocked" no se ven afectados porque su gate de intent (`ctx.intent in (BUILD, VERIFY)`)
ya los excluye antes de llegar a este chequeo.

### [P009] Ambigüedad PLAN/BUILD: el primer heurístico regresionó; el segundo (más preciso) funcionó

**Síntoma:** `test_mixed_analysis_building_becomes_building` — texto con keywords de PLAN
("arquitectónico") y BUILD ("fix") a la vez debía clasificar BUILD, pero
`_regex_fallback_intent` (`router/llm_router.py`) tiene un empate explícito
"is_planning and is_building → prefer PLAN".

**Intento 1 (revertido):** `return "BUILD" if is_analysis else "PLAN"` en el empate. Pasó el
test objetivo Y `test_model_router.py` completo, pero correr la suite COMPLETA reveló una
regresión que ningún test individual mostraba: `test_router.py::test_regex_fallback_with_stripped_reminder`
— `"Analiza la arquitectura del sistema y crea un plan"` (is_analysis=True también) dejó de
clasificar PLAN. Ambos casos tienen la MISMA forma estructural (1 match planning + 1 match
building + is_analysis=True) pero requieren resultados opuestos → `is_analysis` no discrimina.

**Intento 2 (aplicado):** en vez de `is_analysis`, se necesitaba distinguir palabras de PLAN
que son el OBJETO explícito del pedido ("plan", "planea", "diseña") de palabras que solo
describen CÓMO investigar/construir ("arquitectónico", "análisis" como adjetivo/sustantivo
incidental). Se agregó `STRONG_PLANNING_RE` (subset de `PLANNING_RE`: solo
`plan|planning|planea\w*|planific\w*|dise[ñn]\w*|design`) y el empate ahora es:
`"PLAN" if STRONG_PLANNING_RE.search(text) else "BUILD"`.
- "crea un **plan**" → STRONG match → PLAN (correcto)
- "Propón un fix" (sin STRONG match, solo "arquitectónico" en PLANNING_RE normal) → BUILD (correcto)

**Verificado:** suite completa `1191 passed, 0 failed` (era `93 failed, 1098 passed` al inicio
de la sesión). Los 2 casos que antes eran mutuamente excluyentes con el intento 1 ahora
resuelven ambos correctamente.

**Principio:** ante una regresión encontrada solo por la suite completa (no por el test que se
estaba arreglando), no seguir iterando el MISMO heurístico — buscar una señal estructuralmente
distinta que sí discrimine (aquí: "es el objeto del pedido" vs "describe el método"), y
validarla contra AMBOS casos en conflicto antes de aplicar. Correr `pytest tests/ -q` completo
después de CUALQUIER cambio a regex/lógica de clasificación compartida, nunca solo el archivo
relacionado — el blast radius cruza archivos de test.

**Estado final de la sesión:** 93/93 arreglados (`1191 passed, 0 failed`, era `93 failed, 1098
passed`).

### [P010] Sin `X-Session-ID`, `plan_mode_active` nunca persistía entre turnos — causa raíz de un incidente real de Plan Mode

**Síntoma:** en una sesión real de Kimi K2 vía VS Code + proxy en school-system, el modelo nunca
llamó `EnterPlanMode`/`ExitPlanMode` en 206 mensajes — ni una vez, sin errores, simplemente nunca
lo intentó. En su lugar improvisó un workflow equivalente vía subagentes `Agent(Explore)` +
`Agent(Plan)` y escribió el plan a mano con `Write`, sin usar el mecanismo nativo de Plan Mode.

**Investigación (con evidencia real, no teoría):**
- `AskUserQuestion` y `ExitWorktree` SÍ funcionaron sin que el modelo llamara `ToolSearch` en
  ningún momento del transcript — descartando "falta ToolSearch" como causa.
- `utils/tool_utils.py:178` (`validate_tool_name_with_deferred_bypass`) confirma que el proxy
  YA trata los nombres de tools de workflow de CC como siempre-válidos independientemente de
  `ToolSearch` — el supuesto "sin ToolSearch falla silenciosamente" no aplica a nivel proxy.
- `curl http://127.0.0.1:8083/api/logs?n=500` (endpoint real del proxy) mostró
  `"session_id":""` en las 88 entradas logueadas de esa sesión — el header `X-Session-ID`
  (`server.py:251`) nunca llegó desde ese cliente (VS Code extension).
- El intent real (clasificador LLM, `openai/deepseek-chat`, no el fallback regex) oscilaba
  READ↔SYNTHESIZING↔PLAN↔BUILD turno a turno. `intent_classifier.py`'s Signal 3 (persistencia
  de `plan_mode_active` vía cache de sesión) solo actuaba `if ctx.session_id:` — sin fallback,
  a diferencia de `deferred_tools.py`, que YA tiene uno (`_compute_deferred_session_id`, hash
  determinístico de los primeros 20 mensajes). Sin persistencia, cada vez que el clasificador
  devolvía "PLAN" por un turno, `plan_mode_active` se activaba solo ESE turno y se perdía en el
  siguiente.
- `deferred_tools.py` (ADR-0012) quita `EnterPlanMode`/`ExitPlanMode` de `request.tools` cuando
  `plan_mode_active=False and intent != "PLAN"` — con el flicker de arriba, esa condición era
  cierta casi todo el tiempo. El modelo nunca tuvo una ventana estable para llamar la tool.

**Fix:** `intent_classifier.py` ahora reusa `_compute_deferred_session_id` (importado de
`deferred_tools.py`) — `effective_sid = ctx.session_id or _compute_deferred_session_id(messages)`
al inicio de `transform()`, usado en las 5 llamadas al cache de plan-mode en vez de
`ctx.session_id` directo. **ADR-0030.**

**Verificado:** suite completa sin regresión (`1191 passed`). Repro manual: 2 turnos,
`session_id=None` en ambos, mismo prefijo de conversación — turno 1 clasifica PLAN (activa
`plan_mode_active`), turno 2 clasifica CHAT (antes del fix se perdía el estado) — confirmado que
ahora persiste `True`.

**Principio:** cuando un mecanismo de persistencia por sesión tiene un fallback ya resuelto en
OTRO transformer del mismo pipeline (`deferred_tools.py`), y un transformer hermano
(`intent_classifier.py`) implementa la MISMA necesidad sin ese fallback, es una señal de que el
fallback debería vivir en un solo lugar compartido — la ausencia no es necesariamente
intencional, puede ser simplemente que nadie lo propagó al segundo lugar que lo necesitaba.

## Session 2026-08-04: skills de dominio, `Skill` vs `Agent`, y una sobreescritura silenciosa real (ADR-0043)

Hilo largo, disparado por comparar el patrón de skills de wpc-backend (`.claude/agents` symlink)
contra el de `ai-tooling`. Ver ADR-0043 para el detalle completo — aquí solo los aprendizajes
reusables.

### [P011] El discovery de `Skill`/`Agent` es asíncrono y diferido, NO fijo al inicio de sesión

Se probó crear symlinks nuevos en `.claude/agents/` y `.claude/skills/` a mitad de sesión.
Invocaciones inmediatas fallaron ("Unknown skill"/"Agent type not found"). Minutos después, sin
ninguna acción explícita, aparecieron system-reminders no solicitados ("New agent types are now
available...") y las mismas invocaciones funcionaron — `Agent` se refrescó primero, `Skill` unos
minutos más tarde. **Implicación:** no asumir que un archivo nuevo en esas rutas está disponible
de inmediato, ni que si falla la primera vez seguirá fallando — puede aparecer solo, sin reiniciar
sesión.

### [P012] `allowed-tools:` es un grant de auto-aprobación, NO una allowlist — en ningún contexto

Se confirmó empíricamente (subagente real reportando sus tools disponibles) que un subagent con
`allowed-tools:` en su frontmatter (campo pensado para el tool `Skill`) hereda TODAS las tools sin
restricción — el campo correcto para subagents es `tools:` (nombre distinto). Y releyendo la doc:
incluso para `Skill`, `allowed-tools:` solo evita el prompt de permiso ese turno, no bloquea nada
más. **Ninguna de las dos rutas da restricción real de tools sin sacrificar interactividad** (solo
`Agent`-subagent con `tools:` correcto la da, a costa de aislamiento total de contexto).

### [P013] `Read` de un SKILL.md no implica adopción de persona — ni con mandato explícito implica obediencia ciega

Confirmado dos veces con subagentes sin sesgo (fresh, sin memoria de la conversación): sin mandato
explícito, un `SKILL.md` leído se trata como "referencia de fondo". Con un mandato explícito
agregado al wrapper de `workflow-coordinator` ("tras el Read, adopta esto como protocolo
vinculante"), el agente sí se engancha en serio con el contenido — pero **no obedece
literalmente** partes del protocolo que chocan con la realidad verificable del repo (ej. un
"Architect Agent" que no existe, un push a un repo inexistente). Esto es el resultado CORRECTO,
no una falla del fix: el mandato debe lograr "no ignores esto", no "obedece ciegamente aunque esté
roto".

### [P014] El mandato de auto-carga de `CLAUDE.md` probablemente NO aplica a subagentes vía `Agent` tool

Dos pruebas con subagentes `general-purpose` frescos (sin instrucción explícita) nunca invocaron
`Skill(workflow-coordinator)` — resolvieron la tarea leyendo `AGENTS.md`/`SKILL.md` por su cuenta.
El hook que dispara el mandato (`skill-autoload.sh`, `UserPromptSubmit`) parece limitarse a la
sesión interactiva principal. No hay forma limpia de verificar/forzar esto desde dentro de una
sesión ya iniciada — límite conocido, no resuelto.

### [P015] `sync_skills.sh` (hook `UserPromptSubmit`, throttle ~24h) puede sobreescribir ediciones locales sin commitear — pasó de verdad, en vivo

A mitad de esta sesión, sin ninguna acción explícita, `.agents/.last_sync` se actualizó solo y
**sobreescribió 5 archivos ya corregidos** en `.agents/skills/` con el contenido cacheado de
`.agents/_repo` (que refleja el último estado pusheado a GitHub — rama `main`, no el working tree
local). No hay ningún aviso al usuario cuando esto pasa — solo cambia el timestamp de
`.last_sync`. **Mitigación práctica:** commitear ediciones a `.agents/skills/` pronto, no dejarlas
abiertas en sesiones largas. Verificado que `.agents/_repo` (clon separado, remote propio
`github.com:juvs91/ai-tooling.git`, tracking rama `main`) está correctamente configurado — el
riesgo es el throttle silencioso, no una mala configuración del sync.

### [P016] `adr-gate.sh` bypaseaba TODO si existía *cualquier* ADR nuevo sin commitear, sin verificar relevancia

El check de "¿hay un ADR staged?" solo miraba si existía algún `.md` nuevo en `docs/adr/`, no si
mencionaba el prefijo guardado que disparó el gate. Con 9 ADRs de trabajo previo sin relación ya
presentes, el gate quedaba abierto para cualquier edición a `vendor/`/`.agents/skills/`. Fix:
exigir que al menos un ADR nuevo mencione el prefijo guardado (`grep -qF -- "$GUARDED_REASON"`).
Se extendió además el guard a `.claude/agents/` y `.claude/skills/` — un archivo nuevo ahí registra
un subagent/skill real (ver P011) sin pasar por `workflow-coordinator`.

### [P017] Un reporte de sub-agente sobre "qué le falta a X frente a Y" debe verificarse leyendo el código antes de portar nada — puede estar desactualizado o simplemente equivocado

El análisis inicial de `deacero/commons` (delegado a un sub-agente Explore) afirmó que
`ai-tooling/tools/check_adr_gate.py` no soportaba `.claude/adr-gate.conf` y que solo `commons`
tenía esa mejora. Al diseñar el plan de homologación se leyó el archivo real y resultó falso:
`ai-tooling` ya tenía `_load_conf_rules()` con el mismo mecanismo — simplemente no tenía un
`.claude/adr-gate.conf` propio materializado (usa el fallback hardcodeado, que da el mismo
resultado). La brecha real era mucho más acotada (`_check_adr_sequence`/`_staged_files`
ausentes en `commons`). **Regla aplicada:** "la memoria/reporte dice X existe" no es lo mismo
que "X existe ahora" — antes de recomendar o portar algo basado en un hallazgo de análisis,
releer el archivo exacto. Ver `ai-notes/analysis/commons-gitops-analysis-2026-08-04.md` §5.

### [P018] `set -euo pipefail` + `[[ cond ]] && cmd` como sentencia standalone aborta el script si `cond` es falso — y no es un caso raro

En `cmd_worktree clean`/`cmd_check` de `release.sh` (ai-tooling y commons, mismo bug en ambos),
`[[ $found -eq 0 ]] && ok "..."` sin `else`/`|| true` hace que, cuando `found=1` (el caso *útil*
— sí hay algo que reportar), la sentencia completa devuelva 1 y `set -e` aborte el script antes
de imprimir los mensajes de ayuda subsiguientes. Se encontró reproduciéndolo en un sandbox
aislado (no en el repo real) antes de tocar código de producción — precisamente el tipo de bug
que un `bash -n` (solo sintaxis) no detecta, hace falta ejecutar el flujo real con un caso donde
la condición sea verdadera. Fix: agregar `|| true` al final de esa línea. Ver `ADR-0048`
(ai-tooling) / `ADR-0011` (commons).

### [P019] `str.lstrip("./")` no quita un prefijo literal — quita un SET de caracteres, y eso rompía el ADR gate para rutas con punto inicial

Encontrado al comitear el trabajo de homologación con `commons`: el pre-commit hook reportó
"Archivos guardados: 0" pese a que `.agents/skills/.../SKILL.md` estaba staged y es
justamente el guarded pattern principal de `check_adr_gate.py`. Causa: `_normalise()` usaba
`p.lstrip("./")` para quitar un prefijo `./` — pero `str.lstrip(chars)` quita repetidamente
cualquier carácter del set `{'.', '/'}` desde la izquierda, no el substring literal `"./"`. Para
una ruta como `.agents/skills/x.md`, eso se come el punto inicial (queda `agents/skills/x.md`),
y el `fnmatch` contra `.agents/skills/**/*.md` deja de matchear — el gate quedaba
silenciosamente inerte para exactamente la ruta más sensible que protege. El hook bash paralelo
(`adr-gate.sh`, que usa `${FILE#./}` — expansión de parámetro con prefijo literal, no
`lstrip`) nunca tuvo este bug; solo el `.py` (usado por el hook crudo de pre-commit y por CI).
Mismo bug encontrado y corregido en `commons/tools/check_adr_gate.py` (lo heredó al portarlo
en esta misma sesión) y en `wpc-backend/tools/check_adr_gate.py` (variante más simple, mismo
bug, ahí sí explotable en producción porque `.agents/skills/**/*.md` es uno de sus 2 únicos
guarded patterns hardcodeados). Fix: reemplazar por un loop `while p.startswith("./"): p = p[2:]`.
**Lección:** `str.lstrip`/`str.rstrip` con un string de múltiples caracteres NO hacen strip de
prefijo/sufijo literal — es un error de Python común y fácil de no notar porque el caso feliz
(rutas sin punto inicial) funciona bien. Ver `ADR-0048` (ai-tooling) / `ADR-0011` (commons).

### Nota histórica: "deagentic"/"Keystone" no eran referencias externas — eran nombres viejos sin renombrar

`git log --all --grep="deagentic"` encontró el commit real
`feat: rename cornerstone→ai-tooling in all skills + sync_skills local_path` (2026-07-01) — este
proyecto se llamó `cornerstone`/`deagentic` antes de renombrarse a `ai-tooling`. Ese rename quedó
incompleto en 4 archivos (`tool-writer`, `learning-protocol` + su eval fixture, `gitops-expert`) y
"Keystone" (mayúscula, proyecto NFC de ejemplo del autor original de la plantilla) quedó sin
genericizar en otros 4 (`ux-expert`, `bdd-writer`, `unknown-domain-protocol`, `adr-writer`,
`database-expert`). **Principio:** antes de asumir que una referencia a un repo/proyecto
desconocido es "inventada", buscar en `git log --all --grep` — puede ser un rename incompleto, no
una fabricación.

## 2026-09-29 — HLWSS Observer Fase 0: implementación + validación exhaustiva (vendor/hlwss)

**Contexto:** Fase 0 del Observer LLM (PRD v0.4 en `docs/prds/observer-llm/`) implementada en
`vendor/hlwss/` — Observer PyTorch+MPS sobre Qwen3-0.6B, ADR-0060, branch `ex-observer-llm`.

### Decisiones técnicas
- Todas las fórmulas de bytes (attn/FFN/embed/KV) validadas contra config real de Qwen3-0.6B:
  total calculado 1.400 GiB, head_dim explícito 128 (no el derivado 64 — `hidden²` hubiera dado -33%).
- V1 con criterio 0.0 (perfecto) sobre 256 tokens × 5 prompts; V4 reproducible; V3 95.7% (umbral 95%).

### Errores encontrados y resolución (todos en runtime real, los tests sintéticos no los atraparon)
1. **FFN denso emitido 5×/paso** (V1 77% off): el fallthrough de `block_id_for_module` mapeaba
   cualquier hijo de `mlp` (gate/up/down/act_fn) a `L{i}.ffn`. Solo el container posee el bloque.
   Lección: los tests con cfg sintético nunca ejecutaron `instrument()` contra un modelo denso real.
2. **V4 emparejaba por run_id alfabético**: `dd17159c` (smoke 16 tok, cronológicamente primero)
   quedaba "último" alfabéticamente y se emparejaba con una corrida de 256 → FALLA falsa.
   Fix: ordenar por `timestamp_utc` + emparejar solo mismos `generated_tokens`.
3. **NaN en tuplas V4**: `layer`/`token_pos` NaN (embed/prefill) hacen `tuple != tuple` aunque
   idénticas. Fix: normalizar a None en `_v4_seq`. El hash por `repr` ya era robusto.
4. **V1 sobre trazas concatenadas duplicaba token_pos**: smoke (16 tok) + corrida (256 tok) del
   mismo prompt suman bytes en t=1..15 (~2×). Fix: V1 por corrida y agregar (mean criterion, all-pass).
5. **Baseline HLWSS_OFF contaminaba V1/V4**: metadata ahora lleva `hooks: on|off`; los checks
   filtran las baseline. Sin hooks solo hay eventos KV derivados (7,168 vs 36,864 eventos).
6. **DuckDB**: `SUM(x)/1e6 FILTER (WHERE ...)` es error de parser — el FILTER va pegado al
   agregado: `SUM(x) FILTER (...) / 1e6`. Faltó test de analyze (se agregó `tests/test_analyze.py`).

### Patrones que funcionaron
- `task-verify.sh` como gate final obligó a escribir el checklist con paths reales
  (`.venv/bin/python`, `cd vendor/hlwss`) — el checklist con `python` genérico resolvió al venv
  equivocado (claude-code-proxy). Nunca poner `./scripts/task-verify.sh` dentro de su propio checklist.
- Backfill honesto de metadata (`hooks=off`) en vez de re-correr: la corrida baseline era conocida.
- Hallazgo analítico inmediato del analyze: lm_head domina (79.7 GB tocados / 256 pasos ≈ 311 MB/paso,
  tied con embed), ~2.4 GB bytes/paso en denso 0.6B — el "working set" real de decode son los pesos.

### Pendientes / notas
- LOC paquete hlwss ~1,000 (límite blando plan: 800; umbral review R5: 2,000) — decisión del owner.
- `prompts/validation.json` tiene 5 prompts; V3 quedó justo (95.7%): overhead de hooks ~4.3%.
- Paso 9 del plan (línea de cadencia de tests en CLAUDE.md) requiere aprobación — CLAUDE.md está
  fuera del scope de esta tarea.

## 2026-10-02 — HLWSS Fase 0.5: Ollama Spill Curve (implementada + ejecutada)

**Contexto:** PRD §4, ADR-0071, plan `.claude/plans/observer-llm-fase-05.md`. Máquina corregida a 16GB
(propietario confirmó; PRD v0.4 §0/§4/§4.1/§4.2 actualizados; fuente 13 = Jev/System One).

### Hallazgos empíricos (los que ninguna fuente publica)
1. **Ley num_ctx/2 (Ollama 0.34.3):** el prompt útil ≈ num_ctx/2 (2048→1026, 4096→2050, 8192→4098,
   32768→17744 completo). Para 64k de contexto REAL hay que pasar `--ctx 131072`. Confirmada dos veces
   (probe directo + Caso 0 cuyo turno 7 truncó exacto en 65,538).
2. **El stall del propietario fue REPRODUCIDO y explicado** (Caso 0: 9B, ctx 131072, 8 turnos,
   keep_alive 30m): tok/s 13.0 → 12.6 → 11.3 → 9.5 → 8.7 → **0.15** → **0.22** → 5.3. Causa medida:
   RSS runner pico 10.12GB + swapused +10GB (9→19GB) + 89GB de pageins en 67 min. No es "Ollama bug":
   es SPILL-KV clásico — cada token del decode pagina desde SSD (~518 MB/s efectivos medidos).
3. **El colapso exige acumulación, no solo num_ctx:** la escalera (3 modelos × ctx {8k,32k,131k},
   2 turnos, unload entre corridas) quedó TODA en FIT (12.7–17.1 tok/s). El acantilado aparece solo
   con multi-turno + keep-alive + contexto creciente más allá de la ventana.
4. **7B @ 131k encaja sin dolor** (KV ~3.8GB alloc + 4.7GB pesos); el 9B es quien cruza en 16GB.

### Decisiones técnicas
- Criterio de régimen SPILL = **swapins > 1GB** (pageins no sirve: incluye carga del modelo con
  file cache frío — falsos positivos en el primer heuristic).
- Runner de Ollama 0.34.3 = proceso `llama-server` (no "ollama runner"); pgrep por patrón
  "llama-server". Y el runner NO existe hasta que empieza la carga → resolución perezosa de PID
  en el sampler (pid cerrado en None → re-resolver cada muestra).
- Swap "usado" no viene de vm_stat: `sysctl vm.swapusage` (formato "used = 8147.88M").

### Errores/encontrados en runtime
- `--ctx` argparse → atributo `ctx` (no num_ctx). Chequeo glob alfabético vs mtime al inspeccionar
  trazas (uuid no ordena cronológico).
- LOC Fase 0.5: ~314 (ollama_probe 198 + cli ~116) vs límite 200 del plan — desviación reportada;
  total proyecto ~1,343 < 2,000 (umbral duro R5). Owner decide recorte.
- RSS muestras = 0 en primeros smokes por doble causa (patrón pgrep + pid stale) — ahora con
  test implícito vía smoke.

### Estado
- 29/29 tests, gate 3/3 (pytest + smoke live + report). Trazas: Caso 0 + 9 escalera + smokes en
  vendor/hlwss/traces/, CSV `traces/spill-curve.csv`. Scope cerrado por el gate.
- Próximo: E1 early-exit (plan aprobado pendiente) o model loader; o corridas 14B/32B si el
  owner autoriza descargas.

## 2026-10-02 (2) — Experimento num_ctx/2 + E1-A early-exit probes

### Hallazgos
1. **Mecanismo de la ley num_ctx/2 resuelto (parcialmente):** experimento
   discriminador (mismo prompt 53k, num_ctx fijo 8192, num_predict 64 vs 4000)
   → el borde usable quedó idéntico (4098). **Descartada la reserva para
   salida**; es una partición fija 50/50 del lado del servidor Ollama 0.34.x
   (`usable = num_ctx/2 + 2` exacto en 3 puntos). Causa raíz exacta pendiente
   de source-diving. Documentado en
   `ai-notes/findings/ollama-num-ctx-half-law-experiment.md`.
2. **E1-A primer resultado real (Qwen3-0.6B, 64 tokens, prompt validation #0):**
   el argmax NO se estabiliza hasta la capa 27/28 → bytes ahorrables = 0 a
   τ≥0.90. Perfil "late commitment": acuerdo ~0 hasta capa 18, subida
   0.14→0.22→0.27→0.34→0.56→0.69→1.0 en las últimas 7 capas. Entropía logit-lens
   coherente (4.1 nats difusas temprano → 1.84 final). **Conclusión honesta:**
   para early exit con acuerdo de argmax estricto hacen falta τ más laxos,
   tokens-que-salen-temprano por posición, o prompts más longevos — un prompt
   no es evidencia. E1-B queda gated por más datos de E1-A.
3. **Bug propio de entropía en early_exit.py (autodetectado):** clamp sobre el
   log (`clamp_min(-1e-9)`) aplastaba toda la distribución → entropía 0 siempre.
   La versión del adapter (`clamp_min(-1e9)`) es correcta — trazas Fase 0 sanas.
   Forma correcta: clamp sobre las PROBS (`probs.clamp_min(1e-12).log()`).
   Lección: fórmulas con log+clamp merecen test con distribución conocida.

### Decisiones
- ADR-0062 (staged): E1-A mide sin cortar, solo estadísticos (argmax/entropía/
  top1 por capa/token, nunca logits completos), reporte en metadata.early_exit,
  probe coexiste con Adapter (una pasada = traza Fase 0 + E1-A, insumo V1-E).
- τ no se fija: se reporta first_layer_at para {0.90, 0.95, 0.99}; elegir τ es
  decisión de E1-B con PPL en mano.

### Errores
- Test con valor esperado mal calculado (softmax([2,0,0,0]) = 0.711/0.096×3,
  H≈0.918 — yo había puesto 0.576/1.04). Regla: cuando un test de valores
  conocidos falla, recalcular a mano ANTES de tocar el código (casi "arreglo"
  el código correcto).
- scope-gate: escribir vendor/hlwss requiere ADR staged (git add basta, no
  commit) + scope build por sesión.

### LOC
early_exit.py ~120 + tests ~110 + cli ~15 → proyecto ~1,590 < 2,000 (R5 OK).

## 2026-10-02 (3) — E1-A batch: distribución de estabilización (n=15 corridas, 960 tokens)

### Hallazgo principal (negative result honesto, con matices)
Sobre Qwen3-0.6B (28 capas), greedy, 12 prompts diversos (+3 corridas previas
= 15 corridas): la curva media de acuerdo argmax-vs-final es ~0 hasta la capa
14, sube tarde: 0.27@18, 0.58@21, 0.84@25, 0.89@26, 1.0@27.

- **Salida estática estricta (toda la secuencia a una profundidad, τ=0.90):**
  ahorro >0 en 10/15 corridas pero saliendo en capa ~26/28 → solo 1-2 capas
  ≈ **3-7% del tráfico de pesos**. Palanca DÉBIL en esta forma.
- **Cota superior oracle por-token:** de la CDF (mean_s ≈ 28 − Σacuerdo ≈ 21),
  un token típico estabiliza en capa ~21 → salida dinámica per-token ideal
  ahorraría ~7/28 capas ≈ **25% del tráfico del decoder**. No realizable
  directamente (no se conoce el argmax final online; requiere predictor
  estilo CALM). La hipótesis "respuestas cortas estabilizan temprano" NO se
  confirmó ni en "capital de Francia" (capa 26).
- Conclusión: E1-B (corte real) queda gated; antes habría que probar predictor
  de confianza (CALM) o aceptar τ laxo. La tesis global NO se invalida:
  residencia/tiering/MoE son palancas independientes, y MoE es la dirección
  de la industria (más pesos en disco, menos tocados por token).

### Decisiones
- `hlwss report early-exit --traces --model --csv`: agrega curvas de acuerdo
  (la curva ES la CDF de estabilización) + conteo de corridas con ahorro>0 por τ.
- Prompts diversos en prompts/e1a-batch.json (12: factual/código/mate/poesía/
  traducción/sí-no/lista/secuencia) — diseñados para contrastar estabilización.

### Errores / notas operativas
- scope_file_for_session sin CLAUDE_CODE_SESSION_ID en shell cae al fallback
  legacy .claude/task-scope.json (task-verify de la sesión no lo ve; se
  verificó a mano y se limpió).
- El report agregó 15 corridas (las 3 corridas sueltas previas del mismo
  modelo entraron al filtro) — más datos, no bug, pero a saberlo.
- CSV de la curva: traces/e1a-batch-curve.csv.

## 2026-10-02 (4) — Fase 0.6: MoE REAL observado (Qwen1.5-MoE-A2.7B)

### EL número (tesis alimentada)
Sobre 64 tokens decode, 1 prompt (validation #0), MoE real 14.3B/2.7B-activos:
- **fracción de pesos de expertos tocada por token: 6.7%** (96 de 1440
  bloques = 24 capas × top-4 exacto — sanity check perfecto)
- **concentración: 567 bloques (39% del total) cubren 90% de los accesos,
  reuso medio 8.6× por bloque** → localidad temporal fuerte: una política
  expert-aware mantiene ~2.6GB caliente en vez de 6.6GB
- bytes activos/token totales: 3365 MiB (siempre-caliente bf16: attn+shared+
  lm_head ≈ 2.9GB + 442MB expertos int4). Run: traces/e820370d-*
- V2: OK 1512 pasos (gate sobre modelo real). Caveat: 1 prompt — el batch
  amplio queda para la próxima sesión.

### Decisiones
- Cuantización MIXTA: expertos fusionados (tensores 3D) → **int4 propio**
  (`quant_int4.py`: group=64, escala fp16, nibbles uint8, dequant perezoso
  solo de expertos seleccionados); resto bf16. ~10.5GB en RAM, 0.71 tok/s.
- Registry: `expert_dtype_bytes` separado (layout mixto real).
- V2: `want` ahora se construye de `sel` (selección real del router), no de
  topk recomputable — los near-ties bf16 (~1 ulp, 60 expertos) rompen distinto
  MPS vs CPU (medido: 2/168 flips). En path fusionado V2 verifica plumbing.

### Bugs encontrados (clase: container-only block ownership)
1. `shared_expert`: regla `tail[1:2]==["shared_expert"]` mapeaba container +
   gate/up/down/act → **5 emisiones por paso (bytes 5× inflados)**. Primer
   modelo con shared_expert real lo expuso. Fix: solo `len(tail)==2`.
2. Misma regla latente para expertos no fusionados (`experts.N.proy` mapeaba
   a `L{i}.expert.N`). Fix: solo `len(tail)==3`.
3. v2 recomputable-topk no era robusto a near-ties cross-device.

### Errores de terceros documentados
- torchao int4 exige `mslk` (librería CERRADA de Meta, no pública; el paquete
  PyPI "mslk" es un placeholder 0.0.0 sin módulo — ojo, posible typosquat).
- bnb 4-bit SÍ funciona en MPS (validado) pero solo cuantiza nn.Linear 2D;
  los expertos fusionados 3D quedan fuera → por eso el formato propio.

### Notas operativas
- Los "2 tensores meta" = rotary_emb.inv_freq + original_inv_freq (buffers
  vestigiales; transformers 5.x recalcula en forward) — inocuos, verificado.
- Cache bf16 de 28GB retenido (disco OK, 300GB+ libres); descartar cuando
  Fase 1 tenga loader propio si hace falta.
- LOC: 1,866/2,000 (R5) — margen 134 LOC. El siguiente feature grande debe
  recortar o pedir bump del límite al dueño.

## 2026-10-06 — LOC diet (hlwss-loc-diet): resultado honesto NEGATIVO en LOC

Intento de dieta sobre los 3 archivos editables (cli.py, ollama_probe.py,
early_exit.py; registry/checks/adapter_torch quedaron fuera por ser núcleo
de verificación). Resultado medido: **1,866 → 1,871 (+5)**, 44/44 tests
verdes, `report spill` funcional.

### Qué se hizo
- `_metas(traces, model)` en cli.py: las 5 rutas que caminaban metadata.json
  (report spill / early-exit / moe / check V1 / check V4) ahora comparten un
  solo walker. Bonus real: V1 leía cada metadata DOS veces (json.loads por
  condición) — ahora una.
- `_run(cmd)` en ollama_probe.py: deduplica el try/except de subprocess.run
  en runner_pid/_rss_bytes/vm_stat/swapused (4 usos).
- `_request(host, path, payload)`: deduplica la construcción del Request
  HTTP entre http_json y run_turn.
- early_exit.py: `_per_layer_top1` eliminado (se almacenaba y NUNCA se
  exportaba en report()) — el único ahorro neto real (−2).

### Lección (patrón que falló)
Extraer helpers en Python tiene costo fijo ~7 líneas (def + docstring +
blank). El boilerplate duplicado era de solo 3 líneas por sitio. Con <5
sitios, el helper NO paga en LOC — solo en DRY/auditoría. Regla: contar
los sitios de duplicación ANTES de extraer; se necesitan ≥5-6 usos para
ganar líneas, no solo claridad.

### Conclusión estratégica
La grasa estimada (150-250 LOC) NO existe en los archivos editables: el
código de verificación ya es denso y correcto. El presupuesto real:
1,871/2,000 → margen 129 LOC. El Simulator (~200 LOC) necesitará el ADR de
bump del cap R5 (criterio del dueño ya definido: "puede subir siempre que
lo medido sea verídico y fidedigno").

## 2026-10-06 — Simulator Fase 0.7: la tesis se sostiene en régimen SPILL

Replay de las 3 trazas Qwen1.5-MoE reales bajo 3 políticas (lru piso /
hlwss pin+SLRU candidata / oracle Belady techo), sweep 6–12GB.

### El número
- **8GB (spill): hlwss −9.9% bytes vs LRU, cierra 55% del gap al óptimo.**
- 10GB+ (FIT): hlwss −2.4% (peor) — con todo el working set cabiendo, la
  política es overhead. Negativo honesto, esperado, documentado.
- Pin mínimo viable: 7.38GB → régimen útil de la tesis ∈ [7.4, 9.5]GB.
- Resultado idéntico en las 3 corridas → determinismo del pipeline validado.

### Lección dura (patrón que casi corrompe el resultado)
Primer run: hlwss "ganaba al oráculo" (−70%). Artefacto contable: (1) el pin
se contaba residente SIN costo de carga; (2) incluía bloques PHANTOM
(`L{i}.ffn` denso que registry.build() crea para todo cfg pero un MoE real
no tiene — el fix de _pinned es intersectar con el stream). Regla nueva:
**en simuladores de memoria, auditá primero el costo de carga de lo
"pre-residente" y la existencia real de los bloques del universo.**

### Detalles técnicos
- `dict.popitem(last=False)` es API de OrderedDict, no de dict — y la
  anotación `x: OrderedDict = {}` NO cambia el constructor (sigue dict
  plano). Type hints no construyen tipos.
- El primer toque de un bloque pinneado es MISS (es la carga en frío), no
  hit — la simetría transferred/hit-fraction es parte del contrato.
- ADR-0064 (cap R5 2,000→2,500, criterio dueño) + ADR-0065 (diseño) staged.
  LOC actual: 2,140/2,500.

## 2026-10-08 — Análisis del gap al oráculo (go/no-go prefetch, Fase 0.7)

- **Tres hipótesis, tres NO-GOs con datos:** (1) prefetch por ventana de
  recencia NO-GO (distancia de repetición p50=115 toques vs 29 slots;
  window-recall 25% a K=128); (2) 2Q NO-GO (3,271 vs 2,986 MiB LRU); (3) "el
  pin es demasiado grande" NO-GO (sweep de cobertura top-50/70/90%: 11.40 /
  11.19 / 10.78 GiB — top-90% mejor en ambos budgets). Más pin estático = mejor.
- **Lección metodológica:** cuando un análisis diseña 3 candidatos, que los 3
  fallen ordenadamente (cada uno por razón distinta y verificable) es un
  resultado positivo, no un fracaso: cierra el espacio de políticas estáticas
  y localiza el valor restante (dinámico) con precisión.
- **Decomposición del gap:** oráculo ≈ piso teórico (cada bloque 1 vez, 9.49
  GiB); hlwss retransfiere 1.46 GiB de repeticiones de cola no-pinneada =
  miss estructural (distancia ≫ capacidad), no de política. El gap NO es
  capturable con historial → prefetch desprioritizado en Fase 1.
- **Veredicto Fase 1 acotado:** valor restante = detección de régimen (soltar
  pin en FIT, +8.6% en 8.75GB+) + hot-set que encoge con el budget (el pin
  fijo de 7.38GB se cae a 6GB; la versión dinámica recorta cobertura en vez
  de volverse infeasible). Caveat: 1 run de 64 tokens; re-evaluar localidad
  de tema con max_tokens ≥512.
- Análisis completo: `ai-notes/findings/simulator-fase-07.md` §gap-oráculo.

## 2026-10-13 — Fase 1 preflight: hlwss-adaptive (ADR-0066)

- **Componentes mecánicos validados con matices:** shrink-to-fit restaura
  viabilidad y gana ~−25% en 7–7.25GB (zona INFEASIBLE antes); régimen FIT
  elimina la pérdida del −8.0% a 9.5GB — pero el boundary es el WS único
  (9.32GB), no el crossover lru↔hlwss (8.6GB): en 8.75–9.25GB la pérdida vs
  LRU es spill real, NO overhead evitable. Corrección honesta a la predicción
  del plan.
- **Negativo honesto (β=0.9 a 6GB): pin parcial peor que LRU (−15.8%).**
  Con SLRU remainder de 0.6GB (bajo umbral de retención), cualquier byte
  quitado al pin es regalado: la cobertura extra que da β=1.0 (37→68%)
  convierte misses en hits a costo cero. Lección: **el sliver SLRU solo
  vale si puede retener la cola reutilizable; medir ese umbral antes de
  fijar β.**
- **Lección de diseño:** β=1.0 (pin hasta el budget completo) es no-regret —
  nunca pierde vs LRU, replica a la estática exactamente donde cabe. Un
  default con un acantilado conocido (−15.8%) es peor default que uno con
  peor caso +1.5%.
- β óptimo depende del budget (≈1.0 ≤6.5GB, ≈0.9 7–7.5GB, ≥8.5GB
  irrelevante) → la "asignación dinámica" del oráculo tiene versión
  mecánica: regla de umbral sobre el remainder. Próximo análisis, no código.
- Sweep + tabla β: `ai-notes/findings/simulator-fase-07.md` §Fase 1 preflight.

## 2026-10-15 — G1 Gemma 3n E2B: anatomía PLE (ADR-0067)

Hallazgos en `ai-notes/findings/g1-gemma3n-anatomia.md`. Lo esencial:

- **El eje MoE↔3n es filas-vs-bloques.** MoE: esparcidad en espacio de
  pesos (expertos, accionable por pin). 3n: esparcidad en espacio de
  vocabulario (filas PLE, 46% de pesos con 0.0004% de demanda) → invisible
  a políticas por bloques. Acantilado binario en simulación: LRU 0% hits
  a ≤8GB (91% del WS), pin infeasible, oracle solo −78% a 8GB.
- **El acantilado es de granularidad, no del modelo:** el OS pagina a 4KB
  y las ~92 filas PLE calientes caben en ~11MB. Caso más hostil para la
  tesis "model-aware > OS LRU": sin señal de prefetch (secuencial L0→L29
  estricto, 63/63) y la señal de residencia ya la explota el hardware.
- **ple.proj es el costo oculto del PLE:** la proyección global densa
  (31.46MB) se lee entera por token; domina 2000:1 a las filas de tabla.
  "PLE barato" = tablas baratas, proyección cara.
- **Bug de instrumentación real:** módulos hermanos compartiendo un bloque
  de familia + métodos auxiliares sin hook (`altup.predict`) → conteo 3×
  inflado. V1 lo detectó (desviación constante 3.99%). Regla: un bloque,
  un owner (laurel), como mlp→ffn. Fórmula de bytes validada contra params
  reales del checkpoint, no contra doc.
- **V1 criterion=0.0 es alcanzable** si la fórmula esperada se valida
  contra el checkpoint real.
- **V3 en M2: varianza de tokps 3.7× según carga de máquina** (0.91–3.48
  en corridas idénticas). Overhead real de hooks: no detectable (ratio
  1.018 par limpio intercalado). V3 de una corrida aislada es no
  concluyente — usar pares intercalados/medianas.
- **`activation_sparsity_pattern` del `-it` tiene 0 capas sparse** — la
  activation sparsity viene apagada de fábrica; G1.5 queda sin sujeto en
  este checkpoint. Leer la config REAL antes de instrumentar vale sesiones.
- **Gemma3nForCausalLM carga text-only del checkpoint multimodal** (0
  MISSING, towers UNEXPECTED) sin dep `timm`; nombres de módulo sin
  `language_model.` — el mapping acepta ambos prefijos.

## Exp1 (OLMoE falsación) + G2 (cuantizar-vs-política) — 2026-10-21

- **Falsación MIXTA (Exp1).** P1 demanda (2.36GB/tok, 17.1% WS) y P2 valle
  de spill (win −15.7% @8GB, valle [8,11]GB, WS 11.86GB) CONFIRMADAS en un
  MoE arquitectónicamente opuesto a Qwen1.5. La tesis "sparsity de
  activación topk → valle de spill" es ley del MoE (2/2 modelos, magnitud
  predicha por % activo). P3 (Jaccard cross-token) y P4 (concentración de
  frecuencia) FALSADAS: eran artefactos de Qwen. OLMoE: Jaccard 0.30
  (2.4× azar → prefetch viable, el NO-GO cae por modelo) y routing plano
  (top-8 cubre ~51%, no 90%). El win del spill viene del SLRU frío por
  sparsity estructural (12.5% activo), NO del pin por frecuencia → el pin
  top-90% está sobrejustado a Qwen; shrink-to-fit con cobertura medida.
- **V1 prefill MoE fusionado:** OLMoE ejecuta expertos POR POSICIÓN del
  prompt en prefill (topk×prompt_len filas/capa, 272/capa con prompt 34).
  Qwen1.5 (path por submódulos) emite 1 vez/capa — por eso el conteo viejo
  pasaba. El decode era exacto (criterion 0.0000) con la fórmula MoE nueva;
  el criterio principal nunca estuvo roto. Lección: validar checks contra
  DOS implementaciones del mismo concepto antes de darlos por generales.
- **G2: cuantizar (primer orden) vs política (segundo).** Sobre gemma3n:
  int4-todo mueve el umbral fit 11GB→4GB (−64%); ninguna política online lo
  logra (en denso, pin > budget siempre → infeasible; mejor régimen
  feasible = LRU 0% hits bajo el umbral). Con 4GB: bf16 inutilizable
  (645GB transferidos), int4 perfecto (3.93GB 1×). Regla práctica:
  cuantizar hasta WS_cuantizado ≤ budget; política solo si aún no cabe.
  Caveat: KV agregado (~2GB) no escalable por cuantización de pesos —
  weights-fit (2.21GB) ≠ sim-WS-fit (3.93GB).
- **Bug de caso en scale_blocks:** cli.py uppercasing de factores vs clave
  `weights` minúscula → S3 corría como identidad y "pasaba" todos los
  tests (los tests usaban minúscula). Lección: normalizar casing en UN
  punto (la función, no el caller) y cubrir el path cli→función en tests,
  no solo la función aislada.
- **V3 bajo swap no es medible por tokps absoluto** (0.01 tokps, 1.5h por
  corrida de 64 tokens con 13.8GB en 16GB): la conclusión de overhead
  queda heredada de G1 (ratio 1.018 en condiciones limpias); la corrida
  OFF es por protocolo, no por señal nueva.

## 2026-10-25 — Fase 1 engine real (ADR-0069): el ranking de políticas se INVIERTE en hardware

- **Hallazgo central**: el sim (ADR-0065/0066) decía "hlwss-pin gana −15.7%
  @8GB sobre LRU" en OLMoE; el hardware real dice "pin pierde ~2×" (16.3–17.3GB
  vs 7.7–9.1GB en 6–12GB, 64 tokens). La inversión se reproduce en la 2ª
  arquitectura MoE (Qwen1.5: 326 vs 152GB @12GB) y en denso (gemma3n: 489 vs
  310GB @8GB). Doc: ai-notes/findings/fase1-engine-real.md.
- **Mecanismo**: el sim modela la política como *orden de evicción*;
  `madvise(WILLNEED)` real es *prefetch activo con costo*. Con routing flat
  (Exp1 P4), la frecuencia running a 64 tokens cubre casi todos los expertos →
  el pin set rota → cada experto recién pinneado se pagina especulativamente →
  pin transfiere ≈1.1× WS total sin importar el budget (16.3GB constantes).
  En prefill, el MoE fused (topk×prompt_len filas/capa) dispara una tormenta
  WILLNEED de 14.9GB (2× LRU). LRU converge a steady state con 0 faults/token
  en ~8 tokens; la fracción EXPERT residente final es LA MISMA bajo ambas
  políticas (12.5 vs 12.7%) — la diferencia es solo cuánto se transfirió.
- **El LRU del sim era el artefacto**: LRU sim @8GB = 22.2GB vs LRU hw 8.3GB
  (~2.5×). El sim cobra cada touch frío sin readahead ni caché tibio. Gran
  parte del "win" de política del sim era ganancia contra su propio LRU
  pesimista. Lección: antes de creer en una política, validar el BASELINE
  contra hardware; un sim que solo modela la política nueva y no el mecanismo
  del baseline produce rankings invertidos.
- **ru_maxrss en macOS viene en BYTES, no KB** (Linux): peak_rss reportado
  3119GB → real 3.05GB. Bug clásico multiplataforma; normalizar con
  `sys.platform`. 
- **`sysctl vm.swapusage` separa `used`, `=`, valor por espacios** — parsear
  con regex `used = (\d+\.?\d*)M`, no por tokens `used=`. El parser viejo
  devolvía 0.0 silenciosamente y "pasaba" el sanity check porque 0 es
  plausible.
- **Caché tibio contamina pageins entre corridas** (page cache por-vnode
  sobrevive procesos): E1 (bytes ≥ único tocado) falla como cheque absoluto;
  E3 (Qwen3 n=5): CV 0.96 en absolutos. La señal válida es el ranking
  pareado + perfiles por token, no los GB absolutos. Medir frío requiere
  `purge` (sudo, no disponible) — documentado como caveat E8.
- **Glue de loaders que hay que presupuestar** (todas las variantes que
  encontramos HOY, todas necesarias): (1) transformers 5.x fusiona expertos
  OLMoE Y Qwen2Moe (gate_up_proj/down_proj) → _UnfusedExperts con vistas
  por-experto (matemática idéntica, 2 lineares); (2) checkpoint gemma3n
  multimodal: remap `model.language_model.` → `model.`, dropear torres;
  (3) buffers meta no persistentes por familia de modelo: inv_freq con theta
  por layer_type desde `rope_parameters`, embed_scale (`scalar_embed_scale`),
  router_input_scale (hidden**-1), per_layer scales — materializar genérico
  con fallo ruidoso; (4) marcadores 'ple'/'ple.proj' de gemma3n no son
  bloques físicos: bind() debe verificar `bid in registry.blocks()`;
  (5) AutoModelForCausalLM exige `from_config`, Gemma3nForCausalLM NO la
  expone (wrapper multimodal) → duck-type `hasattr(cls, "from_config")`.
- **LOC cap 2900 se sostuvo** con recortes de docstrings/comentarios (2899
  final) — el glue de arriba (~40 líneas) se pagó íntegro con golf de líneas;
  en retrospectiva un cap un poco mayor (2950) habría ahorrado ~30 min de
  micro-recortes, pero el ejercicio mantuvo el paquete honesto.

## 2026-10-25 — Fase 2: evicción pura (hlwss-evict), cierre de la inversión

- **Hallazgo**: el 100% de la penalización 2× de hlwss-pin era el prefetch
  WILLNEED. hlwss-evict (mismo pin + DONTNEED, cero WILLNEED, flag
  `Policy.prefetch = name == "hlwss-pin"`, engine.py:262) colapsa sobre
  os-lru: OLMoE b=6 → evict 7.48GB vs lru 8.05–8.17 (−7%, cap de evicción
  ata) vs pin 15.11GB (2.02×). gemma3n b=6: evict 286.2 vs lru 288.6
  (empate) vs pin 419.3.
- **Divergencia estructural #2 sim-vs-hardware**: el WS único tocado a 64
  tokens es ~7253MB (saturación de `resident_bytes` en policy.jsonl), la mitad
  del WS a-priori del registry (14.57GB) que cobran el sim y el flag `fit`.
  Consecuencia: a b≥8 el cap de evicción nunca ata → hlwss-evict emitió 0
  madvise y fue bit-a-bit os-lru; el "valle" del sim en [8,11]GB no existe a
  64 tokens en hardware. El sim cobra WS a-priori; el hardware solo paga lo
  tocado.
- **Control de ruido ideal gratis**: evict ≡ lru a b≥8 dio el piso de ruido
  pareado ±5% (lru reps @12: 7.15/7.84). Toda comparación futura debe exceder
  este piso para declarar efecto.
- **Lección Qwen3**: modelo demasiado pequeño + caché tibio = sin señal;
  `budget_eff` puede salir negativo si otros procesos fluctúan. Descartado
  como sujeto de política.
- **Puerta abierta principal**: spill real en corridas largas (routing flat
  hace crecer el WS tocado hasta los 1024 expertos) — test OLMoE b=8 ×
  tokens {64,128,256,512}, evict vs lru. Si el win crece con tokens, la
  política tiene frontera de utilidad; si no, el techo es el LRU del OS y el
  proyecto pivota a cuantización (G2b) / embed desagregado.
- Detalle mecánico: el cap de evicción usa budget nominal (engine.py:309),
  no _PIN_FRAC × budget; con pin ~0.9×budget queda margen fino (~0.6GB en
  b=6) — si el pin crece a más tokens el cap puede volverse inalcanzable.

## 2026-10-26 — Fase 3: horizonte largo, W(t) satura, valle refutado

- **Test**: OLMoE b=8 × tokens {64,128,256,512} × {os-lru, hlwss-evict} (8
  corridas intercaladas). Resultado: 0 evicciones en todas; pageins planos
  (7.6–8.9GB, sin crecimiento con tokens; pares dentro del ruido ±5%).
- **Hallazgo central**: `resident_bytes` = exactamente 7253MB en t=64/128/
  256/512 — el WS tocado satura antes de t=64 y entre t=64 y t=512 no se
  toca ni un bloque nuevo. La localidad del routing en greedy decode es
  FUERTE (reutilización de ~660/1024 expertos), no plana como sugería la
  cuenta combinatoria de cobertura uniforme (Fase 2 §3 era pessimista en
  ese punto: asumía uniformidad; el router concentra).
- **Predicción falsable #1 de la tesis: REFUTADA.** El valle del sim no
  existe a b=8 en ningún horizonte. Frontera de utilidad de la política:
  budget < W_sat (~7.25GB, este modelo/prompt/modo), sin importar tokens.
- **Decisión de proyecto derivada**: política de residencia = segundo orden
  puro salvo dispositivos con RAM < W_sat. Levers restantes en orden:
  cuantización (G2b), embed desagregado (0.7GB por 1 página/token), y como
  entregable científico: W_sat como propiedad medible del modelo (predice
  a priori si la política puede ganar en un dispositivo).
- Caveat: un prompt, greedy. Sampling o prompts diversos podrían expandir
  W(t); el engine lo mide igual si se quiere cerrar.

## 2026-10-26 — Fase 4: W_sat caracterizado + transición de fase W=RAM

- W_sat medible y estable: OLMoE greedy p0 = 7253MB en 10+ corridas; p1
  (prompt diverso) = 6797MB (−6%); Qwen1.5-MoE 128t = 5981MB (archivo 27GB).
  El prefill fija la cobertura; decode greedy añade casi nada (localidad por
  bucle de ruta).
- **Sampling T=1 cambia todo**: W cruza el budget 8GB → 9177 evictions de
  churn, 382.7GB transferidos (48× greedy). Transición de fase abrupta:
  +5% de W sobre el presupuesto = thrash. Confirma la formulación de la
  tesis §3 (costo discontinuo en W=RAM, working-set model).
- El valle del sim "no existe en el eje tokens" (Fase 3) pero SÍ en el
  espacio modo×budget: greedy mantiene W bajo; sampling lo dispara. La
  localidad que salvaba al greedy era una propiedad del modo, no del modelo.
- Replicación fría b=6 (flush 12GB, sin sudo): lru 8.49 / evict 8.12 =
  −4.4% (vs −7% tibio). Dirección confirmada; la ventaja de la política vive
  en refaults, no en misses de compulsión.
- Fix técnico: sampling en engine requirió nan_to_num sobre logits bf16
  (OLMoE bajo presión emite inf/nan) + float32 para el softmax. Flag
  --temperature (ADR-0070). LOC 2897 (golf: docstrings + blank lines).
- Caveat fases: trazas Fase 1 no comparables en resident_bytes con Fase 2+
  (fixes de conteo drain/bind; Δ 3.8%).

## 2026-10-26 — Puerta final cerrada: política vs LRU en thrash puro

- Pareado b=8 sampling T=1 128t: lru 80.0GB vs evict 78.9GB (−1.3%,
  empate dentro del ruido). Ambas churnean ~0.62GB/token; la corrida 512t
  (382.7GB ≈ 0.75GB/token) confirma la tasa. En thrash puro el orden de
  desalojo deja de importar: todo se toca poco y todo se rechaza.
- Patrón total de pares con spill real: evict gana 3/3 pero poco (−7%,
  −4.4%, −1.3%), diluyéndose con el churn. Efecto débil consistente.
- VEREDICTO DEL PROYECTO: la política de residencia model-aware no le gana
  al LRU del OS en ningún régimen medible. La jerarquía de levers queda:
  cuantización > política (≤7% condicionada) > prefetch (negativo).
  Decisión práctica: no pelear contra el kernel; cuantizar (G2b como
  proyecto nuevo con scoping propio si se retoma el objetivo práctico).
## Session 2026-10-15: settings que no aplicaban, validación de hooks y Azure Pipelines simulado (ADR-0061)

### `autoCompactWindow` es un entero de tokens: las fracciones se ignoraban en silencio
`.claude/settings.json` tenía `"autoCompactWindow": 0.9` (y el template de Kimi `0.80`).
El validador de Claude Code 2.1.301 lo define como `int().min(1e5).max(1e6).optional().catch(void 0)`:
cualquier otro valor se descarta **sin avisar**. Por eso "el override no se hace" en la otra laptop:
nunca se aplicó en ninguna. Ahora `settings.json` (compartido) ya no fija `model` ni
`autoCompactWindow`; van en `.claude/settings.local.json` por máquina, y `test_cc_kimi_init`
exige un entero en rango. **Lección:** ante un setting "que no hace nada", revisar el schema
real del binario (`grep -ao` alrededor del nombre) antes de buscar problemas de precedencia.

### Validar fixes de hooks = reproducir el bug con el código viejo
Harness old (`5528cd1`) vs new (`HEAD`) por stdin: 19/19 casos tienen bug con el código
viejo y no con el nuevo. Tres casos parecían "no reproducidos" y eran errores del harness: T6
miraba el ítem equivocado del checklist, y T7b daba exit 0 con el viejo porque decía
"Nothing to verify" (un falso completo, que justamente era el bug). **Lección:** un exit 0
no significa éxito; hay que revisar qué imprimió.

### Bug pendiente: `edit-drift-detector.sh` escribe `.claude/drift-state/` en el cwd
Usa `.cwd` del stdin en vez de la raíz del proyecto (el mismo tipo de bug que se corrigió en
scope-gate/intent-bootstrap). Tras un `cd` del agente deja directorios `.claude/` en
subcarpetas; uno llegó a colarse en un commit. Fix sugerido: `project_root` de
`task-scope-lib.sh`. Mientras tanto, no hacer `cd` en Bash (usar rutas absolutas).

### Azure Pipelines: lógica fuera del YAML + simulador (ADR-0061)
- Cada step es `bash scripts/ci-<x>.sh <comando>`. Las entradas van como variables del job
  (`CI_*`) o en el `env:` del step, y las salidas como `##vso[...]`. Los tests estáticos
  verifican que cada `ci_require` esté cableado.
- Gotcha de Azure: los jobs `deployment` **no** hacen checkout implícito. El placeholder
  funcionaba con un `echo` inline y al llamar a `scripts/` necesitó `checkout: self`.
- Gotcha de Azure: `PublishTestResults@2` sin archivos solo avisa. `'false'` (string) es
  truthy en expresiones. Sin `persistCredentials` el agente no puede empujar tags.
- El simulador detecta lo que el test estático no ve: renombrar `plan.source` → `plan.src`
  pasa el estático y el simulador lo reporta como `faltan variables de entorno: CI_SOURCE`.
- Docker Desktop no levantaba (la distro `docker-desktop` estaba Stopped y `/usr/bin/docker`
  era un symlink roto). Decisión: Docker Engine nativo en WSL (sin licencia de Desktop,
  igual que `ubuntu-latest`).

### Primera corrida real del simulador (Docker Desktop, 2026-10-15): 9/9 escenarios, 52 aserciones
Hallazgos al correr de verdad (ninguno aparecía con fakes):
- **Bug real del template:** `check-paths` corría ANTES de `UsePythonVersion` → python del
  sistema sin PyYAML (y PEP 668 impide instalarlo). Fix: el step va después y `ci-python.sh`
  instala PyYAML si falta.
- `docker buildx imagetools create` contra un registry OCI real conserva el digest en el
  re-tag (pendiente de ADR-0060, validado salvo la auth de ACR).
- Infra del simulador: nginx resuelve upstreams al arrancar (recrear el registry → 502, hay que
  recrear nginx también); `--force-recreate` conserva volúmenes anónimos (`--renew-anon-volumes`);
  `registry:2` rechaza `_catalog?n>1000`.
- **Mutación enmascarada:** "error de listado = no existe" no se detectaba porque el fallo
  simulado de auth también rompía `az acr login`. Se agregó el modo `list` (login OK, listar
  falla). **Lección:** si una mutación no se detecta, revisar si otra falla la está tapando.

### Planes fechados en `ai-notes/plans/` (2026-10-29, portado desde ai_by_design)
- Regla nueva en CLAUDE.md § "Planes": `ai-notes/plans/AAAA-MM-DD-<tema>.md`, con `Estado:` como primera
  línea después del título. Plan mode de CC sigue escribiendo su borrador en `~/.claude/plans/<aleatorio>.md`
  (ADR-0028). **No** se usa `plansDirectory` porque el nombre aleatorio rompería la convención de fecha.
  La copia fechada se hace a mano antes de `ExitPlanMode`.
- Hueco encontrado al validar: `scope-gate.sh` en build/full con `allowed_patterns` bloqueaba escribir
  planes. Ahora `ai-notes/plans/*` y `.claude/plans/*` se permiten siempre, salvo en validate.
  `intent-bootstrap.sh` agrega `<docs>/plans` a `analysis_write_paths`.
- Los tests de `ci-service.sh`/azure-sim fallan en macOS porque usan `mapfile` (no existe en bash 3.2).
  Esta falla ya existía y es independiente de este cambio.

### Worktrees nativos de Claude Code en el harness — ADR-0073 (2026-10-29)
- **Decisión:** NO hooks `WorktreeCreate`/`WorktreeRemove` (reemplazan la creación nativa, apagan
  `.worktreeinclude`, se propagarían a todos los consumers). Vía nativa + hooks conscientes de worktrees.
- **Verificado en vivo (Fase 0, CC 2.1.312):** el cwd del PROCESO hook sigue al `cd` del Bash tool;
  `CLAUDE_PROJECT_DIR` se queda en la raíz principal incluso en un subagente aislado; `isolation` sí llega en
  `tool_input` de Agent; el subagente comparte session id; sus eventos traen `agent_id` (el hilo principal no);
  NO hay variable de entorno que distinga principal de subagente (`CLAUDE_CODE_CHILD_SESSION=1` en ambos).
- **Bug reproducido en vivo:** tras `cd scripts`, `skill-load-gate` bloqueó `Agent` (buscaba el marker
  relativo al cwd). Y los hooks basados en `.cwd` crean `.claude/` basura en subdirectorios — ya se había
  commiteado en `vendor/hlwss/.claude/`. **Regla:** el estado de sesión SIEMPRE vía `state_root` de la lib.
- **Bug preexistente encontrado por los tests:** `adr-gate.sh` (`set -euo pipefail`) salía con exit 1 cuando
  no había ADRs nuevos (grep sin match en una asignación) → error NO bloqueante → el gate fallaba abierto
  justo en el caso a bloquear. **Lección:** en hooks con `set -e`, toda `X=$(… | grep …)` necesita `|| true`;
  y exit 1 ≠ exit 2 en Claude Code.
- **Lecciones de diseño (9 rondas de validación adversarial):** comparar paths siempre canonizados (doble
  montaje `/case-sensitive` → `/Volumes/case-sensitive`; git imprime físicos); worktree enlazado ⇔
  `--git-dir` ≠ `--git-common-dir` (la primera entrada de `worktree list` miente en submódulos y
  `--separate-git-dir`); `permissionDecision: "allow"` auto-aprueba el tool — un hook de solo-aviso usa
  `additionalContext` sin decisión; stderr con exit 0 en PreToolUse nunca llega al modelo; un ADR staged y
  luego editado queda `AM` (el gate aceptaba solo `A `).
- **Operativo:** no hacer `cd` persistente en Bash durante una sesión con estos hooks viejos — usar rutas
  absolutas o subshells `( cd … )`. `protect-skill-gate-bypass.sh` da falso positivo con cualquier `>` si el
  comando nombra el archivo protegido (registrado como fuera de alcance).

### Pendientes de ADR-0073 cerrados — ADR-0074 (2026-10-29)
- **install-hooks merge:** `.hooks = $hooks` borraba hooks del consumer. Ahora merge hook-por-hook
  (no por grupo) con propiedad por segmento de path + `manifest.registered`. **Bug atrapado por test:** en jq,
  tras `... as $x |` el `.` ya no es la raíz — usar `. as $root | ... | $root | .hooks = $x` (se perdía
  `permissions`).
- **Provisión que respeta al usuario:** crear solo si falta y registrar en el manifest; si el usuario lo
  borra, "declinado" (no se recrea en el sync diario). Mismo patrón para una línea en `.gitignore`.
- **ADR gate git:** `--check` con códigos 10-13 (no 0-3: chocan con `set -e`=1 y bash/argparse=2), una sola
  clasificación compartida por check e instalación, y los llamadores solo usan instaladores que declaren
  `# supports: --check` — una versión vieja ignora el flag e INSTALA. auto-sync solo migra (11); un clon sin
  gate (12) solo se avisa (decisión del usuario: no encender bloqueos nuevos en silencio).
- **Framework pre-commit:** los consumers de gitops-init usan `id: adr-gate` del framework; nuestro bloque
  agregado tras su `exec` es código muerto. Detectar "framework gestiona el gate" (no "framework instalado":
  puede ser solo lint).
- **Bypass gate:** un parser "solo destino" en bash pierde más de lo que gana (xargs, variables, find -exec).
  Mejor: regla amplia + exenciones probadamente seguras + verbos en posición de comando. `sed -E` de macOS
  no soporta `\s`; ordenar alternativas de la más larga a la más corta (`>>` antes que `>|`).
- **Tests:** funciones que crean fixtures dentro de `$(…)` no pueden usar contadores globales (subshell) —
  usar `mktemp -d`. Al listar el entorno con `env | grep`, filtrar por nombre exacto: un patrón amplio
  imprimió un token de socket local en la sesión anterior.

### Prueba exhaustiva ADR-0073/0074 (2026-10-29)
- **Mutation testing** (30 mutaciones en un worktree temporal, sub-suite por mutación): 24/30 detectadas de
  entrada. Sobrevivientes: 3 huecos reales de test (canon_path sin `-P`, match de tsc sin anclar, `cp` a
  directorio sin `/`), 1 mutación mal escrita, 1 equivalente (`set -e` ya aborta) y **1 test vacío**:
  `(cd x && ${2:+VAR="$2"} bash …)` — una asignación producida por expansión NO es asignación, bash la toma
  como nombre de comando y el test pasaba sin ejecutar nada. Usar `env ${2:+VAR="$2"} cmd`, y agregar una
  aserción que pruebe que el código bajo test sí corrió. Tras corregir: 29/30 (la restante equivalente).
- **E2E en vivo:** `cd` + Agent (gate ya no bloquea), EnterWorktree real (settings.local.json copiado por
  .worktreeinclude, adr-gate/protect-secrets/commit-msg bloquean dentro del worktree, sync_skills no-op,
  main-top sigue al worktree), subagente con isolation real corre task-verify y CONSERVA el scope del
  padre; el hilo principal lo borra.
- **Consumers reales:** gitops-init con framework pre-commit real → `--check` 13 y el framework sigue
  bloqueando; consumer legacy → auto-sync migra, conserva hook propio, `[skip-adr]` viejo ya no bypasea.
- `gitops-init.sh` sin `GITOPS_STACKS`/`GITOPS_PROJECT_MAP` pide input interactivo — en scripts, pasarlos.

### Portabilidad del harness: macOS + Linux (2026-10-29)
- **Matriz probada:** macOS (bash 3.2, BSD), Linux GNU (Alpine + coreutils/findutils/grep/sed = userland de
  Ubuntu/Debian/Fedora) y Linux busybox mínimo. Las 4 suites (178) y el E2E de consumers (22, con
  framework pre-commit real) en verde en las tres.
- **Cómo correrlo:** Docker Desktop solo comparte `$HOME` — el volumen `/Volumes/case-sensitive` y el
  scratchpad (`/private/tmp`) NO se montan. Mandar el repo por stdin:
  `COPYFILE_DISABLE=1 tar -cf - . | docker run -i alpine:3.20 sh -c '…; tar -xf - -C /work'`.
  apt de Debian está bloqueado por el proxy corporativo (Cloudflare); `apk` de Alpine sí funciona.
- **Bug real (fail-open):** `shasum` no es estándar en Linux (perl-utils / perl-Digest-SHA). Sin él,
  `install-hooks.sh` calculaba hashes vacíos e iguales → toda modificación local parecía "al día" y se
  sobrescribía sin backup. Fix: `sha256sum` (coreutils) con fallback a `shasum`, y abortar si no hay
  ninguno. Para comparar igualdad usar `cmp -s`; para snapshots, `cksum` (ambos POSIX).
- **busybox `mktemp`** exige ≥6 `X` en la plantilla; con menos falla, el path queda vacío y los fixtures
  colapsan al cwd (20 fallos "corridos" en githook-tests). Usar siempre `XXXXXX` y abortar si mktemp falla.
- **shellcheck** detectó un test débil: T9 calculaba el mtime y nunca lo comparaba (cksum no detecta una
  reescritura idéntica). Ahora usa `find -newer` (portable); verificado inyectando la reescritura.
