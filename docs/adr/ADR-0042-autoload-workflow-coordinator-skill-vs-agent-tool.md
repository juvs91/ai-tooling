# ADR-0042: Autoload de workflow-coordinator vía Skill tool, no Agent tool

**Status:** Accepted
**Date:** 2026-08-03
**Supersedes:** —
**Superseded by:** —
**Related:** ADR-0022 (workflow-coordinator genera task-scope.json) — concern distinto,
ver nota en Context.

---

## Context

`CLAUDE.md` obliga a que el primer tool call de cada sesión cargue `workflow-coordinator`,
que lee la tabla de routing de `AGENTS.md` y decide qué skill de dominio cargar. El mecanismo
original usaba `Skill(skill="workflow-coordinator")`, disparado por `skill-autoload.sh`
(`UserPromptSubmit`) en **cada turno**, sin estado — esto reinyectaba el wrapper de routing en
el contexto principal de forma acumulativa (O(N turnos)).

Se probaron dos cambios en secuencia (validados primero en el repo hermano `wpc-backend`, que
usa el mismo mecanismo sincronizado vía `sync_skills.sh`):

1. **Fix stateful de `skill-autoload.sh`**: el reminder solo se emite **una vez por sesión**,
   marcando la sesión en `.claude/sessions/`. Este fix ya existía en `ai-tooling` (es la fuente
   canónica) y se portó desde aquí hacia `wpc-backend`. Convierte la acumulación de O(N turnos)
   a O(1) por sesión, independientemente de qué tool cargue el skill.
2. **Migración de `Skill` a `Agent(subagent_type="workflow-coordinator")`**, buscando contexto
   aislado y modelo Haiku para la decisión de ruteo. Se probó primero en `wpc-backend` y se
   replicó aquí. Introdujo dos problemas reales, confirmados empíricamente:
   - **Ambigüedad de instrucción:** `Agent` es el mismo tool que la guía genérica del sistema
     usa para exploración de código ("para exploración amplia, usa Agent con
     subagent_type=Explore"). Ante un prompt real de investigación de código, el modelo ignoró
     el mandato de `CLAUDE.md` y llamó `Explore` directo, saltándose `workflow-coordinator` por
     completo. `Skill` nunca tuvo esta ambigüedad — ningún otro sistema compite por ese tool.
   - **Dependencia de enforcement inerte bajo bypass mode:** para evitar un deadlock (el hook
     `skill-load-gate.sh` bloquea `Agent`/`EnterPlanMode` hasta leer un SKILL.md, y
     `workflow-coordinator` es quien decide cuál leer), se agregó una excepción por
     `subagent_type`. Se confirmó en `wpc-backend` (que corre con `defaultMode: bypassPermissions`
     en dev local) que ese hook `PreToolUse` con `exit 2` no bloquea bajo bypass mode — probado
     de forma aislada (bloquea correctamente) vs. en sesión real bajo bypass (no bloquea) vs. en
     sesión real sin bypass (bloquea). `Skill` nunca dependió de este hook.

**Nota sobre ADR-0022 (relación, no superposición):** ADR-0022 documenta que
`workflow-coordinator` escribe `.claude/task-scope.json` como side-effect de su clasificación de
intent. ADR-0041 (posterior) describe que esa responsabilidad de scope-por-sesión ya la asume
`intent-bootstrap.sh` (bash puro, sin depender de Skill/Agent tool ni de instruction-following
del modelo). Este ADR **no toca esa responsabilidad** — `intent-bootstrap.sh` sigue siendo
independiente y sigue corriendo (ver `settings.json`, hook separado). Este ADR es exclusivamente
sobre **qué tool invoca la capacidad de ruteo semántico de workflow-coordinator** (elegir qué
skill de dominio cargar), no sobre generación de `task-scope.json`.

Adicionalmente se confirmó que el documento detallado
(`.agents/skills/workflow/workflow-coordinator/SKILL.md`, 846 líneas) **nunca se cargó
automáticamente** — el Skill tool siempre resolvió contra el wrapper delgado
`.claude/commands/workflow-coordinator.md` (~90 líneas); el documento de 846 líneas solo se
carga vía `Read` explícito. Ese documento tenía ~55-65% de contenido semánticamente duplicado
(la misma tabla de routing de `AGENTS.md` re-narrada 4-5 veces) más una sección totalmente
muerta ("Enhanced Commands vs Skills") que referencia comandos y paths que no existen en el
repo `wpc-backend` de origen.

## Decision

Revertir el mandato de `Agent` a `Skill(skill="workflow-coordinator")`, manteniendo el fix
stateful de `skill-autoload.sh`, y recortar `.agents/skills/workflow/workflow-coordinator/SKILL.md`
de 846 a 287 líneas eliminando la duplicación semántica y la sección muerta.

Se conserva el subagente `Agent`-compatible de 36 líneas
(`.claude/agents/workflow-coordinator.md`) como *capability* manual opcional — no se borra, pero
deja de ser invocado automáticamente. Se aclaró su `description` de frontmatter para dejar
explícito que no es el mecanismo default.

### Alternatives Considered

- **A. `Skill` tool + autocarga stateful (elegida).** Wrapper delgado, disparado una sola vez
  por sesión. Sin ambigüedad de tool, sin dependencia de `skill-load-gate.sh`.
- **B. `Agent(subagent_type="workflow-coordinator")` + refuerzo de texto en `CLAUDE.md`.**
  Rechazada: el historial ya demostró que el fallo no es de "wording débil" sino estructural —
  dos instrucciones compitiendo por el mismo tool namespace. Reforzar texto es una apuesta
  empírica sin garantía verificable sobre una instrucción base del sistema que no controlamos.
- **C. Híbrido:** `Skill` como mandato default, con delegación opcional a `Agent` para casos
  puntuales. Adoptada en forma mínima — el subagente de 36 líneas se conserva como capability,
  sin invocación automática.
- **D. Trimming del `SKILL.md` de 846 líneas.** Aplicada como complementaria a A.

### Sobre acumulación de tokens y prompt caching

Con el fix stateful, el bloque de `workflow-coordinator` se inserta una sola vez por sesión,
cerca del inicio de la conversación — no en cada turno (O(1) por sesión, no O(N turnos)). Por
estar en una posición temprana y estable, cae dentro del prefijo que el prompt caching de
Anthropic puede cachear: los turnos siguientes de la misma sesión pagan tarifa de "cache read"
por ese bloque, no tarifa completa de input. No se requiere ningún mecanismo de caching
adicional — es consecuencia directa de que el mandato dispare una sola vez, en una posición
temprana y estable.

## Consequences

**Positivo:**
- Elimina la ambigüedad de instrucción de forma categórica, no probabilística.
- Elimina la dependencia del mandato en `skill-load-gate.sh` y, por extensión, en el enforcement
  de hooks `PreToolUse` bajo cualquier modo de permisos.
- Resuelve una contradicción preexistente: el header de `AGENTS.md` nunca fue migrado a `Agent`
  y ya decía "usa Skill tool" — con este revert ambos quedan consistentes.
- `SKILL.md` pasa de 846 a 287 líneas (~66% de reducción), reduciendo el costo de los `Read`
  explícitos ocasionales y el riesgo de desincronización con `AGENTS.md`.
- Permite retirar de `skill-load-gate.sh` la excepción por `subagent_type`, que era un loophole
  de bypass sin verificación real una vez que nada en el flujo automático vuelve a usar `Agent`
  para esto.
- No interfiere con `intent-bootstrap.sh` / `task-scope.json` (ADR-0022, ADR-0041) — ese
  mecanismo es independiente y sigue funcionando igual.

**Negativo / limitaciones:**
- El wrapper de ~90 líneas se recarga una vez por sesión en el contexto principal (en vez de las
  ~3 líneas que devolvía el subagente aislado). Costo aceptado por ser bajo, acotado a una vez
  por sesión, y cacheable — a cambio de confiabilidad categórica.
- El subagente de 36 líneas queda como capability sin uso automático — costo cero mientras nadie
  lo invoque, pero requiere que un mantenedor futuro sepa que existe y por qué no es el default.

## Fuera de alcance (follow-ups, no resueltos por este ADR)

1. **Inercia de hooks `PreToolUse exit 2` bajo `bypassPermissions`** (confirmada en
   `wpc-backend`, no verificada aún en el entorno de `ai-tooling`): potencialmente afecta a
   varios hooks bloqueantes más allá de `skill-load-gate.sh`. Requiere revisión de seguridad
   propia, no se resuelve aquí.
2. **Inconsistencia menor detectada:** la descripción del hook en `settings.json`
   ("Autoload stateless de workflow-coordinator en cada prompt") quedó desactualizada desde que
   `skill-autoload.sh` se volvió stateful — no se corrigió como parte de este ADR.

## Files Changed

- `CLAUDE.md` (sección AUTO-SKILL-LOAD)
- `.claude/hooks/skill-autoload.sh` (mensaje del reminder)
- `.claude/hooks/skill-load-gate.sh` (edición manual — quitar excepción de `subagent_type`)
- `.agents/skills/workflow/workflow-coordinator/SKILL.md` (846 → 287 líneas)
- `.claude/agents/workflow-coordinator.md` (`description` de frontmatter aclarada)
- `docs/adr/index.md` (esta entrada)

## Verification

1. Sesión nueva, sin marcador previo en `.claude/sessions/`: confirmar que `skill-autoload.sh`
   emite el reminder con texto "Call Skill tool NOW...".
2. Confirmar que el primer tool call real es `Skill(skill="workflow-coordinator")`, no `Agent`.
3. Repetir un prompt de investigación de código real — confirmar que el mandato ya no se salta a
   favor de `Explore` (no hay ambigüedad de tool).
4. Segundo/tercer turno de la misma sesión: confirmar que el reminder NO se repite.
5. Con el cambio manual en `skill-load-gate.sh` aplicado: en sesión sin bypass, confirmar que un
   `Agent`/`EnterPlanMode` sin SKILL.md leído sigue bloqueado, y que
   `subagent_type="workflow-coordinator"` ya no es un bypass válido.
6. Confirmar que `intent-bootstrap.sh` y `task-scope.json` siguen funcionando sin cambios
   (mecanismo independiente).
