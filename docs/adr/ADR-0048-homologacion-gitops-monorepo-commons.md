# ADR-0048: Homologación GitOps con `commons` — puerto bidireccional de mejoras validadas en producción

- **Estado:** Aceptado
- **Fecha:** 2026-08-04
- **Autor:** jeguzman

---

## Contexto

`docs/adr/ADR-0007-gitops-monorepo-trunk-based.md` y `.agents/skills/infrastructure/gitops-monorepo/SKILL.md`
documentan una estrategia GitOps agnóstica de organización. `deacero/commons` es la instancia real
de esa estrategia en producción (bootstrapeada con `scripts/gitops-init.sh` hace meses, aloja
`python/auth`, `go/auth`, `typescript-auth` en uso real por otros repos de Grupo Deacero).

Un análisis exhaustivo de `commons` (`ai-notes/analysis/commons-gitops-analysis-2026-08-04.md`)
encontró que, tras el bootstrap, ambos repos evolucionaron por separado sin canal de retorno:
`commons` generó fixes y mejoras reales de producción — documentados en su propio
`docs/adr/ADR-0008-reconciliacion-esquema-tags.md` (Aceptado) — que nunca se portaron de vuelta
a `ai-tooling`. Ese mismo `ADR-0008` deja registrado explícitamente, en su sección "Fuera de
alcance", que el subsistema `worktree` de `ai-tooling` (más rico que el de `commons`) debía
homologarse en algún momento futuro. Este ADR ejecuta esa homologación pendiente, en ambas
direcciones.

Al mismo tiempo, `docs/adr/ADR-0044-worktree-gitops-integration.md` seguía en estado "Propuesto"
en este repo pese a que su implementación (`cmd_worktree` con `add|add-branch|rm|prune|clean|list`,
detección de huérfanos en `cmd_status`, regla 5 de `block-dangerous.sh`,
`worktree-isolation-gate.sh`) ya existía y ya fue validada indirectamente por el uso real que
`commons` hizo de un modelo más simple del mismo concepto.

## Decisión

1. **Aceptar `ADR-0044`** — su implementación ya existe en este repo; se marca `Aceptado`.
2. **Portar la mejora de `commons` a `ai-tooling`:** el `cmd_worktree_add` de `commons` aplica
   sparse-checkout automáticamente dentro del worktree nuevo (por nombre de proyecto). Se agrega
   el equivalente a `scripts/release.sh`, adaptado al modelo rama-céntrico de `ai-tooling`: una
   función `_replicate_sparse_checkout(wt_path)` que, si el árbol principal tiene sparse-checkout
   activo, replica el mismo sparse-set (`git sparse-checkout list`) dentro del worktree nuevo. Se
   invoca al final de los subcomandos `add` y `add-branch` de `cmd_worktree()`.
3. **Agregar a la tabla de gotchas de `.agents/skills/infrastructure/gitops-monorepo/SKILL.md`**
   los 2 hallazgos reales de producción documentados en `commons` que no estaban aquí: bash de
   sistema en macOS (3.2) sin `mapfile`/`readarray`, y el bug `tr -d '/'` vs `sed 's:/*$::'` al
   parsear la directiva `adr_path:` de `.claude/adr-gate.conf`.
4. **Fix de corrección encontrado durante la validación en sandbox de este ADR (no reportado
   antes):** `cmd_worktree()` subcomando `clean` tiene el patrón
   `[[ $found -eq 0 ]] && ok "ningún candidato"` como única guarda antes de dos líneas `info`
   subsiguientes. Con `set -euo pipefail` activo en el script, cuando SÍ hay candidatos
   (`found=1`, el caso útil), la expresión completa retorna estado 1 y el script aborta
   silenciosamente antes de imprimir las dos líneas de ayuda (`para eliminar...`,
   `para limpiar refs...`). Se corrige agregando `|| true` al final de esa línea. Se reprodujo
   el bug y se validó el fix en un repo git aislado antes de tocar el `release.sh` real (ver
   Implementación). El mismo patrón existe también en `cmd_check` (línea con
   `[[ $found -eq 0 ]] && ok "sin hotfixes pendientes"`); ahí es inofensivo porque es la última
   sentencia de la función y no hay código subsiguiente que dependa de seguir ejecutándose, pero
   se corrige igual por consistencia (mismo one-liner `|| true`).

### Lo que NO cambia

- El modelo de comandos de `cmd_worktree()` (`add|add-branch|rm|prune|clean|list`) no cambia —
  ya es el modelo de referencia que `commons` va a adoptar (ver homologación en `commons`,
  documentada en su propio `ADR-0011-homologacion-worktree-subsystem.md`).
- `check_adr_gate.py` de `ai-tooling` no se modifica en este ADR — ya soporta
  `.claude/adr-gate.conf` (función `_load_conf_rules()`), a diferencia de lo que el análisis
  previo sugería; la única brecha real frente a `commons` es que ese repo carecía de
  `_check_adr_sequence()`/`_staged_files()`, que se porta del lado de `commons` (fuera del
  alcance de este ADR).

## Consecuencias

### Positivas

- El canal de retorno commons→ai-tooling que no existía queda ejercido por primera vez: 2
  gotchas de producción real entran a la documentación de referencia.
- Un worktree nuevo hereda automáticamente el sparse-set activo — evita que el primer `cd` a un
  worktree recién creado muestre el árbol completo por sorpresa.
- Se corrige un bug latente (`set -e` + `&&` sin guarda) que existía sin reportarse desde que
  `ADR-0044` se implementó.

### Negativas / Costos

- Ninguna migración requerida — ambos cambios son aditivos/correctivos, no rompen el
  comportamiento existente para quien no usa sparse-checkout o no ejecuta `worktree clean` con
  candidatos pendientes.

## Implementación

Ver plan ejecutado en esta sesión (`ai-notes/analysis/commons-gitops-analysis-2026-08-04.md`,
sección "Homologación ejecutada").

Archivos modificados:
- `docs/adr/ADR-0044-worktree-gitops-integration.md` — estado → Aceptado
- `docs/adr/index.md` — entrada de este ADR
- `scripts/release.sh` — `_replicate_sparse_checkout()`, llamadas en `add`/`add-branch`, fix
  `|| true` en `clean` y `cmd_check`
- `.agents/skills/infrastructure/gitops-monorepo/SKILL.md` — 2 gotchas nuevos + nota de
  auto-sparse en la sección Worktrees

**Validación:** la lógica de `_replicate_sparse_checkout()` y el fix de `clean` se reprodujeron y
verificaron primero en un repo git aislado (sandbox, fuera de este repositorio) con 7 escenarios
(add-branch con sparse activo/inactivo, add sobre rama existente, rm, clean con/sin candidatos,
prune, list) antes de aplicarse aquí. Repetido en vivo contra el `release.sh` real (clon temporal)
antes del commit.

### Addendum (mismo día, post-commit): bug real encontrado al comitear

Al comitear este trabajo, el pre-commit hook reportó "Archivos guardados: 0" pese a que
`.agents/skills/.../SKILL.md` estaba staged. Causa: `tools/check_adr_gate.py::_normalise()`
usaba `p.lstrip("./")`, que quita un *set* de caracteres (`.` y `/`), no el prefijo literal
`"./"` — para una ruta que empieza con punto (`.agents/skills/...`) esto se come el punto
inicial y rompe el match contra el guarded pattern. El hook bash paralelo (`adr-gate.sh`, que
usa `${FILE#./}`) nunca tuvo este bug. Corregido con un loop `while p.startswith("./")`. Mismo
bug encontrado y corregido en `commons/tools/check_adr_gate.py` (heredado al portarlo en esta
misma homologación) y en `wpc-backend/tools/check_adr_gate.py` (variante más simple, ahí
explotable en producción real). Ver `ai-notes/AI_LEARNING.md` P019.
