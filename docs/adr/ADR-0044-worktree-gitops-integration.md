# ADR-0044: Integración de Git Worktree al GitOps Monorepo

- **Estado:** Aceptado
- **Fecha:** 2026-07-02
- **Autor:** jeguzman
- **Nota:** renumerado de ADR-0008 a ADR-0044 el 2026-08-03 — ese número quedó duplicado con
  `ADR-0008-plan-lock-implicit-exit-signal4.md` (dominio Proxy, sin relación). Contenido sin
  cambios, solo el número y sus referencias cruzadas.
- **Nota (2026-08-04):** aceptado formalmente vía `ADR-0048-homologacion-gitops-monorepo-commons.md`
  — la implementación aquí descrita ya existía en este repo; ese ADR documenta además la
  homologación bidireccional con `deacero/commons` (la instancia de producción real de este
  patrón), incluyendo un fix de corrección (`set -e` + `&&` sin guarda en `cmd_worktree clean`)
  encontrado durante la validación en sandbox de ese trabajo.

---

## Contexto

El flujo actual (ADR-0007) usa sparse checkout para que cada desarrollador materialice solo los archivos de su proyecto. Sin embargo, dos problemas persisten:

1. **Desarrollador con contexto dual**: al llegar un hotfix urgente mientras hay un feature en progreso, el desarrollador debe `git stash`, cambiar de branch, trabajar, y luego recuperar contexto. El stash no preserva el estado mental ni el working tree visual.

2. **Agentes escritores paralelos**: cuando el Workflow tool de Claude Code lanza múltiples agentes que escriben archivos simultáneamente en el mismo working tree, los cambios de un agente pueden sobrescribir o conflictuar con los de otro en disco — antes de cualquier merge.

---

## Decisión

Extender el toolset de GitOps con soporte nativo de `git worktree`:

1. **`release.sh worktree`** — nuevo subcomando con `add`, `add-branch`, `rm`, `prune`, `clean` y `list`
2. **`release.sh status`** — incluye `git worktree list` y detección de worktrees huérfanos (`prune --dry-run`)
3. **`block-dangerous.sh`** — nueva regla que bloquea `rm -rf` sobre paths que son worktrees activos registrados en git
4. **`worktree-isolation-gate.sh`** — hook PreToolUse en el Workflow tool de Claude Code que advierte cuando hay `parallel(agent())` sin `isolation: 'worktree'`
5. **`gitops-monorepo/SKILL.md`** — documenta worktrees como 6to sabor de trabajo, con flujo completo y reglas de disciplina

### Lo que NO cambia

- Sparse checkout sigue siendo la herramienta primaria para aislamiento de archivos
- El modelo `tag = versión en prod por proyecto` (ADR-0007) no se modifica
- Los pipelines de Bitbucket no cambian — siguen usando fresh clone
- Worktrees son opt-in; el flujo sin worktrees sigue funcionando igual

---

## Consecuencias

### Positivas
- Hotfix sin interrumpir el working tree activo: `cd ../wt-hotfix` en lugar de `git stash`
- Agentes paralelos escritores no se pisan en disco cuando usan `isolation: 'worktree'`
- `release.sh status` muestra el estado completo del entorno (sparse + worktrees + refs huérfanas)
- `block-dangerous.sh` previene la principal fuente de refs huérfanas (`rm -rf` accidental)

### Negativas / Costos
- Requiere que los desarrolladores internalicen un nuevo ritual: `worktree rm` en lugar de `rm -rf`
- El subcomando `worktree clean` tiene un falso negativo si la rama local ya fue borrada (manejado con `git rev-parse` guard)
- Overhead: máx. 3-4 worktrees simultáneos es el límite práctico antes de perder visibilidad

### Neutrales
- Los worktrees comparten la misma historia git — un `git fetch` en cualquier worktree actualiza todos
- La combinación sparse checkout + worktree es válida: cada worktree puede tener su propio sparse set

---

## Implementación

Ver plan de implementación en `.claude/plans/analizame-exhausitvamente-como-funciona-harmonic-valley.md`.

Archivos modificados:
- `scripts/release.sh` — `cmd_status` + `cmd_worktree`
- `.claude/hooks/block-dangerous.sh` — regla 5
- `.claude/hooks/worktree-isolation-gate.sh` — nuevo
- `.claude/settings.json` — registro del nuevo hook
- `.agents/skills/infrastructure/gitops-monorepo/SKILL.md` — sección worktrees
