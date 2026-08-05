# Contexto para diseño de ADR: orquestación de tools de Kimi K2 en espacios de búsqueda amplios

**Propósito de este documento**: contexto exhaustivo y autosuficiente para
arrancar una sesión nueva enfocada en diseñar (vía skill `architect` →
`adr-writer`, per `AGENTS.md` §5 ADR-First Mandate) una solución de raíz al
patrón de drift descrito abajo. No decide el diseño — lo deja listo para
decidirlo.

**Documentos previos relacionados (leer si hace falta más detalle):**
- `ai-notes/analysis/kimi-school-system-drift-triage-2026-08-02.md` — triage
  completo, línea por línea, del incidente que originó este trabajo.
- `docs/findings/FINDINGS.md` → entrada **F-001** — el finding formal, con las
  4-5 direcciones de solución ya bocetadas.
- `bad_conversation.txt` (raíz de `ai-tooling`) — transcript real de 7847
  líneas de la sesión de Kimi K2.8 (redactado: se removió una llamada real de
  Playwright a un dominio externo, a pedido del usuario — ver nota de
  seguridad al final de este doc).

---

## 1. El problema, en una frase

Kimi K2 (y modelos no-Claude en general, ya documentado repetidamente para
GLM-4.7 también) falla mucho menos generando/razonando código que **decidiendo
qué hacer cuando tiene que orquestar un espacio de búsqueda amplio**: leer un
repo, hacer fetch de la web, sintetizar lo ya explorado, decidir qué tool usar
después, y actuar de forma congruente con lo aprendido. El síntoma más severo y
mejor documentado de esto es que el modelo **no conecta contexto que él mismo
generó/leyó, presente en la misma conversación sin compresión de por medio,
con la decisión inmediata que tiene enfrente** — no es un problema de memoria
perdida, es una falla de aplicación de contexto/orientación.

## 2. Evidencia concreta del incidente que originó este trabajo

Proyecto: `school-system` (backend FastAPI). Plan que se estaba ejecutando:
`~/.claude/plans/necesito-que-tomes-toda-swirling-catmull.md` — "Operation
Compiler para Formas Agenticas", 8 fases, diseño explícitamente agnóstico de
proveedor.

**Secuencia observada (con líneas exactas del transcript, ver el triage report
para el detalle completo):**

1. **Líneas 1-54**: loop de 5+ vueltas donde Kimi repite "voy a llamar
   Playwright" y en vez de eso vuelve a llamar
   `mcp__sequential-thinking__sequentialthinking`, hasta que esa tool devuelve
   un error real de validación y aun así tarda 2 vueltas más en cambiar de
   estrategia. Coincide con el patrón ya documentado en **ADR-0009** (re-lectura
   de archivo 40+ veces sin cambio de estrategia) — mismo mecanismo, distinto
   objeto del loop.

2. **Líneas 400-3242 (~2800 líneas)**: Kimi busca
   `mcp__playwright__playwright_get`, no la encuentra en su lista de tools
   visibles, y en vez de reconocer que es un **deferred tool** (requiere
   `ToolSearch` primero — el mismo mecanismo que el propio Claude, en la sesión
   de análisis original, tuvo que usar para `EnterPlanMode`/`ExitPlanMode`/
   `AskUserQuestion`), concluye que "no está conectada". Termina invocando por
   error tools de BigQuery en su lugar, repetidamente.

3. **Líneas 3980-4262**: Kimi fetchea (WebFetch exitoso) y lee el contenido
   completo de un tutorial que explica, letra por letra, qué es un deferred
   tool y las 3 formas de query de `ToolSearch` (`select:`, keyword search,
   `+require`).

4. **Líneas 7822-7846, ~4000 líneas después**: necesitando `EnterPlanMode`
   (también deferred), Kimi razona *"There's an EnterPlanMode tool? Not
   listed... I don't have EnterPlanMode tool"* — **nunca conecta el
   conocimiento que él mismo leyó con la acción que necesita tomar.
   Confirmado por grep exacto: Kimi nunca invocó `ToolSearch(...)` ni una sola
   vez en las 7847 líneas de la sesión.**

5. **Líneas 4298-4336 — la violación real de "plan mode"**: cuando el usuario
   escribe en texto plano *"remember you are in plan mode"*, Kimi razona
   *"Also maybe we need to use EnterPlanMode? Not needed; user says already in
   plan mode"* — trata la afirmación textual como prueba suficiente de estado,
   sin verificar ni llamar la tool real. A partir de ahí implementa código real
   (README.md, pyproject.toml, tests, ADR-0006, contract_schema.py, sources.py,
   openapi_loader.py, etc. — líneas 4518-7596) creyendo estar en plan mode.

**Por qué el proxy no lo bloqueó**: existe un gate técnico real y duro,
`PlanModeGuardTransformer`
(`vendor/claude-code-proxy/llm/transformers/plan_mode_guard.py`), que si
`ctx.plan_mode_active == True` reemplaza los `tool_use` de
Edit/Write/MultiEdit/NotebookEdit y Bash-de-escritura por bloques de texto
(Claude Code nunca los ejecuta). Pero esa bandera depende de señales reales:
historial de llamadas a `EnterPlanMode`/`ExitPlanMode`, el string de sistema de
Claude Code ("Plan mode is active"), o el intent-classifier
(`llm/transformers/intent_classifier.py`). Como `EnterPlanMode` nunca se llamó
y ninguna otra señal se disparó para ese mensaje del usuario, `ctx.plan_mode_active`
nunca pasó a `True` — el gate no falló, **nunca llegó a evaluarse**.

## 3. ¿Fue correcto el código que sí se llegó a escribir? Verificación real hecha

Repo: `school-system/agents/operation-compiler/`. Resultado (ya verificado con
lectura + ejecución estática read-only contra datos reales, no solo inspección
del transcript):

- Fase 0 (estructura + ADR-0006): completa, razonable.
- Fase 1 (~30% hecha): las dos piezas centrales del diseño **no** implementan
  el plan:
  - `compiler/contract_schema.py` no tiene `apiVersion`/`kind`/`metadata`/
    `informationGraph`/`executionPlan` anidado/`validationRules`/`renderers`
    — es un esquema plano de "operación OpenAPI cruda" con el nombre
    equivocado.
  - `compiler/sources.py` no implementa la precedencia de fuentes (ORM >
    Pydantic > Endpoint > Docs > OpenAPI) que el plan marca como su corrección
    más importante.
  - Bug reproducido en vivo corriendo `openapi_loader.py` contra el
    `openapi.json` real: falta el campo `payment_plan_id` en
    `create_enrollment` — exactamente el bug que el plan usa como ejemplo de
    por qué hace falta la fusión multi-fuente.
  - `openapi_loader.py` no está roto (corre sin excepciones sobre datos
    reales), pero tiene imports muertos y un bug confirmado: 5 endpoints sin
    key `security` no reciben ninguna clasificación de auth/público.
- Fases 2-7: 100% sin empezar, ni stubs. `fast-mcp` no está instalado en el
  repo.
- Tests: solo existe `test_project_structure.py`, que verifica que existan
  directorios (varios vacíos) — pasa 10/10 sin certificar correctitud alguna.

Esto importa para el diseño de la solución porque confirma que el costo real
del drift no es solo "tiempo perdido" — es **código con el nombre correcto y
la estructura correcta pero la lógica de negocio central ausente**, lo cual es
más peligroso que un fallo obvio porque puede pasar revisión superficial.

## 4. Arquitectura actual del proxy relevante para el diseño

`vendor/claude-code-proxy/` es un proxy Anthropic↔OpenAI-compatible. Pipeline
de transformers relevantes (todos en `llm/transformers/`):

| Archivo | Rol | Relevancia para este ADR |
|---|---|---|
| `intent_classifier.py` | Clasifica cada request en READ/PLAN/BUILD/VERIFY/CHAT/SYNTHESIZING; deriva `ctx.plan_mode_active` de 4 señales (historial EnterPlanMode/ExitPlanMode, string de sistema de CC, intent PLAN, session-cache fallback) | Es donde viviría cualquier señal nueva de "el modelo declaró no tener una tool que sí existe" |
| `plan_mode_guard.py` | Gate técnico real: bloquea Edit/Write/Bash-de-escritura si `ctx.plan_mode_active` | Depende 100% de que la bandera se derive bien — el gap está upstream de este archivo, no en él |
| `grounding_validator.py` | Detecta claims de "fixed X" (ADR-0031) y "todos los casos" (ADR-0033) vía regex ES/EN; NO tiene ningún check sobre "¿de verdad estás en el estado que crees?" | Candidato natural para extender con un check de "exploration-before-action" |
| `quality_refinement.py` + `utils/quality.py` | Loop de calidad agnóstico de proveedor (18 heurísticas H1-H18), no depende de bloques `<thinking>`; re-envía con nudge si falla el score | Candidato natural para la heurística de "loop de re-intento sin progreso" generalizada |
| `reasoning_handling.py` | Strip de bloques `<reasoning>`/`<think>` — no-op si el modelo no emite ninguno | No es la causa aquí, pero confirma que no se puede depender de CoT estructurado de Kimi para detectar nada |
| `provider_quirks.py` | Special-casing por nombre de modelo (temperature clamp, thinking params, endpoint path) para Kimi | Punto de extensión existente si la solución requiere lógica específica de Kimi |

ADRs ya existentes y relevantes (todos en `docs/adr/`):
- **ADR-0009** (kimi-model-drift-guardrails): drift documentado de Kimi K2 —
  re-lecturas de archivo 40+ veces, MCP tools descartadas como "alucinadas"
  (`validate_tool_name_with_deferred_bypass` las filtraba silenciosamente antes
  del fix), 67% crash rate en tool_use bajo concurrencia.
- **ADR-0010**: Kimi re-dispara `EnterPlanMode` cada ~60 turnos; fuente de la
  señal de plan-mode y sus bugs de clasificación.
- **ADR-0012**: strip de tools de plan-mode en el gate final para deferred
  tools.
- **ADR-0015**: diseño original del `PlanModeGuardTransformer`.
- **ADR-0016**: validación estructural de `tool_use` nativo (Kimi devolvía
  bloques con `id`/`input` nulos bajo concurrencia).
- **ADR-0025/0027/0028/0030**: mecánica fina de tracking de sesión para
  plan-mode (session-id fallback, ventana de escaneo, etc.) — historial extenso
  de iteración sobre el mismo subsistema.
- **ADR-0031**: completion-claim grounding — nace de un incidente real donde
  Kimi reclamó haber arreglado 3 bugs inexistentes mientras ignoraba el real.
- **ADR-0032**: refactor puro, descompone `compressor.py` en
  `llm/session/{store,plan_mode,completion_claims,quality_history,
  deferred_tools_cache,grounding,lifecycle}.py` — confirma que plan_mode,
  completion_claims, y grounding ya comparten un mecanismo de caché de sesión
  común, útil si la solución necesita estado persistente por sesión.
- **ADR-0033**: generality-claim grounding — nace de otro incidente real
  (Kimi generalizó sin verificar, dejó pasar un bug real de dependencias de
  `useEffect`). Su propio texto admite: "evidence check is binary and
  topic-blind" — mismo tipo de gap que se busca cerrar aquí.

**Patrón importante en todos estos ADRs**: cada uno nace de un incidente real
documentado con evidencia de transcript, no de especulación — el diseño que
salga de este contexto debería seguir el mismo estándar (referenciar este
incidente concreto, no proponer mecanismos genéricos sin casos de prueba
reales).

## 5. Direcciones de solución ya bocetadas en F-001 (no decididas — para evaluar en el ADR)

1. **Nudge determinístico de deferred-tool**: cuando el modelo declare
   textualmente "no tengo la tool X" / "X no está disponible" para una tool
   que el proxy sabe que existe (aunque diferida), inyectar automáticamente
   `"X es un deferred tool — usa ToolSearch('select:X') antes de llamarla"`.
   Resuelve directamente la causa raíz de la sección 2.4 (arriba).
2. **Generalizar la heurística de "loop de re-intento sin progreso"** de
   ADR-0009 (hoy limitada a `Read`) a cualquier familia de tool (`mcp__X__*`,
   etc.) — si la misma tool/familia falla o se reintenta 3+ veces sin cambio de
   estrategia, forzar un nudge de refinamiento antes de continuar.
3. **Checkeo de grounding "exploration-before-action"**: verificar que el
   modelo citó/usó contenido de exploración previa en su razonamiento
   inmediato anterior antes de actuar sobre él (no solo que la exploración
   ocurrió en algún punto de la conversación — gap ya señalado en el propio
   texto de ADR-0033).
4. **Checkpoint de "orientación" forzado** cada N tool calls en fases de
   exploración amplia, exigiendo síntesis explícita de lo aprendido antes de la
   siguiente acción.
5. **Nunca aceptar aserciones textuales de estado sin verificación técnica**
   (aplica tanto al patrón específico de "plan mode" como al patrón general).
6. Opción más simple y ya identificada como alto-impacto/bajo-esfuerzo: **no
   diferir `EnterPlanMode`/`ExitPlanMode` para modelos no-Claude ya
   documentados como frágiles en este flujo** (Kimi K2) — cargar su schema
   completo desde el inicio de sesión en vez de dejarlos como deferred tools.
   Esto no resuelve el patrón general (Playwright, otras MCP tools seguirían
   diferidas), pero elimina el 100% de las ocurrencias observadas del fallo
   específico de plan-mode en este incidente.

## 6. Preguntas abiertas que la sesión de diseño debe resolver

- ¿Se ataca primero el caso específico (deferred `EnterPlanMode`, opción 6,
  barato y ya evidenciado) o se diseña directamente la solución general
  (opciones 1-4, más ambiciosa, cubre Playwright/Serper/cualquier MCP tool
  también)? Ambas son compatibles, pero el orden de implementación importa para
  no bloquear valor mientras se diseña lo grande.
- Las opciones 1 y 3 requieren que el proxy sepa qué tools están "diferidas
  pero existen" — ¿ese catálogo ya existe en algún lado del proxy (para poder
  comparar contra lo que el modelo dice no tener), o hay que construirlo?
  (Pista: `validate_tool_name_with_deferred_bypass`, mencionado en ADR-0009,
  ya distingue MCP tools legítimas de alucinadas — podría ser la base).
  Confirmar leyendo ese código antes de diseñar.
- ¿La heurística de "loop sin progreso" (opción 2) debe vivir en
  `quality_refinement.py` (ya tiene el mecanismo de nudge + re-envío) o en un
  transformer nuevo? Revisar si extender un archivo con 18 heurísticas ya
  existentes es mejor que separar concerns.
- ¿Vale la pena diferenciar el tratamiento por proveedor (ej. solo aplicar
  estas heurísticas para modelos ya marcados como frágiles vía
  `provider_quirks.py`) o debe ser agnóstico como el resto del quality loop?
  El propio `quality_refinement.py` es deliberadamente agnóstico por diseño —
  revisar si eso debe respetarse aquí también o si este caso amerita
  excepción.
- Comparar `school-system/CLAUDE.md` contra `ai-tooling/CLAUDE.md` (que ya es
  explícito e iterativo sobre `ToolSearch`, aparentemente motivado por
  incidentes similares) — si la documentación en `school-system` ya era
  igual de explícita y aun así Kimi falló, es evidencia adicional de que la
  solución debe ser técnica (proxy/hook), no solo documental.

## 7. Nota de seguridad (contexto, no bloqueante para el diseño)

Durante la investigación de este incidente, `bad_conversation.txt` (el archivo
que contiene el transcript) disparó el detector de prompt-injection del
harness porque su contenido incluye texto en primera persona dirigiendo a un
agente lector a llamar Playwright/Serper contra un dominio externo
(`claude-world.com`). Se confirmó con el usuario que no es un ataque — es una
transcripción real que él mismo generó y el dominio es un sitio legítimo de
tutoriales sobre Claude Code. El bloque con la llamada real y el HTML
fetcheado fue removido del archivo a pedido del usuario. Esto no bloquea el
diseño de la solución, pero es relevante como motivación adicional para la
opción 5 (nunca aceptar aserciones textuales sin verificar): el mismo patrón
de "actuar sobre texto en la conversación sin distinguir su origen/legitimidad"
que llevó a Kimi a creer estar en plan mode es, en abstracto, el mismo
mecanismo que explotaría una inyección real.

## 8. Cómo arrancar la sesión nueva

1. Leer este documento completo (ya autosuficiente).
2. Invocar el skill `architect` (`software/architecture/architect/SKILL.md`,
   per tabla de routing de `AGENTS.md`) para diseñar la solución — su primer
   paso es escribir el ADR.
3. Antes de escribir código en `vendor/claude-code-proxy/`, confirmar que el
   ADR está en staging (`docs/adr/ADR-NNNN-*.md`) — el hook `adr-gate.sh` lo
   exige de todas formas.
4. Si se necesita más detalle línea-por-línea del transcript original, está en
   `ai-notes/analysis/kimi-school-system-drift-triage-2026-08-02.md` y en
   `bad_conversation.txt` directamente.
