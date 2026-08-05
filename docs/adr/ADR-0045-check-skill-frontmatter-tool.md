# ADR-0045: Nuevo checker `tools/check_skill_frontmatter.py`

**Status:** Accepted
**Date:** 2026-08-03
**Supersedes:** —
**Superseded by:** —
**Related:** ADR-0001 (Adopt Agentic CI Skill System) — este checker protege la
precondición de la que depende todo ese sistema (un `SKILL.md` sin `name`
resoluble no puede cargarse vía `Skill` tool ni catalogarse por `sync_skills.sh`).

---

## Context

El sistema de skills de este repo (`.agents/skills/**/SKILL.md`) depende de que
cada archivo tenga un frontmatter YAML con, como mínimo, un campo `name` no
vacío — es la clave que el `Skill` tool usa para resolver `Skill(skill="...")`
y que `sync_skills.sh` usa para catalogar. No existía ningún checker que
verificara esto de forma automatizada; la única forma de detectar un
`SKILL.md` roto era que fallara en tiempo de uso.

Una inspección manual confirmó 3 archivos reales sin frontmatter en el
árbol actual (ver sección "Consequences" — hallazgo, no arreglado por este
ADR):
- `.agents/skills/archaeology/squit/SKILL.md`
- `.agents/skills/core/orchestrator/SKILL.md`
- `.agents/skills/frontend/frontend-analysis/SKILL.md`

### Sobre el proceso de este ADR (nota de transparencia)

Este tool se escribió bajo la identidad "Tool Writer" (`.agents/skills/core/tool-writer/SKILL.md`),
cuyo protocolo documentado prescribe: (a) consultar un "Architect Agent" antes
de escribir código, (b) ubicar el tool en
`tools/[domain]/[subdomain]/[tool]/` con su propio subdirectorio `ADR/`, y (c)
tras commitear, hacer push automático a un repo remoto llamado `deagentic`.
Se verificó contra el estado real del repo que:
- No existe ningún agente "Architect" invocable como `subagent_type` en esta
  sesión (sí existe `.agents/skills/software/architecture/architect/SKILL.md`
  como archivo, pero no está en el roster de skills/agentes cargables aquí).
- `tools/` es plano en la práctica (`tools/check_adr_gate.py`,
  `tools/install_hooks.sh` — sin jerarquía `domain/subdomain/tool/`), y los
  ADRs reales del repo viven todos en `docs/adr/ADR-NNNN-*.md`, nunca dentro
  de un tool individual.
- `git remote -v` solo lista `company` (Bitbucket corporativo) y `personal`
  (GitHub personal) — no existe ningún remoto `deagentic`.

Se decidió seguir la convención REAL y verificable del repo (ubicación plana
en `tools/`, ADR en `docs/adr/`) en vez de la jerarquía aspiracional descrita
en el `SKILL.md` del Tool Writer, y se omite el Phase 5 (push a `deagentic`)
por no existir ese remoto — inventar un push a un repo inexistente sería peor
que omitirlo. Se reporta esta discrepancia aquí explícitamente para que un
mantenedor futuro decida si el `SKILL.md` del Tool Writer debe actualizarse
para reflejar la convención real, o si el repo debe migrar hacia la jerarquía
que ese `SKILL.md` describe.

## Decision

Crear `tools/check_skill_frontmatter.py`: un script Python 3 (stdlib only, sin
PyYAML) que recorre recursivamente `--root` (default `.agents/skills`)
buscando `--filename` (default `SKILL.md`), valida que cada archivo tenga un
bloque de frontmatter YAML (`---` ... `---`) con un campo `--field` (default
`name`) no vacío, y reporta los archivos problemáticos por stdout con exit
code 0 (todo válido) / 1 (al menos un problema) / 2 (error de uso, ej. `--root`
inexistente).

Diseño clave:
- **Parseo ingenuo por línea**, no un parser YAML completo — suficiente para
  este contrato (detectar delimitadores `---` y una clave de nivel superior),
  evita la dependencia de PyYAML, consistente con el resto del repo (
  `tools/check_adr_gate.py` tampoco usa parsers pesados).
- **CLI parametrizado** (`--root`, `--filename`, `--field`) para que el mismo
  script sirva para validar otros archivos con contrato similar (ej. futuros
  `AGENT.md` con campo `id`), no solo `SKILL.md`/`name` — generalización
  pedida explícitamente por el mandato del Tool Writer.
- **Salida dual**: texto plano legible (default, mismo estilo que
  `check_adr_gate.py`, que es lo que efectivamente consume un humano/hook hoy)
  y `--json` opcional (objeto único a stdout) para consumo agéntico estricto —
  concilia el mandato de "salida JSON estricta" del Tool Writer con la
  convención real y explícitamente pedida de mantener consistencia con
  `check_adr_gate.py`.

### Alternatives Considered

- **A. Parseo ingenuo por línea + CLI parametrizado (elegida).** Sin
  dependencias nuevas, generalizable, consistente con el estilo del repo.
- **B. Usar PyYAML para un parseo completo del frontmatter.** Rechazada: el
  enunciado del requisito explícitamente pide evitarlo si no está ya en uso,
  y no aporta valor para el contrato pedido (solo se necesita detectar
  delimitadores y una clave top-level).
- **C. Ubicar el tool en `tools/agent-infra/skills/check-skill-frontmatter/`
  con su propio `ADR/` y `tests/` internos**, siguiendo literalmente el
  `SKILL.md` del Tool Writer. Rechazada: no hay ningún precedente de esa
  jerarquía en el repo real; habría introducido una convención nueva y
  discordante solo para este tool, contradiciendo la "Deduplication Mandate"
  del propio Tool Writer (reusar/alinear con lo existente antes de inventar).

## Consequences

**Positivo:**
- El sistema de skills gana un gate automatizable (CI / pre-commit / manual)
  para la precondición mínima de la que depende `Skill` tool y
  `sync_skills.sh`.
- Hallazgo real capturado por el propio tool en su primera corrida contra
  `.agents/skills/` (76 archivos revisados, 3 problemáticos — ver arriba).
  **Este ADR NO corrige esos 3 archivos** — están fuera de alcance de esta
  tarea (que era construir el checker, no arreglar skills existentes) y
  además tocar `.agents/skills/**/*.md` requeriría su propio ADR bajo el
  ADR-First Gate ya vigente en este repo.
- Reusable fuera de este repo tal cual (sin hardcodear rutas de este
  proyecto más allá de los defaults, todos overrideables por flag).

**Negativo / limitaciones:**
- No valida el resto del YAML del frontmatter (solo delimitadores + una
  clave). Un frontmatter con YAML inválido en otros campos no se detecta.
- No soporta comentarios inline después del valor de la clave (ej.
  `name: foo  # nota`) — se incluirían como parte del valor. No se consideró
  necesario dado el uso real observado en los 76 archivos existentes.
- No se consultó un "Architect Agent" real (no invocable en esta sesión) ni
  se hizo push a un repo `deagentic` (no existe) — ver nota de transparencia
  arriba.

## Files Changed

- `tools/check_skill_frontmatter.py` (nuevo)
- `tools/tests/test_check_skill_frontmatter.py` (nuevo — 17 tests, unittest
  stdlib, corridos localmente: 17/17 OK)
- `docs/tools/index.md` (nuevo — cataloga este tool y `check_adr_gate.py`)
- `docs/adr/index.md` (esta entrada)

## Verification

1. `python3 -m unittest tools/tests/test_check_skill_frontmatter.py -v` → 17/17 OK.
2. `python3 tools/check_skill_frontmatter.py` corrido contra el árbol real →
   76 archivos revisados, 3 problemáticos, exit code 1 — confirmado contra
   inspección manual independiente (`grep`/`awk` ad-hoc sobre los mismos 76
   archivos, mismos 3 resultados).
3. `python3 tools/check_skill_frontmatter.py --json` → JSON parseable con
   las claves documentadas (`root`, `field`, `total_files`, `ok`,
   `problem_count`, `problems[]`).
4. `python3 tools/check_skill_frontmatter.py --root <inexistente>` → exit
   code 2, stdout vacío, mensaje en stderr.
