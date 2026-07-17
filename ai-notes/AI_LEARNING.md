# AI_LEARNING
> Aprendizajes iterativos del proyecto. Actualizar despues de cada sesion.

## Ultima actualizacion
- Fecha: 2026-07-17
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
