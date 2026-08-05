# ADR-0049: Fix — `_project_path()` no resolvía paths `shared/`/`scripts` literales

- **Estado:** Aceptado
- **Fecha:** 2026-08-05
- **Autor:** jeguzman

---

## Contexto

Al validar exhaustivamente `ai-notes/analysis/gitops-monorepo-guia-2026-08-04.md` contra los
4 escenarios de uso documentados (un proyecto / múltiples proyectos / solo shared / múltiples
+ shared), se encontró y **confirmó empíricamente en sandbox** que el escenario "solo shared"
(equipo de Platform trabajando en `shared/libs/auth` sin ser consumidor de ningún proyecto,
documentado como "Sabor 2" en `.agents/skills/infrastructure/gitops-monorepo/SKILL.md`) está
**roto** en el `release.sh` real:

```
$ ./scripts/release.sh init-multi shared/libs/auth
ERROR: no existe projects/shared/libs/auth
```

**Causa raíz:** `_project_path()` — usada por `cmd_work`/`init-multi`, `require_project_dir`,
`shared_deps_of`, `cmd_hotfix` y `_manifest_kind_and_path` — le antepone incondicionalmente
`projects/` (o el path de `GITOPS_PROJECT_MAP`) a cualquier argumento, sin verificar si ya es
un path literal de `shared/` o `scripts`. `cmd_add`/`cmd_drop` sí tienen ese caso especial
inline (`if path != shared/* && path != scripts*`), pero nunca se centralizó en
`_project_path()` — por eso `cmd_work`/`init-multi`, que no tienen ese guard, rompen.

El bug es idéntico en los 3 repos que comparten este contrato de `release.sh`: `ai-tooling`,
`deacero/commons` y `wpc-backend` (confirmado leyendo el código de los 3).

**Fuera de alcance de este ADR:** el "Sabor 2" de `SKILL.md` también afirma que el comando
"baja auth + todos sus consumers automáticamente" — esa función de descubrimiento inverso
(shared lib → proyectos que la consumen) **no existe en ninguna forma** en `release.sh`
(`shared_deps_of()` solo resuelve en la dirección proyecto → sus shared deps). Agregar ese
descubrimiento sería una *feature* nueva, no un fix, y no se hace aquí — se corrige la
documentación para no prometer algo que el código no hace.

## Decisión

Centralizar el guard de `cmd_add`/`cmd_drop` dentro de `_project_path()` mismo, como primer
branch, antes del lookup de `GITOPS_PROJECT_MAP` y antes del fallback `projects/<nombre>`:

```bash
_project_path() {
  local proj="$1"
  if [[ "$proj" == shared/* || "$proj" == scripts* ]]; then
    echo "$proj"
    return
  fi
  if [[ -n "${GITOPS_PROJECT_MAP:-}" ]]; then
    ...
```

Esto arregla, sin tocarlos, todos los call sites que ya dependen de `_project_path()`.
`cmd_add`/`cmd_drop` conservan su guard inline (queda redundante pero inofensivo).

Se corrige además el `SKILL.md` (Sabor 2) para quitar la afirmación de "descubre consumers
automáticamente", dejando claro que el developer de Platform debe conocer/agregar
manualmente los proyectos consumidores si quiere probarlos junto con el cambio en la shared
lib.

### Lo que NO cambia

- No se agrega descubrimiento de consumers (ver "Fuera de alcance").
- `cmd_add`/`cmd_drop` no se tocan — su guard inline queda redundante pero no daña nada.
- El resto de `release.sh` no se modifica.

## Consecuencias

### Positivas
- El escenario "solo shared" (equipo Platform) ahora funciona con el comando documentado:
  `init-multi shared/libs/auth` activa sparse con `scripts + shared/libs/auth`, sin
  requerir ningún proyecto consumidor en el sparse-set.
- La documentación deja de prometer una capacidad que no existe.

### Negativas / Costos
- Ninguna — cambio aditivo, no rompe ningún flujo existente (validado: escenarios de un
  proyecto, múltiples proyectos, múltiples+shared, y hotfix, todos sin regresión).

## Implementación

Validado en sandbox aislado (repo con `shared/libs/auth`, `shared/libs/logging`,
`projects/proyecto-a` consumidor, `projects/proyecto-b` consumidor, `projects/proyecto-c`
NO consumidor — para confirmar que "solo shared" no depende de tener un proyecto que lo
use) antes de aplicar a los archivos reales.

Archivos modificados: `scripts/release.sh` (`_project_path()`),
`.agents/skills/infrastructure/gitops-monorepo/SKILL.md` (Sabor 2),
`ai-notes/analysis/gitops-monorepo-guia-2026-08-04.md` (hallazgo + fix documentado).
Mismo fix portado a `deacero/commons` y `wpc-backend` (mismo contrato de `release.sh`).
