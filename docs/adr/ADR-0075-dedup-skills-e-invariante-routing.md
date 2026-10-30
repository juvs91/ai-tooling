# ADR-0075: Deduplicación de skills e invariante de routing verificado por test

- **Estado:** Aceptado — retroactivo; borrado implementado en b53b6ba (2026-10-30), invariante el mismo día
- **Fecha:** 2026-10-30
- **Autor:** Juve + Claude
- **Relacionado:** ADR-0043 (wrapper de routing), refactor company-agnostic (7cb7810)

---

## Contexto

OpenCode reportaba `duplicate skill name` en cada arranque: 11 `SKILL.md` repetían el `name:` de
otro skill. Claude Code no avisa, pero con dos skills del mismo nombre carga uno sin garantía de
cuál.

El commit b53b6ba los borró **sin ADR**. Se hizo desde un clon sin el ADR gate git instalado
(ADR-0074: si falta, solo se avisa). En este clon el gate lo habría bloqueado. Este ADR lo
documenta de forma retroactiva.

Los 11 eran duplicados. Se verificó comparando cada archivo borrado con el que quedó:

| Borrado | Sobreviviente | Diferencia |
|---|---|---|
| `frontend/design/stitch/{design-md, enhance-prompt, react-components, remotion, shadcn-ui, stitch-design, stitch-loop}` (7) | `frontend/<mismo>` | Ninguna (idénticos byte a byte) |
| `backend/python-testing`, `frontend/nextjs`, `frontend/senior-frontend`, `quality/tdd-workflow` (4) | `software/<misma ruta>` | Solo el frontmatter; el cuerpo es idéntico |

El frontmatter legacy de esos 4 traía `allowed-tools` con MCPs de la empresa (Jira, Bitbucket,
squit) y un campo `color`. Eso es lo que eliminó el refactor company-agnostic (7cb7810): los
sobrevivientes son las versiones posteriores a ese refactor.

**Daño colateral:** `.claude/commands/workflow-coordinator.md` (el wrapper de routing que se carga
al inicio de cada sesión) seguía apuntando a `backend/python-testing/SKILL.md`. `AGENTS.md` y
`skills.md` ya estaban bien. Nada lo detectó, porque las rutas de skills viven **copiadas a mano en
3 archivos** y no había ninguna verificación.

## Decisión

1. **Se aceptan los borrados de b53b6ba.** Los sobrevivientes son la fuente de verdad.
2. **Se corrige la ruta** en `workflow-coordinator.md` a `software/backend/python-testing`.
3. **Invariante de routing como test** (`tests/harness/run-tests.sh`, T14), que corre en cada
   suite:
   - toda ruta `SKILL.md` citada en `AGENTS.md`, `.agents/skills/skills.md` y
     `.claude/commands/workflow-coordinator.md` existe;
   - ningún `name:` de frontmatter se repite entre los `SKILL.md` (la condición que veía OpenCode);
   - cada fila de la tabla de `workflow-coordinator.md` apunta a un skill cuyo `name:` coincide con
     el nombre de la fila (detecta una ruta que existe pero es de otro skill).

   Verificado: contra 351d152 (antes de la corrección) T14 falla y señala
   `backend/python-testing/SKILL.md(wc)`.

## Alternativas descartadas

- **Generar `skills.md` y la tabla del wrapper desde el frontmatter.** Eliminaría la copia manual,
  pero el wrapper y `AGENTS.md` llevan triggers y descripciones curadas a mano (ADR-0043). El test
  da la misma garantía sin cambiar ese flujo.
- **Revertir b53b6ba y rehacerlo con ADR.** El contenido es correcto; revertirlo solo agregaría
  ruido al historial.

## Consecuencias

- Mover, borrar o renombrar un skill sin actualizar los 3 índices rompe la suite, no el routing en
  silencio.
- Dos nombres de presentación en `AGENTS.md` (`nextjs` → `nextjs-skills`, `react-components` →
  `react:components`) difieren del `name:`. Es cosmético y T14 no lo exige para `AGENTS.md`.
- Pendiente operativo: instalar el ADR gate git en el clon donde se hizo b53b6ba
  (`bash tools/install_hooks.sh`).
