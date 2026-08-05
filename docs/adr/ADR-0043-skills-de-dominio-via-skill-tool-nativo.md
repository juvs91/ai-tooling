# ADR-0043: Skills de dominio se exponen vía el tool `Skill` nativo, no vía `Agent`-subagent

**Status:** Accepted
**Date:** 2026-08-03
**Supersedes:** —
**Superseded by:** —
**Related:** ADR-0042 (autoload de `workflow-coordinator` vía `Skill`, no `Agent`) — mismo
argumento de fondo, aplicado aquí a las ~30 skills de dominio en vez de al router.

---

## Context

Al observar que el repo hermano `wpc-backend` tiene `.claude/agents -> ../.agents/skills`
(symlink), surgió la pregunta de si ai-tooling debía replicar ese patrón y, además, si las
skills de dominio (`python-senior-backend`, `database-expert`, `architect`, `tdd-workflow`,
etc. — hoy consumidas con `Read` manual después de que `workflow-coordinator` decide cuál
aplica) debían pasar a invocarse con el tool `Skill` o con el tool `Agent`
(`subagent_type=...`).

Un primer análisis concluyó erróneamente que el symlink de wpc-backend era cosmético. Se corrigió
tras verificar la documentación oficial de Claude Code:

1. El discovery de subagents en `.claude/agents/` **es recursivo** — escanea subcarpetas, y la
   identidad del `subagent_type` viene del campo `name:` del frontmatter, no de la ruta.
2. El campo real para restringir tools en un **subagent** es `tools:` (string/list simple). Las
   ~30 skills de dominio usan `allowed-tools:` (formato YAML array, el campo correcto para el
   tool `Skill`, no para subagents) — si se registraran como subagents vía symlink, ese campo se
   ignoraría silenciosamente y el subagent heredaría todas las tools sin restricción real.
3. El tool `Skill` corre **en el mismo contexto principal por defecto** (no aislado) — solo se
   aísla con `context: fork` explícito en el frontmatter. Los subagents de `Agent`, en cambio,
   siempre corren aislados y devuelven un único bloque de texto final.
4. El tool `Skill` descubre skills únicamente desde `.claude/skills/<nombre>/SKILL.md` (o
   `~/.claude/skills/`, o plugins) — nunca desde `.agents/skills/` ni `.claude/agents/`. Ningún
   repo (ai-tooling, wpc-backend) usa hoy esa convención para las skills de dominio; ambos usan
   `Read` manual sobre una ruta arbitraria fuera de ella.

Esto último es clave: el destino que de verdad da control nativo (grants de tools por turno,
aislamiento opt-in, override de modelo) sin sacrificar la interactividad es `.claude/skills/`,
no `.claude/agents/`.

### Piloto empírico ejecutado (dos rondas, misma sesión)

**Ronda 1** — symlinks iniciales: `.claude/skills -> ../.agents/skills` (árbol completo) y
`.claude/agents/tool-writer-pilot -> ../../.agents/skills/core/tool-writer`. Al invocar de
inmediato `Skill(skill="tool-writer")`, `Skill(skill="core")` y
`Agent(subagent_type="tool-writer")`, los tres fallaron con la lista fija que la sesión ya tenía
cargada desde su inicio — conclusión provisional: discovery estático por sesión.

**Ronda 2** — minutos después, en la MISMA sesión, sin reiniciar nada: apareció un
system-reminder no solicitado — *"New agent types are now available for the Agent tool:
tool-writer"* — y `Agent(subagent_type="tool-writer")` funcionó (`pong`). Esto refuta la
conclusión de la ronda 1 para subagents: **el discovery de `Agent`/subagents sí se refresca a
mitad de sesión** (mecanismo exacto no documentado — posiblemente ligado a algún evento interno,
no a un timer fijo), y confirma que el anidamiento de 2 niveles
(`.claude/agents/tool-writer-pilot -> .../core/tool-writer/SKILL.md`) **sí es descubierto**.

Con ese refresh confirmado, se probó `Skill(skill="tool-writer")` otra vez → **sigue fallando**
(`Unknown skill: tool-writer`). Se sospechó que el problema era el anidamiento (2 niveles bajo el
symlink de árbol completo), así que se reemplazó por una estructura plana real: se borró el
symlink de árbol completo y se creó `.claude/skills/tool-writer -> ../../.agents/skills/core/tool-writer`
(un nivel exacto, `.claude/skills/<nombre>/SKILL.md`, igual al ejemplo de la documentación oficial).
`Skill(skill="tool-writer")` **sigue fallando** incluso plano. Conclusión: en esta sesión, el
discovery de `Skill` específicamente **no se ha refrescado todavía** (a diferencia de `Agent`,
que sí lo hizo) — no se puede distinguir todavía si es solo cuestión de tiempo/otro trigger, o si
`Skill` tiene una regla de discovery genuinamente más estricta que `Agent`. **Esto sigue
pendiente de una sesión nueva.**

**Ronda 3** — minutos después todavía, sin ninguna acción explícita de "refresh": apareció un
segundo system-reminder no solicitado — *"The following skills are available for use with the
Skill tool: tool-writer"* — y `Skill(skill="tool-writer")` funcionó, devolviendo el contenido
completo del `SKILL.md` (con "Base directory for this skill:
/Users/jeguzman/ai-tooling/.claude/skills/tool-writer"). **Confirmado: el tool `Skill` sí
descubre skills en `.claude/skills/<nombre>/SKILL.md` cuando la estructura es plana (un nivel).**
No quedó una prueba limpia de si la estructura ANIDADA (symlink de árbol completo,
`.claude/skills -> ../.agents/skills`, con `SKILL.md` 2 niveles más abajo) también hubiera
funcionado — se reemplazó por la estructura plana antes de que ese refresh ocurriera, así que
esa variante específica queda sin confirmar (aunque es razonable esperar el mismo resultado dado
que ambos mecanismos comparten el mismo tipo de refresh diferido).

**Conclusión revisada sobre timing:** ni `Agent`/subagents ni `Skill` tienen discovery
estrictamente fijo al inicio de sesión — ambos se refrescan de forma diferida y asíncrona durante
la sesión (el trigger exacto no está documentado ni fue posible aislarlo), simplemente en
momentos distintos entre sí (Agent se refrescó primero, Skill unos minutos después). Para
efectos prácticos: **no hace falta una sesión nueva para confirmar discovery** — solo esperar
a que el refresh ocurra dentro de la sesión actual.

**Resuelto — enforcement de `allowed-tools:` en subagents:** una vez refrescado (mismo patrón
diferido: primero "not found", minutos después disponible bajo el nombre real del frontmatter,
`python-senior-backend`, no el nombre del symlink), se invocó
`Agent(subagent_type="python-senior-backend")` pidiéndole que reportara sus tools disponibles sin
usarlas. Reportó acceso a `Agent`, `Artifact`, `Bash`, `Edit`, `Read`, `ReportFindings`,
`ShareOnboardingGuide`, `Skill`, `ToolSearch`, `Write` — varios de estos (`Agent`, `Artifact`,
`ReportFindings`, `ShareOnboardingGuide`, `Skill`, `ToolSearch`) están **fuera** del
`allowed-tools:` declarado (`Read, Edit, Write, Bash, Glob, Grep, mcp__atlassian__*,
mcp__bitbucket__*, mcp__squit-remote__*`). **Confirmado: `allowed-tools:` no restringe nada en
contexto de subagent** — la hipótesis del cuerpo de este ADR era correcta.

**Corrección adicional sobre qué es `allowed-tools:` incluso para el tool `Skill` (no solo para
subagents):** releyendo la documentación citada por `claude-code-guide` con más cuidado —
*"Tools Claude can use **without asking permission** during the turn that invokes this skill"* —
`allowed-tools:` en contexto de `Skill` es un **grant de auto-aprobación** (evita el prompt de
permiso para esos tools durante ese turno), **no una allowlist que bloquee el resto**. Ni `Read`
ni `Skill` restringen de verdad qué tools puede usar el agente principal después de cargar el
contenido — solo un subagent con el campo `tools:` (correctamente nombrado, no `allowed-tools:`)
impondría una restricción real, y eso exige el modelo aislado de `Agent` con su correspondiente
pérdida de interactividad. **Esto corrige una sobreventa en la sección "Decision"/"Consequences"
de arriba: migrar a `.claude/skills/` no da "enforcement" de tools — da discoverability nativa,
ejecución en el mismo contexto, y auto-aprobación de permisos sin fricción. Ninguna de las dos
rutas (`Skill` ni `Agent`) da hoy una restricción real de tools sin sacrificar algo (interactividad
en el caso de `Agent`, o directamente no dar restricción en el caso de `Skill`).**

**Actualización:** los tres symlinks de piloto SÍ se eliminaron después (ver Consequences) — la
frase original decía que se dejarían para verificación futura, pero se confirmó el enforcement
en esta misma sesión (ver arriba) antes de que hiciera falta esperar, así que se limpiaron de
inmediato tras confirmar el hallazgo. Durante la ventana en que existieron, uno de ellos
(`tool-writer-pilot`) fue recogido y usado por un agente completamente distinto sin relación con
esta investigación — ver Addendum 2, Prueba 2, y la discusión de riesgo de contaminación que
motivó extender `adr-gate.sh`.

## Decision

**No migrar las ~30 skills de dominio a `.claude/skills/` ni a `.claude/agents/`. Se mantiene el
statu quo (`Read` manual tras el routing de `workflow-coordinator`), sin cambios a `CLAUDE.md`,
`AGENTS.md` ni al protocolo de `workflow-coordinator`.**

El piloto (ver arriba) sí validó que el tool `Skill` nativo funciona vía `.claude/skills/<nombre>/SKILL.md`
(estructura plana) y que el discovery de ambos mecanismos (`Skill`, `Agent`) se refresca de forma
diferida durante la sesión — pero la razón original para migrar (creer que `allowed-tools:` daría
enforcement real de tools) **resultó ser falsa**: ese campo es solo un grant de auto-aprobación de
permisos por turno, no una restricción. Con esa corrección, el beneficio de migrar se reduce a
"discoverability nativa + cero fricción de permisos", y el costo real es alto: automatizar ~30
symlinks en `sync_skills.sh` (o quedar expuestos a que una skill nueva en `.agents/skills/` nunca
se refleje en `.claude/skills/`), reescribir `CLAUDE.md`/`AGENTS.md`/el protocolo de
`workflow-coordinator` para invocar `Skill(skill=...)` en vez de `Read`, y absorber el riesgo de
UX del discovery diferido (una skill recién creada/renombrada puede fallar con "Unknown skill"
durante minutos en la misma sesión — algo que `Read` nunca sufre, porque no depende de ningún
registro cacheado). El sistema actual con `Read` ya funciona y nunca tuvo el problema de
ambigüedad de tool namespace que sí rompió el mandato del router con `Agent` (ADR-0012/0042),
porque `Read` no es un mandato exclusivo que compita con ninguna convención del sistema.

**Decisión tomada explícitamente por el usuario** tras ver el costo/beneficio corregido: no vale
la pena el costo de reescritura + mantenimiento para un beneficio que quedó en "conveniencia",
no en fiabilidad ni seguridad.

### Alternatives Considered

- **A. Statu quo — `Read` manual tras routing de `workflow-coordinator` (elegida).** Sin
  discoverability nativa ni auto-aprobación de permisos, pero sin costo de migración, sin
  dependencia de un discovery diferido no documentado, y sin superficie nueva que mantener
  sincronizada.
- **B. `Agent`-subagent vía `.claude/agents/` (symlink, como wpc-backend).** Rechazada: mismo
  problema de ambigüedad de tool namespace que rompió el mandato del router en ADR-0012/0042
  (multiplicado por ~30 `subagent_type` compitiendo con `Explore`), más pérdida de interactividad
  (contexto aislado, respuesta final única), más `allowed-tools:` confirmado inerte en ese
  contexto (ver piloto).
- **C. `Skill` nativo vía `.claude/skills/`.** Funciona (piloto confirmado), preserva
  interactividad, pero el beneficio real (discoverability + auto-aprobación de permisos) no
  compensa el costo de reescritura de 3 documentos rectores + mantenimiento de symlinks +
  discovery diferido. Rechazada por decisión explícita del usuario, no por falla técnica.

## Consequences

**Positivo (de NO migrar):**
- Cero costo de reescritura: `CLAUDE.md`, `AGENTS.md` y el protocolo de `workflow-coordinator`
  quedan intactos.
- Sin superficie nueva que mantener sincronizada (0 symlinks nuevos que puedan quedar
  desactualizados respecto a `.agents/skills/`).
- El mecanismo actual (`Read`) es instantáneo y predecible — no hereda el discovery diferido
  (confirmado empíricamente: minutos de latencia) que sí afecta a `Skill` y `Agent`.
- Ninguna de las alternativas evaluadas daba el beneficio que originalmente se buscaba
  (enforcement real de tools) — la corrección de esa premisa fue la que inclinó la decisión.

**Negativo / trade-off aceptado:**
- Las skills de dominio siguen sin aparecer en el listado nativo de skills/agents disponibles —
  el usuario/modelo depende de que `workflow-coordinator` calcule y comunique la ruta correcta de
  `Read`.
- Si en el futuro se necesita restricción REAL de tools por skill (no solo discoverability), la
  única vía confirmada es `Agent`-subagent con el campo `tools:` (no `allowed-tools:`) — eso
  implica aceptar la pérdida de interactividad y reintroducir el riesgo de ambigüedad de tool
  namespace ya visto en ADR-0012/0042. Evaluar caso por caso si esa necesidad aparece.

**Limpieza post-decisión:** los tres symlinks de piloto (`.claude/skills/tool-writer`,
`.claude/agents/tool-writer-pilot`, `.claude/agents/python-senior-backend-pilot`) se eliminaron
del working tree una vez tomada la decisión de no migrar — ya cumplieron su propósito
(evidencia empírica documentada arriba) y dejarlos habría sido un artefacto sin uso.

## Fuera de alcance (no resuelto por este ADR)

1. Rotación/purga del token de Kraken expuesto en el historial de git de `wpc-backend`
   (commit `e72e02e6`, 2026-06-15) — otro repo, corresponde al equipo de seguridad.
2. Loophole de relevancia en `adr-gate.sh` (bypass si existe *cualquier* ADR nuevo, sin verificar
   que sea el relevante) — corregido por separado, ver commit/cambio en
   `.claude/hooks/adr-gate.sh` del mismo lote de trabajo que este ADR.
3. Revisión general de hooks `PreToolUse exit 2` bajo `bypassPermissions` (follow-up ya anotado
   en ADR-0012/ADR-0042) — requiere revisión de seguridad propia (NIST CSF/ISO 27001/IEC 62443).
4. Si en el futuro se necesita restricción real de tools por skill vía `Agent`-subagent con el
   campo `tools:` correcto — no evaluado aquí, requeriría su propio ADR.

## Addendum — dos hallazgos post-decisión, mismo hilo de trabajo

**1. `Read` no implica adopción de persona.** Se demostró empíricamente (leer
`core/tool-writer/SKILL.md` sin ninguna instrucción explícita de "síguelo" no cambió el
comportamiento del agente principal — siguió respondiendo igual que antes). Ni el wrapper
(`.claude/commands/workflow-coordinator.md`) ni el `SKILL.md` completo tenían una línea
imperativa de "tras el Read, adopta esto como protocolo vinculante" — solo un banner cosmético de
ejemplo. Se agregó esa línea explícita al wrapper (ver `Files Changed`) y se validó en caliente:
con la línea puesta, al rutear un pedido real hacia `tool-writer`, el agente sí reconoció que
debía seguir su Phase 1 (consultar Architect antes de escribir código) en vez de saltar directo a
programar. Esto NO es enforcement duro (sigue siendo instrucción de texto, misma categoría que el
resto de mandatos de `CLAUDE.md`) — se descartó explícitamente ir a enforcement real (`Agent`-subagent
con `tools:` correcto) por ser el mismo costo ya rechazado en la sección Decision de arriba, y
porque un hook no puede verificar semánticamente "¿adoptó el modelo la persona?".

**2. Costo real de invocar `Skill(workflow-coordinator)` más de una vez por sesión.** El fix
stateful de `skill-autoload.sh` (ADR previo) garantiza que el *reminder automático* no se repita,
pero no impide que el modelo mismo re-invoque `Skill(skill="workflow-coordinator")` a mitad de
sesión — cada invocación reinyecta el wrapper completo (medido: 142 líneas, 9,011 caracteres,
≈2,300-2,700 tokens). Se confirmó en vivo, contra la sesión real, que el hook seguía silencioso
(marcador ya escrito desde el inicio de la sesión) — el mecanismo de una-vez-por-sesión sigue
intacto. Pero se detectó una re-invocación manual innecesaria del propio agente durante esta
misma sesión (para un test de validación), pese a que la tabla de routing ya estaba en su
contexto — un ~2,500 token de costo evitable. Se agregó una aclaración a `CLAUDE.md` (ambos
repos, `ai-tooling` y `wpc-backend`) indicando explícitamente no re-invocar
`Skill(workflow-coordinator)` si la tabla ya está en contexto; comparar directo contra ella y
solo hacer `Read` del `SKILL.md` de destino.

## Addendum 2 — validación con subagentes limpios (sin sesgo del hilo principal)

Se corrieron 3 pruebas con subagentes `general-purpose` frescos (sin memoria de esta conversación),
para separar "¿el fix de adopción funciona?" de "¿yo mismo, sesgado por haber diseñado el fix, lo
veo funcionar porque lo espero?".

**Prueba 1 — sin forzar nada** (pedido real: diseñar un schema SQL, sin mencionar skills):
el agente **nunca invocó `Skill(workflow-coordinator)`** — leyó `AGENTS.md` directo con `Read` por
su cuenta, encontró y leyó `database-expert/SKILL.md`, y lo usó **"como referencia de fondo, no
como protocolo vinculante"** (cita textual del agente al pedírsele que reportara su propio
proceso). Confirma el problema original una vez más, sin sesgo.

**Prueba 2 — delegación accidental vía `Agent`-subagent** (ver Addendum 1 más arriba, incidente de
contaminación con `tool-writer-pilot`): el agente sí siguió sustancialmente la persona (ADR, tests,
índice de tools) — pero por aislamiento total de contexto (no hay nada más compitiendo), no por el
mandato de texto del wrapper, que ni siquiera se cargó por ese camino.

**Prueba 3 — forzando explícitamente la entrada** (se le instruyó al agente fresco que su primera
tool call debía ser `Skill(workflow-coordinator)`, aceptando ese sesgo puntual para poder aislar
la pregunta real: ¿la línea de adopción, una vez que el wrapper SÍ se carga, cambia algo?):
- El agente confirmó haber visto la línea agregada textualmente en el resultado del `Skill` tool.
- **Sí leyó completo y se enganchó en serio con `tool-writer/SKILL.md`** (no lo saltó ni lo trató
  como decorativo) — este parece ser el efecto real y verificable de la línea agregada.
- **NO seguyó literalmente el protocolo cuando este chocaba con la realidad del repo**: no
  consultó un "Architect Agent" (no existe), no escribió el ADR *antes* del código como manda
  Phase 1 (lo escribió después, como registro retrospectivo de una decisión ya verificada), y
  omitió el push a un repo `deagentic` inexistente (confirmado con `git remote -v`).
- Por su propio reporte honesto: esa desviación no vino de la línea de adopción, vino de
  encontrar el precedente real `ADR-0045` (mismo dilema, ya resuelto) más la cultura general de
  `CLAUDE.md` de verificar contra el repo real antes de actuar — no de ignorar el mandato.

**Conclusión calibrada:** el fix cumple su propósito angosto — evita que el agente descarte o
ignore el `SKILL.md` leído — sin sobre-extenderse a obediencia ciega de contenido roto o
aspiracional, que habría sido un resultado *peor*, no mejor. No es un fix que garantice adopción
total palabra por palabra; es un fix que garantiza que el contenido se toma en serio como punto de
partida. Eso es exactamente lo que se necesitaba, ni más ni menos.

**Gap real, distinto y NO resuelto por este ADR:** las pruebas 1 y 2 muestran que, sin forzarlo
explícitamente, el mandato de `CLAUDE.md` (disparado por el hook `skill-autoload.sh` sobre
`UserPromptSubmit`) probablemente **nunca se activa para subagentes lanzados vía `Agent` tool** —
ese hook parece limitarse a la sesión interactiva principal. Cualquier `Agent(subagent_type=...)`
que yo mismo dispare dentro de una sesión (Explore, general-purpose, Plan, etc.) corre con su
propio juicio, sin pasar por `workflow-coordinator`, salvo que se le instruya explícitamente
(como en la Prueba 3) — lo cual reintroduce el mismo sesgo que se quería evitar. No hay una forma
limpia de verificar o corregir esto desde dentro de una sesión ya iniciada (el hook en cuestión no
es inspeccionable ni disparable a demanda para un subagente). Se deja documentado como límite
conocido, no como algo a "arreglar con más texto" — ya se demostró en este mismo hilo que el
enforcement por hook es frágil (`skill-load-gate.sh` bajo `bypassPermissions`, el loophole de
`adr-gate.sh`) y no hay evidencia de que forzarlo vía texto en `CLAUDE.md` cambie el comportamiento
de hooks que no se disparan en ese contexto.

## Addendum 3 — limpieza de referencias rotas descubiertas por la Prueba 3

La Prueba 3 del Addendum 2 expuso contenido aspiracional/roto heredado de la plantilla genérica de
origen (misma familia que el `~/.keystone/vaults.json` encontrado antes en `wpc-backend`):
`tool-writer/SKILL.md` mandaba consultar un "Architect Agent" inexistente como `Agent`-subagent,
seguir una jerarquía `tools/[domain]/[subdomain]/` no usada en este repo, y hacer auto-push a un
repo `deagentic` que nunca existió (`git remote -v`: solo `deacero`/`personal`). Se corrigió:

- `tool-writer/SKILL.md`: Phase 1 ahora referencia el skill real `architect` (vía `Read`/routing,
  no `Agent`-subagent); Phase 2 usa la convención real plana de `tools/<tool_name>.py`; **Phase 4
  ya no manda commitear automáticamente** (contradecía la regla de "nunca commitear sin pedido
  explícito" — este era un riesgo real, no solo cosmético, ahora que el mandato de adopción del
  Addendum 2 hace más probable que se siga el protocolo al pie de la letra); Phase 5 aclara que
  `ai-tooling` es el destino upstream real (si aplica) y que la contribución nunca es automática.
- `learning-protocol/SKILL.md`: mismas correcciones (referencia a `tool-writer` como skill real,
  no Agent; upstream = `ai-tooling`, nunca automático).
- `gitops-expert/SKILL.md`: referencia a `deagentic/Skills` corregida a `ai-tooling`. La referencia
  su propia revisión.
- `evals/evals.json` de `learning-protocol`: fixture de test actualizado (`deagentic` → `ai-tooling`).

También se corrigió `database-expert/SKILL.md`: la sección "SQLite-Specific Notes (for this
project)" afirmaba falsamente que este repo usa SQLite para `~/.keystone/vaults.json` (mismo
origen heredado). Se reemplazó por una nota honesta de qué DBs usa realmente `ai-tooling`
(AlloyDB/CloudSQL vía MCP; SQLite solo incidental en artefactos de `software-archeologist`),
conservando el contenido técnico genérico de SQLite como referencia no atada a este proyecto.

El usuario pidió una segunda pasada explícita por "keystone" (búsqueda case-sensitive previa se
había quedado corta — "Keystone" con mayúscula no matcheaba). Búsqueda case-insensitive en todo
el repo encontró 4 archivos adicionales, todos usando "Keystone" como nombre de un proyecto
ilustrativo (app NFC de gestión de vaults/tarjetas) en ejemplos pedagógicos, no como instrucción
viva rota (a diferencia de `deagentic`):
- `design/ux-expert/SKILL.md`: sección "UX Patterns for This Project" (falso — `ai-tooling` no
  tiene UI de escritorio) reencuadrada como ejemplo genérico explícito, nombre de marca genericizado.
- `software/quality/bdd-writer/SKILL.md`: "Keystone card" → "NFC card" en 2 ejemplos Gherkin/pytest-bdd.
- `software/discovery/unknown-domain-protocol/SKILL.md`: "Keystone card" → "test card" en un
  ejemplo de `[HARDWARE ACTION NEEDED]`.
- `software/architecture/adr-writer/SKILL.md`: "Keystone project" → "a new project" en el ejemplo
  de "Seed ADRs"; se aclaró explícitamente que es un ejemplo genérico, no ligado a `ai-tooling`.

**Nota sobre `.agents/_repo/`:** es un clon git SEPARADO (remote propio:
`git@github.com:juvs91/ai-tooling.git`) usado como caché por `sync_skills.sh` — no se edita
directamente porque cualquier cambio ahí se perdería en el próximo fetch y no afecta lo que
realmente se consume (`.agents/skills/`). Su copia de `database-expert/SKILL.md` sigue mostrando
el texto viejo porque refleja el último estado pusheado a GitHub, no el working tree local —
se actualizará sola la próxima vez que se corra `sync_skills.sh` después de commitear y pushear
estos cambios.

No se hizo auditoría exhaustiva de las ~75 skills restantes buscando otros patrones similares
(more allá de "keystone"/"deagentic") — se corrigieron los 8 archivos que salieron a la luz
durante este hilo (`tool-writer`, `learning-protocol`, `gitops-expert`, `database-expert`,
`ux-expert`, `bdd-writer`, `unknown-domain-protocol`, `adr-writer`). Queda como posible follow-up
una pasada dedicada. Nota colateral, no corregida: `unknown-domain-protocol/SKILL.md` referencia
5 archivos locales inexistentes (`nfc/iso-15693.md`, `nfc/acr122u-commands.md`,
`nfc/rf-field-timing.md`, `smartcard/pcsc-api.md`, `smartcard/apdu-reference.md`) — mismo origen
heredado, fuera del alcance de "eliminar referencias de keystone".

`check_skill_frontmatter.py` sigue en verde (75/75) tras todos estos cambios de contenido.

## Addendum 4 — `sync_skills.sh` puede sobreescribir ediciones locales sin commitear (hallazgo crítico)

A mitad de esta misma sesión, `.agents/.last_sync` se actualizó solo (`sync_skills.sh` corre como
hook `UserPromptSubmit`, throttle ~24h — ver `.claude/settings.json:283`) y **sobreescribió 5 de
los archivos ya corregidos** con el contenido cacheado en `.agents/_repo` (que refleja el último
estado pusheado a GitHub, no el working tree local): `core/tool-writer/SKILL.md`,
`core/orchestrator/SKILL.md`, `core/learning-protocol/SKILL.md`,
`core/learning-protocol/evals/evals.json`, y resucitó `archaeology/squit/SKILL.md` (que se había
borrado). Los 5 se repararon de nuevo tras detectarlo (mismos fixes, ver Addendums 1 y 3).

**Confirmado benigno, no relacionado:** `infrastructure/gitops-monorepo/SKILL.md` también cambió
en el mismo sync — corrigió una referencia `ADR-0008` → `ADR-0044` (el usuario había renumerado
ese ADR en otro momento). Ese cambio es correcto y se dejó intacto.

**Implicación operativa real, no solo de esta sesión:** cualquier edición de contenido dentro de
`.agents/skills/` que quede sin commitear por más de lo que dure el throttle de
`sync_skills.sh` corre riesgo real de pérdida silenciosa — no hay ningún aviso al usuario cuando
esto pasa, solo el timestamp de `.last_sync` cambia. Mitigación práctica: commitear ediciones a
`.agents/skills/` pronto después de hacerlas, no dejarlas abiertas en el working tree por sesiones
largas. No se implementó ningún fix estructural para esto (ej. deshabilitar el hook mientras se
edita) — queda como riesgo conocido a mitigar operativamente, no técnicamente, en esta ronda.

## Files Changed

- `docs/adr/ADR-0043-skills-de-dominio-via-skill-tool-nativo.md` (este archivo)
- `docs/adr/index.md` (nueva entrada, ADR-0043)
- `.claude/hooks/adr-gate.sh` (fix del loophole de relevancia + guard nuevo sobre
  `.claude/agents/` y `.claude/skills/`, ver Addendum 1)
- `.claude/commands/workflow-coordinator.md` (línea de adopción explícita post-`Read`; fila de
  routing de `squit` eliminada, ver abajo)
- `CLAUDE.md` (ai-tooling y wpc-backend — aclaración de costo, no re-invocar
  `Skill(workflow-coordinator)` si la tabla ya está en contexto)
- `AGENTS.md` (fila de routing de `squit` eliminada de la tabla principal y de la tabla de
  dominios; `squit` ya no existe como skill — ver más abajo)
- `.agents/skills/skills.md` (entrada de `squit` eliminada del índice)
- `.agents/skills/archaeology/squit/SKILL.md` — **eliminado por completo** (sin frontmatter,
  detectado por `check_skill_frontmatter.py`; se optó por borrar en vez de corregir). Directorio
  `archaeology/` eliminado por quedar vacío.
- `.agents/skills/core/orchestrator/SKILL.md` — frontmatter YAML agregado (`name`, `description`,
  `version`); antes no tenía ninguno.
- `.agents/skills/frontend/frontend-analysis/SKILL.md` — frontmatter YAML agregado; antes no
  tenía ninguno.
- `tools/check_skill_frontmatter.py` + `tools/tests/test_check_skill_frontmatter.py` (17 tests) —
  checker nuevo, usado para encontrar los 3 archivos sin frontmatter de arriba.
- `docs/adr/ADR-0045-check-skill-frontmatter-tool.md` — ADR de ese tool.
- `tools/check_adr_sections.py` + `tools/tests/test_check_adr_sections.py` (20 tests) — checker de
  secciones obligatorias en ADRs, generado como parte de la Prueba 3 del Addendum 2 (delegación
  real, no simulada). Encontró 7 ADRs con secciones "faltantes" en el sentido estricto, aunque 6
  son solo el mismo contenido en español (`Estado/Fecha/Contexto/Decisión`) — único gap genuino:
  ADR-0022 sin campo `Date`/`Fecha` propio.
- `docs/adr/ADR-0046-check-adr-sections-tool.md` — ADR de ese segundo tool.
- `docs/tools/index.md` — catálogo de tools, creado durante este trabajo (no existía antes);
  documenta `check_adr_gate.py`, `install_hooks.sh`, `check_skill_frontmatter.py` y
  `check_adr_sections.py`.
- Symlinks de piloto (`.claude/skills/tool-writer`, `.claude/agents/tool-writer-pilot`,
  `.claude/agents/python-senior-backend-pilot`) — creados, usados para las pruebas, y eliminados.
  Sin rastro final en el working tree.

**Nada de lo anterior está commiteado** — todo vive sin commitear en el working tree, a la espera
de decisión del usuario sobre si commitear (y en cuántos commits).

## Verification

1. **Pendiente, bloqueante para decidir el plan de migración real:** en la próxima sesión nueva
   de Claude Code sobre este repo, al inicio, verificar si `tool-writer` aparece en el listado de
   skills/agents disponibles; si no aparece, repetir `Skill(skill="tool-writer")` y
   `Agent(subagent_type="tool-writer")` manualmente y registrar el resultado exacto aquí (editar
   este ADR con el resultado, no crear uno nuevo).
2. Si `Skill(skill="tool-writer")` funciona pese al nesting de `core/tool-writer/SKILL.md` (2
   niveles bajo `.claude/skills/`): symlink simple `.claude/skills -> ../.agents/skills` basta
   para la migración completa, sin reestructurar `.agents/skills/`.
3. Si no funciona: la migración real requiere aplanar cada skill a
   `.claude/skills/<nombre>/SKILL.md` (un symlink por skill, no un symlink de árbol completo).
4. Confirmar que `Agent(subagent_type="workflow-coordinator")` NO se ve afectado por el nuevo
   symlink `.claude/agents/tool-writer-pilot` — el router sigue funcionando como en ADR-0042.
