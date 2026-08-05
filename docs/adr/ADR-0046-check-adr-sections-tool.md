# ADR-0046: Nuevo checker `tools/check_adr_sections.py`

**Status:** Accepted
**Date:** 2026-08-03
**Supersedes:** —
**Superseded by:** —
**Related:** ADR-0045 (`check_skill_frontmatter.py`) — mismo patrón de checker
stdlib-only para una precondición estructural de contenido; ADR-0001 (Adopt
Agentic CI Skill System) — introdujo `docs/adr/` como fuente de verdad de
decisiones, sin verificación automatizada de su forma mínima hasta ahora.

---

## Context

`docs/adr/` acumula 45 ADRs (`ADR-0001`..`ADR-0046`, con un hueco renumerado —
ver ADR-0044) sin ningún checker que valide que cada uno tenga las secciones
mínimas de las que depende el resto del sistema: `Status` (¿sigue vigente la
decisión?), `Date` (¿cuándo se tomó?), `Context` (¿qué problema resuelve?) y
`Decision` (¿qué se decidió?). `check_adr_gate.py` (ADR previo, sin número
propio) verifica que un ADR *exista* junto a un cambio guardado, pero no
verifica su *forma interna*.

Una inspección manual del corpus real reveló que NO es uniforme:
- La mayoría usa `## Context` / `## Decision` como heading H2.
- Varios (`ADR-0001`, `ADR-0002`, `ADR-0003`, `ADR-0004`, etc.) usan
  `Status`/`Date` como campo inline en negrita (`- **Date**: 2026-03-22`),
  no como heading.
- Al menos uno (`ADR-0010`) tiene `**Context**:` como campo inline en la
  cabecera Y ADEMÁS una sección `## Problem` separada para la elaboración —
  el campo inline sí satisface el requisito aunque el heading H2 diga otra
  cosa.
- Uno (`ADR-0022`) usa `## Status` como heading con el valor Y la fecha
  mezclados en el texto libre debajo (`Accepted — 2026-07-15`), sin un campo
  `Date` propio en ninguna forma.
- Seis (`ADR-0007`, `ADR-0017`, `ADR-0018`, `ADR-0034`, `ADR-0035`,
  `ADR-0044`) están redactados enteramente en español: `Estado`, `Fecha`,
  `Contexto`, `Decisión` en vez de los nombres en inglés.

### Sobre el proceso de este ADR (nota de transparencia, mismo patrón que ADR-0045)

Este tool se escribió bajo la identidad "Tool Writer"
(`.agents/skills/core/tool-writer/SKILL.md`), cuyo protocolo documentado
prescribe: (a) consultar un "Architect Agent" antes de escribir código, (b)
ubicar el tool en `tools/[domain]/[subdomain]/[tool]/` con su propio
subdirectorio `ADR/`, y (c) tras commitear, hacer push automático a un repo
remoto llamado `deagentic`. Se re-verificó (independientemente de ADR-0045,
mismo resultado):
- No existe ningún agente "Architect" invocable como `subagent_type` en esta
  sesión.
- `tools/` sigue siendo plano en la práctica (`check_adr_gate.py`,
  `check_skill_frontmatter.py`, `install_hooks.sh`) — sin jerarquía
  `domain/subdomain/tool/`.
- `git remote -v` solo lista `company` (Bitbucket corporativo) y `personal`
  (GitHub personal) — sigue sin existir ningún remoto `deagentic`.

Se sigue la misma convención real ya establecida por ADR-0045: ubicación
plana en `tools/`, ADR en `docs/adr/`, y se omite el Phase 5 (push a
`deagentic`) por no existir ese remoto. Este ADR tampoco ejecuta ningún
`git commit`/`git push` — el checker se entrega listo, y la decisión de
commitear queda con quien pidió la tarea (fuera de alcance de este ADR
autorizar operaciones de git no solicitadas explícitamente).

## Decision

Crear `tools/check_adr_sections.py`: un script Python 3 (stdlib only) que
recorre (sin recursión) `--root` (default `docs/adr`) buscando `--pattern`
(default `ADR-*.md`), y para cada ADR valida que existan las secciones
`--sections` (default `Status,Date,Context,Decision`) en **cualquiera** de
dos formas equivalentes por línea:
1. **Heading-style**: `#`..`######` cuyo texto empieza con el nombre de
   sección (prefix-match con límite de palabra — acepta `## Context and
   Problem Statement`, `## Decision Outcome`).
2. **Inline-field-style**: línea (con viñeta `-`/`*` opcional, negrita `**`
   en cualquier combinación alrededor del nombre) que, tras normalizar
   (quitar viñeta y asteriscos), empieza con `nombre:`.

Con `--i18n-es`, además acepta el equivalente en español de cada sección
(`Estado`, `Fecha`, `Contexto`, `Decisión`/`Decision`) en las mismas dos
formas. **Sin ese flag es el default** — el usuario pidió verificar los
nombres en inglés explícitamente; el flag existe para un chequeo más laxo
sobre el mismo corpus mixto, no para ocultar el hallazgo real.

Reporta por stdout (texto o `--json`) la lista de ADRs con secciones
faltantes; exit code 0 (todo completo) / 1 (al menos un ADR incompleto) / 2
(error de uso, `--root` inexistente).

Diseño clave:
- **Detección dual (heading + inline-field) en vez de forzar un único
  formato**, porque unificar el formato de 45 ADRs existentes está fuera de
  alcance de esta tarea (el pedido era "reportar cuáles no las tienen", no
  "reescribir todos los ADRs a un formato único").
- **`--i18n-es` opt-in, no default**, porque el pedido nombró explícitamente
  `Status, Date, Context, Decision` — tratar `Estado`/`Contexto` como
  equivalentes por default habría ocultado silenciosamente que 6 ADRs reales
  no tienen esos nombres en inglés en absoluto.
- **Sin dependencias externas**, mismo criterio que `check_adr_gate.py` y
  `check_skill_frontmatter.py`.

### Alternatives Considered

- **A. Detección dual heading+field, i18n opt-in (elegida).** Refleja el
  corpus real sin ocultar el hallazgo pedido.
- **B. Exigir un único formato (solo heading H2).** Rechazada: habría
  marcado como "incompletos" ~15 ADRs que en realidad sí documentan
  Status/Date, solo que como campo inline — falso negativo masivo, inútil
  para el reporte pedido.
- **C. Tratar Estado/Contexto/Decisión como equivalentes por default (sin
  flag).** Rechazada: el usuario pidió verificar los nombres en inglés
  específicamente; asumir la equivalencia por default habría respondido una
  pregunta distinta a la que se hizo.

## Consequences

**Positivo:**
- `docs/adr/` gana un gate reusable (CI / pre-commit / manual) para su
  precondición estructural mínima, análogo a `check_skill_frontmatter.py`
  para `.agents/skills/`.
- Reporte real generado en esta misma tarea (ver `Verification`) — 1 gap
  genuino encontrado (`ADR-0022` sin `Date` en ninguna forma), y 6 falsos
  positivos en modo estricto resueltos exactamente por `--i18n-es`
  (confirmando que el corpus mixto ES→EN es real, no un error del tool).

**Negativo / limitaciones:**
- No valida el *contenido* de cada sección (ej. que `Date` tenga un formato
  de fecha válido, o que `Status` sea uno de un enum conocido) — solo su
  presencia.
- El prefix-match de heading (`## Context and Problem Statement` cuenta como
  `Context`) es una heurística deliberada, no infalible: un heading como
  `## Contextualización` también matchearía `Context` por prefijo — no se
  observó ningún caso así en el corpus real, pero es un falso positivo
  teórico posible.
- No se consultó un "Architect Agent" real ni se hizo push a un repo
  `deagentic` — ver nota de transparencia arriba (mismo trade-off que
  ADR-0045).

## Files Changed

- `tools/check_adr_sections.py` (nuevo)
- `tools/tests/test_check_adr_sections.py` (nuevo — 20 tests, unittest
  stdlib, corridos localmente: 20/20 OK)
- `docs/tools/index.md` (nueva entrada para este tool)
- `docs/adr/ADR-0046-check-adr-sections-tool.md` (este archivo)

## Verification

1. `python3 -m unittest tools/tests/test_check_adr_sections.py -v` → 20/20 OK.
2. `python3 tools/check_adr_sections.py --root docs/adr` (modo estricto,
   default) → 45 ADRs revisados, 7 incompletos, exit code 1:
   - `ADR-0007`, `ADR-0017`, `ADR-0018`, `ADR-0034`, `ADR-0035`, `ADR-0044`:
     `faltan secciones: Status, Date, Context, Decision` (las 4, en inglés).
   - `ADR-0022`: `faltan secciones: Date`.
3. `python3 tools/check_adr_sections.py --root docs/adr --i18n-es` → 45
   revisados, 1 incompleto (`ADR-0022`, sigue faltando `Date` en cualquier
   idioma) — confirma que los otros 6 eran falsos positivos del modo
   estricto por estar en español, no bugs del checker.
4. Verificación manual independiente (`grep -inE` ad-hoc sobre los 7
   archivos flaggeados) confirma cada hallazgo: los 6 en español sí tienen
   `Estado`/`Fecha`/`Contexto`/`Decisión`; `ADR-0022` sí tiene `## Status`
   pero la fecha está embebida como texto libre, no como campo propio.
