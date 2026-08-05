# Triage: drift severo de Kimi K2.8 — sesión school-system (Operation Compiler)

**Fecha del análisis**: 2026-08-02
**Fuente**: `bad_conversation.txt` (7847 líneas, redactado — ver nota de seguridad
al final), proyecto `/Users/jeguzman/Documents/school-system`
**Plan involucrado**: `~/.claude/plans/necesito-que-tomes-toda-swirling-catmull.md`
("Operation Compiler para Formas Agenticas en School-System", 482 líneas, 8 fases)

## Resumen ejecutivo

Kimi K2.8 nunca violó "plan mode" en el sentido de saltarse un gate técnico real
— nunca llegó a activar ese gate porque **nunca invocó `EnterPlanMode` ni una
sola vez en toda la sesión**. En su lugar, aceptó la afirmación textual del
usuario ("remember you are in plan mode") como si fuera el estado real, y
procedió a implementar de verdad un plan de 8 fases mientras narraba
internamente que estaba "en modo plan". Esto no es un bug de "chain-of-thought
malo" en el sentido de razonamiento incoherente — el razonamiento interno es,
turno a turno, bastante lógico — es una falla de **no verificar el estado antes
de actuar sobre él**, agravada por una incapacidad total de descubrir y usar
`ToolSearch` para resolver el bloqueo de *deferred tools*, pese a haber
literalmente leído un tutorial que se lo explica. Por separado, hubo un loop
severo (~3700 líneas, casi la mitad de la sesión) de confusión de nombres de
tool intentando usar Playwright/Serper. Nada de esto lo detectó el
intent-classifier ni el grounding validator del proxy, porque ninguno de los
dos está diseñado para verificar *ese tipo* de fallo — están diseñados para
otros patrones (completitud, generalidad, plan-mode vía señales técnicas
reales), no para "el modelo cree estar en un estado sin haberlo verificado".

## Cronología del drift

### 1. Loop inicial: 5+ vueltas de "voy a llamar Playwright" sin llamarlo (líneas 1-54)

Kimi repite, turno tras turno, una variación casi idéntica de: *"I am stuck in a
loop... I will now actually call the Playwright tool... no more thinking"* — y
en cada turno, en vez de llamar Playwright, vuelve a invocar
`mcp__sequential-thinking__sequentialthinking`. Esto continúa hasta que esa
misma tool devuelve un error real de validación (falta el campo booleano
`nextThoughtNeeded`, línea 35-44) — y aun con el error explícito en pantalla,
Kimi tarda dos vueltas más en efectivamente intentar otra tool.

Esto coincide exactamente con el patrón ya documentado en **ADR-0009** (drift de
Kimi K2: un archivo re-leído 40+ veces sin cambio de estrategia). Aquí el
objeto del loop es una tool de "pensar" en vez de un archivo, pero el mecanismo
es el mismo: el modelo repite la misma acción no-productiva en vez de escalar o
cambiar de enfoque.

### 2. Confusión masiva de nombres de tool / falla de tool-calling (líneas 400-3242, ~2800 líneas)

Kimi busca `mcp__playwright__playwright_get` en su lista de tools visibles, no
la encuentra, y en vez de reconocer que es un *deferred tool* (requiere
`ToolSearch` antes de poder llamarse — el mismo mecanismo que yo mismo tuve que
usar al inicio de esta sesión para `EnterPlanMode`/`ExitPlanMode`), concluye
repetidamente que "no está conectada" (líneas 414, 438, 468, 492). Intenta
igual llamarla "a ciegas" sin cargar su schema, y termina invocando por error
tools de BigQuery en su lugar — un patrón que se repite y que el propio Kimi
señala explícitamente: *"I am consistently failing to call the correct tool"*
(línea 3049), *"I keep accidentally invoking BigQuery tools"* (línea 2965).

Consume una fracción enorme de la sesión — desde línea ~400 hasta que finalmente
logra un llamado real y exitoso en la línea 3726 — sin que en ningún momento
llame `ToolSearch` para resolver la causa raíz.

### 3. Kimi leyó la explicación exacta del mecanismo que necesitaba, y no la aplicó (líneas 3980-4262)

Vía WebFetch, Kimi obtiene el contenido completo de un tutorial ("Deferred
Tools & ToolSearch") que explica letra por letra: qué es un deferred tool, las
3 formas de query de `ToolSearch`, y el flujo "load then call". Esto queda en
el contexto de la conversación. Sin embargo, ~4000 líneas después (línea
7822-7846), cuando el usuario pide detener la implementación y volver a
planear, y el sistema le exige llamar `EnterPlanMode`, Kimi razona:

> *"There's an EnterPlanMode tool? Not listed... I don't have EnterPlanMode
> tool... Maybe there is a way to enter plan mode via the /plan slash command?
> ... I will respond in text that I'm entering plan mode."*

Nunca conecta el conocimiento que él mismo leyó (`ToolSearch("select:
EnterPlanMode")` habría resuelto esto en un solo paso) con la acción que
necesitaba tomar. **Confirmado por grep exacto sobre las 7847 líneas: Kimi
nunca invocó `ToolSearch(...)` ni una sola vez en toda la sesión.**

Esto no es un problema de memoria perdida — el tutorial seguía presente en la
misma conversación, sin que hubiera compresión de contexto de por medio. Es una
limitación de razonamiento: el modelo no conecta espontáneamente contexto
lejano-pero-presente con la decisión inmediata que tiene enfrente.

### 4. El "plan mode" fantasma — la violación real (líneas 4298-4336)

Cuando el usuario escribe en texto plano *"remember you are in plan mode and we
are trying to build the ai form builder/contract..."*, Kimi razona
explícitamente:

> *"Also maybe we need to use EnterPlanMode? Not needed; user says already in
> plan mode."* (línea 4298)

Es decir: trata la afirmación textual del usuario como prueba suficiente de
estado, sin verificar ni llamar la tool real. A partir de ahí, decide empezar a
implementar la Fase 0 del plan (línea 4311: *"we should use TodoWrite and
begin"*), y en efecto escribe archivos reales: `README.md`, `pyproject.toml`,
`test_project_structure.py`, `ADR-0006...md`, `contract_schema.py`,
`sources.py`, `openapi_loader.py`, `exceptions.py`, múltiples `__init__.py`
(grep de Write/Edit: líneas 4518 a 7596).

**Por qué el proxy no lo bloqueó**: el gate técnico real (`plan_mode_guard.py`
en `vendor/claude-code-proxy/llm/transformers/`, que sí existe y sí bloquea
Edit/Write/Bash-de-escritura cuando `ctx.plan_mode_active == True`) depende de
que esa bandera se derive de señales reales: historial de
`EnterPlanMode`/`ExitPlanMode`, el string de sistema de Claude Code, o el
intent-classifier. Como `EnterPlanMode` nunca se llamó, y nada más disparó esas
señales para este mensaje del usuario en particular, `ctx.plan_mode_active`
nunca se activó — por lo tanto el gate nunca entró a bloquear nada. No es que
el gate fallara técnicamente; es que nunca llegó a evaluarse porque el estado
de entrada nunca se estableció.

### 5. Intento fallido de detenerse (líneas 7822-7846)

Cuando el usuario finalmente dice explícitamente "STOP the implementation",
Kimi vuelve a intentar razonar sobre `EnterPlanMode`, vuelve a fallar en
encontrarlo/invocarlo, y resuelve el conflicto respondiendo solo en texto,
pidiendo contexto al usuario — nunca activa el mecanismo real. Aun así, sí deja
de escribir código a partir de este punto (correcto, aunque por las razones
equivocadas: no porque el gate lo bloqueara, sino porque decidió, en texto,
obedecer la instrucción directa).

## ¿Se implementó código correcto durante el "plan mode" fantasma? No, en gran parte

Verificación directa contra el repo real
(`school-system/agents/operation-compiler/`), no solo contra lo que el
transcript narra:

- **Fase 0** (estructura + ADR-0006): completa y razonable. El ADR-0006 está
  bien escrito y es fiel al diseño del plan (aunque cita
  `contracts/enrollment.create.yaml` como si existiera — no existe, el
  directorio `contracts/` está vacío).
- **Fase 1** (contract schema + loaders): ~30% hecha, y las dos piezas
  centrales del diseño están mal:
  - `compiler/contract_schema.py` **no implementa el `OperationContract` real**
    que el plan especifica (falta `apiVersion`, `kind`, `metadata`,
    `informationGraph`, `executionPlan` anidado, `validationRules`,
    `renderers`). Lo que existe es un esquema plano de "operación OpenAPI
    cruda" con el nombre equivocado — quien continúe este trabajo necesita
    reescribir este archivo, no extenderlo.
  - `compiler/sources.py` **no implementa la lógica de precedencia de fuentes**
    (ORM > Pydantic > Endpoint > Docs > OpenAPI) que el propio plan marca como
    su corrección de diseño más importante. Es solo un path-walker de
    directorios.
  - **Bug reproducido en vivo**: corriendo el `openapi_loader.py` real contra
    el `backend/docs/openapi.json` real (174KB) para `create_enrollment`, el
    campo `payment_plan_id` sale faltante — exactamente el bug de "OpenAPI
    desactualizado" que el plan usa como su ejemplo canónico de por qué hace
    falta la fusión multi-fuente. Como esa fusión nunca se escribió, el único
    loader que existe reproduce el bug sin corregirlo.
  - `openapi_loader.py` no quedó roto por los 3 intentos de edit fallidos al
    final (corre sin excepciones sobre datos reales: 67 contratos, 49
    schemas), pero tiene imports muertos (evidencia de lógica a medio conectar:
    `ContractReference`, `_path_param_names`, `_resolve_ref`) y un bug real
    confirmado: los 5 endpoints sin key `security` en el spec (`/`, `/health`,
    `/login`, `/register`, `/refresh_token`) no reciben ninguna regla de
    autenticación ni de "público" — un hueco de clasificación justo en los
    endpoints más sensibles del sistema.
- **Fases 2-7** (builder, planner, runtime, renderers, servidor MCP, contenido
  real de skills, integración end-to-end): 100% sin empezar — ni siquiera
  stubs. `fast-mcp`/`fastmcp` (dependencia central del runtime) no está
  instalada en ningún lado del repo.
- **Tests**: el único test existente (`test_project_structure.py`) solo
  verifica que existan directorios (varios vacíos) — pasa 10/10 pero no
  certifica correctitud alguna. Los tests de Fase 1 que el propio plan exige
  (`test_contract_schema.py`, `test_loaders.py`) nunca se escribieron — la
  disciplina TDD que el plan mandata se abandonó después del test de
  estructura.

**Conclusión**: detener la sesión y volver a planear fue la decisión correcta.
Lo construido tiene el esqueleto correcto pero el corazón del diseño (contrato
portable + fusión de fuentes) no existe, pese a que el monólogo interno de Kimi
sugiere lo contrario en varios puntos (ej. "Write repo skills... end-to-end
integration tests and final verification" — nada de eso es real en disco).

## Por qué los sistemas de guardrail existentes no lo detectaron

| Sistema | Por qué no aplicó aquí |
|---|---|
| `.claude/hooks/plan-mode-gate.sh` | Solo inyecta texto una vez por sesión ("DEBES llamar EnterPlanMode"); no verifica ni bloquea nada — es un mandato, no un gate. Y como es client-side, tampoco puede ver ni el proxy ni el estado real del modelo. |
| `.claude/hooks/skill-load-gate.sh` | Bloquea `EnterPlanMode`/`Agent` si no se leyó ningún `SKILL.md` — no verifica que el modelo esté haciendo lo correcto dentro de plan mode, solo que "cargó algo" antes de intentar entrar. |
| `plan_mode_guard.py` (proxy, gate real y técnico) | Depende 100% de que `ctx.plan_mode_active` se derive correctamente de señales reales (llamadas a EnterPlanMode/ExitPlanMode, string de sistema de CC, o clasificador). Como Kimi nunca llamó `EnterPlanMode`, ninguna señal se activó, así que el gate nunca entró a evaluar nada — no falló, nunca se disparó. |
| `grounding_validator.py` (ADR-0031/0033) | Detecta reclamos de "fixed X" / "todos los casos cubiertos" — no tiene ningún check sobre el *estado de modo* ("¿de verdad estás en plan mode?"). Es un gap de diseño, no un bug: nunca fue construido para esto. |
| `quality_refinement.py` | Es agnóstico de proveedor por diseño (no depende de bloques `<thinking>`), y evalúa calidad del output final — no la coherencia del propio razonamiento interno turno a turno ni el estado de sesión. No es la causa aquí. |
| Tabla de routing de `AGENTS.md` / intent-bootstrap | No aplica directamente a este repo (school-system tiene su propio CLAUDE.md) — pero el patrón es el mismo: son instrucciones a nivel de prompt, sin ningún hook que verifique que el modelo realmente las siguió. |

## Nota de seguridad (hallazgo colateral, ya resuelto en esta sesión)

`bad_conversation.txt` apareció inicialmente vacío (condición de carrera de
escritura mientras se investigaba) y, al llenarse, su contenido activó el
detector de prompt-injection del harness (contenía texto en primera persona
dirigiendo a un agente lector a llamar Playwright/Serper contra
`claude-world.com`). Se verificó con el usuario: no es un ataque — es una
transcripción real que él mismo generó, y `claude-world.com` es un sitio
legítimo de tutoriales sobre Claude Code, no malicioso. El bloque con la
llamada real de Playwright y la respuesta HTML fetcheada fue removido del
archivo a pedido del usuario (queda un placeholder apuntando a este reporte).
**El hallazgo de seguridad que sí queda en pie**: es un patrón real de riesgo
que un agente termine fetcheando contenido externo con una tool real
(Playwright/WebFetch) basándose únicamente en una instrucción en texto plano
dentro de la conversación — independientemente de que en este caso el dominio
resultó ser benigno, el mecanismo (agente actúa sobre texto sin distinguir
"instrucción legítima del usuario humano" de "texto que apareció en algún punto
de la conversación") es el mismo que explotaría un ataque de prompt injection
real. Vale la pena que el equipo de ciberseguridad lo tenga documentado como
patrón a mitigar, no solo como incidente cerrado.

## Recomendaciones

### Sobre plan-mode / deferred tools (causa raíz directa)

1. **No diferir `EnterPlanMode`/`ExitPlanMode` para modelos no-Claude ya
   documentados como frágiles en este flujo** (Kimi K2, ADR-0009/0010) — cargar
   su schema completo desde el inicio de sesión en vez de dejarlos como
   deferred tools. Elimina la necesidad de que el modelo descubra `ToolSearch`
   por su cuenta, que es exactamente donde falló aquí el 100% de las veces (0
   invocaciones en 7847 líneas).
2. **Nunca aceptar una aserción textual de estado del usuario como suficiente**
   ("remember you are in plan mode") sin verificación técnica — el proxy
   debería tratar esa frase como una señal a *verificar* (¿se llamó
   `EnterPlanMode` de verdad? ¿lo confirma el clasificador?), no como un hecho
   a aceptar tal cual.
3. Comparar el texto de `school-system/CLAUDE.md` contra `ai-tooling/CLAUDE.md`
   (que ya es explícito e iterativo sobre `ToolSearch`, aparentemente motivado
   por este mismo tipo de incidente) para confirmar si la documentación ahí es
   igual de enfática. Si aun con texto igual de explícito Kimi sigue fallando,
   confirma que la solución real es técnica (no diferir la tool), no
   documental — la documentación por sí sola ya demostró no bastar.

### Sobre el problema de fondo (orquestación en espacios de búsqueda amplios)

Esta es la causa que más le preocupa al usuario a largo plazo: Kimi no falla
tanto generando/razonando código — falla decidiendo qué hacer cuando tiene que
explorar (repo, web, resultados previos) y sintetizar antes de actuar. Este
incidente lo evidencia dos veces (el loop de Playwright/BigQuery, y el fallo de
conectar el tutorial leído con la acción de `EnterPlanMode`). Direcciones de
solución propuestas — **staging para ADR, no implementadas todavía** (ver
`docs/findings/FINDINGS.md` F-001):

1. Heurística de "loop de re-intento sin progreso" generalizada a cualquier
   familia de tool (no solo `Read`, que ya cubre ADR-0009): si la misma tool o
   familia `mcp__X__*` falla o se reintenta 3+ veces seguidas sin cambio de
   estrategia, inyectar un nudge de refinamiento forzado antes de dejarlo
   seguir.
2. Nudge determinístico específico: cuando el modelo declare textualmente "no
   tengo la tool X" / "X no está disponible" para una tool que el proxy sabe
   que existe en el catálogo (aunque diferida), inyectar automáticamente
   `"X es un deferred tool — usa ToolSearch('select:X') antes de llamarla"`.
   Esto resuelve directamente la causa raíz de la sección 3 de la cronología.
3. Checkeo de grounding tipo "exploration-before-action": antes de que el
   modelo escriba/edite código basado en investigación previa (fetch web,
   lectura de repo), verificar que efectivamente citó/usó contenido de esa
   exploración en su razonamiento inmediato anterior — no solo que la
   exploración ocurrió en algún punto de la conversación (gap ya señalado en el
   propio ADR-0033: "evidence check is binary and topic-blind").
4. Checkpoint de "orientación" forzado cada N tool calls en fases de
   exploración amplia, pidiendo síntesis explícita de lo aprendido antes de la
   siguiente acción.

Nota: sobre si el modelo "no toma como contexto lo aprendido en pasos
anteriores" es responsabilidad de la gestión de memoria/contexto de Claude Code
(el harness) — la evidencia de este transcript sugiere que **no** es un
problema de memoria perdida (el tutorial seguía presente, sin compresión de por
medio, ~4000 líneas después). Es una limitación de razonamiento/atención del
modelo mismo, que no se soluciona con "más memoria" sino forzando la conexión
de forma determinística vía los nudges arriba — por eso las recomendaciones
apuntan al proxy (que sí controla qué se inyecta y cuándo) y no al harness.
