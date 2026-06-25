# ADR-0007: GitOps Monorepo con Trunk-Based Development y Sparse Checkout

- **Estado:** Propuesto
- **Fecha:** 2026-06-24
- **Autor:** jeguzman

---

## Contexto

Los proyectos de Deacero comparten un monorepo pero operan con mentalidad de multirepo:
las ramas `dev`, `qa` y `prod` son compartidas por todos los proyectos simultáneamente.

El resultado es acoplamiento involuntario: para deployar `proyecto-a` se arrastra también
el estado de `proyecto-b` y `proyecto-c`, aunque no estén listos. Los ambientes se contaminan
y la trazabilidad de "qué exactamente está en producción" se pierde.

---

## Decisión

Adoptar el modelo **`tag = versión en producción por proyecto`**, combinado con:

1. **Trunk-Based Development** — `main` (o la rama trunk configurada) siempre verde y desplegable
2. **Sparse checkout** — cada developer materializa solo los paths que necesita; el repo es uno
3. **Tag semántico por proyecto** — `proyecto-a@1.4.2` apunta a un SHA exacto y es inmutable
4. **CI path-based** — el pipeline detecta qué carpetas cambiaron y solo construye/despliega lo afectado

### Contrato de ramas (las únicas permitidas)

| Tipo | Patrón | Vida máxima | Origen |
|------|--------|-------------|--------|
| Trunk | `main` (configurable) | Permanente | — |
| Feature | `feature/*` | 3 días | trunk |
| Integración | `integration/*` | 2 semanas | trunk |
| Hotfix | `hotfix/<proyecto>/<nombre>` | Días | tag de prod |

**No existen** `develop`, `qa`, `staging` como ramas permanentes. Los ambientes son pipelines, no ramas.

### Tag naming convention

```
<proyecto>@<semver>[-<env>.<n>]

proyecto-a@1.4.2              ← producción (inmutable)
proyecto-a@1.4.2-rc.1         ← aprobado en QA
proyecto-a@1.4.2-dev.1        ← build en DEV
proyecto-a@1.4.2-hotfix.1     ← hotfix aplicado en prod
```

### Variables de entorno para repos en transición

| Variable | Default | Propósito |
|----------|---------|-----------|
| `GITOPS_REMOTE` | auto-detect | Remote autoritativo (para repos sin `origin`) |
| `GITOPS_TRUNK_BRANCH` | `main` | Permite usar `master` durante migración |
| `GITOPS_SCOPE` | `@deacero` | Scope de paquetes internos en shared/ |

### Advertencias del modelo

- `promote` manual es solo escape hatch para rollback, no para avanzar el flujo CI
- Tags de `dev.*` acumulan — cleanup automático recomendado para tags > 30 días
- CODEOWNERS sin branch restrictions en Bitbucket es documentación, no enforcement
- Bitbucket Pipelines usa `--depth 1` por defecto; el pipeline debe incluir `git fetch --unshallow`

---

## Consecuencias

### Positivas
- `proyecto-a` se deploya sin esperar a `proyecto-b`
- `prod` siempre rastreable a un SHA exacto (el tag)
- Hotfix quirúrgico desde el tag, sin riesgo de jalar código no listo
- Developer solo baja lo que le corresponde (sparse checkout)
- `shared/libs/` actualizada → todos sus consumers se testean automáticamente antes del merge

### Negativas / Costos
- Requiere capacitación del equipo en sparse checkout y trunk-based
- Pipeline de CI/CD más complejo (path-based change detection)
- CODEOWNERS y branch restrictions deben configurarse en Bitbucket antes de arrancar
- Migración de `master` → `main` requiere coordinación si se elige ese camino

### Neutrales
- El monorepo sigue siendo uno solo — solo cambia el contrato de deploys
- `vendor/` (ej. `vendor/claude-code-proxy/`) no se mueve; se documenta en CODEOWNERS

---

## Implementación

La utilería `scripts/release.sh` implementa todos los comandos de este flujo:

```
work    <proy> [proy-b...]  sparse checkout
expand                      checkout completo
sync                        rebase diario
status                      estado del entorno actual
add     <path>              agregar path al sparse set
drop    <path>              quitar path del sparse set
init    <proy>              alias de work (un proyecto)
init-multi <proy...>        alias de work (multi-proyecto)
tag     <proy> <ver>        crear tag de release desde trunk
hotfix  <proy> <ver> <nom>  branch de hotfix desde tag de prod
cherry  <proy> <ver>        cherry-pick del/los hotfix a trunk
check   [proy]              hotfixes pendientes de cherry-pick
promote <proy> <ver> <env>  escape hatch: re-promote / rollback
```

---

## Alternativas consideradas

### Alternativa A: Mantener ramas por ambiente, agregar solo path filters en pipelines
Mitiga el problema de contaminación pero no lo resuelve. Los releases siguen siendo
por rama, no por proyecto. La trazabilidad en prod sigue siendo ambigua.

### Alternativa B: Separar en repos independientes (multirepo real)
Elimina el acoplamiento de deploys pero fragmenta la visibilidad, complica los
refactors cross-proyecto y pierde el beneficio del historial compartido.

### Elegida: Monorepo + tags por proyecto
Combina la visibilidad del monorepo con la independencia de deploys del multirepo.
