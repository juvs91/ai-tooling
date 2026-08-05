# GitOps Monorepo — Guía Exhaustiva de Uso

Análisis de la skill `gitops-monorepo`, `scripts/gitops-init.sh` y `scripts/release.sh`
(código real leído línea por línea, sin inferencias no verificadas). Incluye diagramas de
ambientes DEV/QA/PROD, tagging, pipeline → artefactos, y un modelo de interacción de
equipo (20 personas / 4 proyectos) derivado del contrato documentado en ADR-0007 y ADR-0044.
Todos los diagramas están en ASCII (compatibles con exportación a Word/documento plano).

> **Nota de actualización (2026-08-04, mismo día, sesión posterior):** después de escribir
> este análisis se ejecutó una homologación real entre `ai-tooling` y `deacero/commons`
> (instancia de producción de este mismo patrón) que modificó `release.sh`, `SKILL.md` y
> aceptó `ADR-0044`. Esta revisión actualiza todos los números de línea, conteos y estados
> afectados, y agrega la sección 16 con lo nuevo. Ver
> `ai-notes/analysis/commons-gitops-analysis-2026-08-04.md` para el detalle completo de esa
> homologación (no se duplica aquí, solo se referencia lo que cambia esta guía).

**Fuentes analizadas:**
- `.agents/skills/infrastructure/gitops-monorepo/SKILL.md` (489 líneas)
- `scripts/gitops-init.sh` (485 líneas)
- `scripts/release.sh` (866 líneas, 16 comandos + 6 subcomandos de `worktree`)
- `templates/gitops/bitbucket-pipelines.yml.template` (187 líneas)
- `templates/gitops/bitbucket-pipelines-library-publish.yml.template` (231 líneas)
- `templates/gitops/CODEOWNERS.template` (43 líneas)
- `docs/adr/ADR-0007-gitops-monorepo-trunk-based.md`
- `docs/adr/ADR-0044-worktree-gitops-integration.md` (Estado: **Aceptado**, actualizado 2026-08-04)
- `docs/adr/ADR-0048-homologacion-gitops-monorepo-commons.md` (2026-08-04)
- `docs/adr/ADR-0049-fix-project-path-shared-only.md` (nuevo, 2026-08-05)

---

## 1. El problema que resuelve

Mentalidad multirepo dentro de un monorepo: `dev`/`qa`/`prod` como ramas compartidas por
todos los proyectos. Deployar `proyecto-a` arrastra el estado de `proyecto-b` y
`proyecto-c` aunque no estén listos → ambientes contaminados, trazabilidad de "qué hay
exactamente en prod" perdida.

```
  ❌ Modelo multirepo (el problema)

  proyecto-a ─┐
  proyecto-b ─┼──► rama dev ──► rama qa ──► rama prod
  proyecto-c ─┘

  Deployar proyecto-a arrastra lo que esté en dev de b y c, estén listos o no.
```

**Causa raíz:** cada proyecto depende de en qué *rama* está parado, no de qué *versión*
se decidió promover.

---

## 2. La solución: `tag = versión en producción por proyecto`

```
  ✓ Modelo GitOps monorepo

  main:  A──B──C──D──E──F──G   (sigue creciendo, siempre desplegable)
              │           │
              │           └── tag: proyecto-b@2.0.0 ──► solo esto en prod de proyecto-b
              │
              └── tag: proyecto-a@1.4.2 ──► solo esto en prod de proyecto-a
```

Cuatro pilares (ADR-0007):
1. **Trunk-Based Development** — `main` siempre verde y desplegable.
2. **Sparse checkout** — cada developer materializa solo los paths que necesita.
3. **Tag semántico por proyecto** — `proyecto-a@1.4.2` apunta a un SHA exacto, inmutable.
4. **CI path-based** — el pipeline detecta qué carpetas cambiaron y solo construye/despliega lo afectado.

---

## 3. Estructura del monorepo

```
repo/
├── shared/
│   └── libs/
│       ├── auth/          @company/auth      ← mínimo 2 proyectos deben consumirla
│       └── logging/       @company/logging
├── projects/
│   ├── proyecto-a/
│   ├── proyecto-b/
│   └── proyecto-c/
├── scripts/
│   ├── release.sh
│   └── gitops-init.sh
└── CODEOWNERS
```

**Regla de shared:** si solo un proyecto la usa, no es shared — va dentro del proyecto.
Esto lo aplica `release.sh` en tiempo de ejecución vía `shared_deps_of()` (sección 6.3).

---

## 4. Modelo de ramas (las únicas permitidas)

| Tipo | Patrón | Vida máxima | Origen |
|------|--------|-------------|--------|
| Trunk | `main` (configurable vía `GITOPS_TRUNK_BRANCH`) | Permanente | — |
| Feature | `feature/*` | 3 días | trunk |
| Integración | `integration/*` | 2 semanas | trunk |
| Hotfix | `hotfix/<proyecto>/<nombre>` | Días | tag de prod |

`develop`, `qa`, `staging` como ramas permanentes **no existen**. Los ambientes son
pipelines (definidos por qué tag se acaba de crear), no ramas.

---

## 5. Tagging — convención y ciclo de vida

```
<proyecto>@<semver>[-<env>.<n>]

proyecto-a@1.4.2              ← producción (inmutable)
proyecto-a@1.4.2-rc.1         ← aprobado en QA
proyecto-a@1.4.2-dev.1        ← build en DEV
proyecto-a@1.4.2-hotfix.1     ← hotfix aplicado en prod
```

### Ciclo de vida de un tag (flujo normal, mismo SHA de principio a fin)

```
  main:  A──B──C──D (bump 1.4.2)
                     │
                     ├─ tag proyecto-a@1.4.2-dev.1   ◄── CI automático
                     │
                     ├─ tag proyecto-a@1.4.2-rc.1     ◄── gate manual QA
                     │
                     └─ tag proyecto-a@1.4.2          ◄── gate aprobación humana

  Los 3 tags apuntan al MISMO commit D — solo se re-etiqueta, nunca se
  crea un commit nuevo entre dev → rc → prod.
```

`release.sh tag` **no crea un commit nuevo** — el tag `-dev.1`/`-rc.1`/final apunta al
mismo SHA de `main` en cada promoción. Verificable con:
```bash
git diff proyecto-a@1.4.2-rc.1 proyecto-a@1.4.2   # debe ser vacío
```

### Clasificación de un tag por sufijo (`cmd_versions`, release.sh:681-706)

| Sufijo del tag | Ambiente reportado |
|---|---|
| `-dev.N` | `DEV` |
| `-rc.N` | `QA` |
| `-hotfix.N` | `HOTFIX` |
| (sin sufijo, `N.N.N`) | `PROD` |

---

## 6. Análisis de `scripts/release.sh` — 16 comandos

### 6.1 Router y estructura

```
work | expand | status | add | drop | init | init-multi | sync
bump | tag | hotfix | worktree | cherry | check | promote | versions
```
(`release.sh:807-866`, 16 ramas de `case` más el fallback `help`)

### 6.2 Funciones auxiliares críticas

| Función | Línea | Propósito |
|---|---|---|
| `resolve_remote()` | 49-66 | Prioridad: `GITOPS_REMOTE` env > `origin` > `upstream` > primer remote disponible |
| `is_applied_to()` | 72-82 | Detecta si un SHA ya está en una rama por **dos** vías: ancestro directo (`merge-base --is-ancestor`) o patch-id equivalente (`git cherry`, cubre cherry-picks) |
| `_project_path()` | 100-117 | Resuelve el directorio de un proyecto desde `GITOPS_PROJECT_MAP`, o cae a `projects/<nombre>` — **desde 2026-08-05 (ADR-0049), retorna tal cual paths que ya empiezan con `shared/`/`scripts`** (ver sección 18) |
| `_manifest_kind_and_path()` | 122-129 | Detecta el manifiesto de versión: `pyproject.toml` (python) → `package.json` (node) → `VERSION` (go) |
| `require_full_history()` | 150-156 | Si detecta `.git/shallow`, hace `fetch --unshallow` automático (fix para CI shallow clone de Bitbucket) |
| `shared_deps_of()` | 183-207 | Extrae qué libs de `shared/libs/` consume un proyecto, leyendo `package.json` (regex `@scope/lib`) o `pyproject.toml` (regex `scope-lib`) — **solo en esta dirección, no existe el lookup inverso (ver sección 18.2)** |
| `_replicate_sparse_checkout()` | 715-723 | **Nuevo (2026-08-04, ADR-0048):** si el árbol principal tiene sparse-checkout activo, replica el mismo sparse-set dentro de un worktree recién creado. Portado desde `deacero/commons` y generalizado al modelo rama-céntrico de este script (no requiere argumento de proyecto). Se invoca desde `cmd_worktree()` en `add`/`add-branch`. |

**Nota de líneas:** los números de esta tabla y de toda la guía se re-verificaron el
2026-08-05 tras dos rondas de cambios reales: la homologación con `commons` (2026-08-04,
`release.sh` 838→858 líneas) y el fix de `_project_path()` (2026-08-05, 858→866 líneas,
ver sección 18). Cualquier línea posterior a la función `_project_path()` (línea 100) se
recorrió +8 respecto a lo citado en secciones anteriores de este documento — ya
actualizado en todas las citas de esta guía.

**Contrato de versión (ADR-0010, compartido con commons):** el manifiesto de cada
proyecto es la **única fuente de verdad** de la versión. `bump` la escribe (vía PR
revisado); `tag` la lee y publica. Nunca se pasa una versión "a ciegas" como argumento —
si se pasa `expected_version` a `cmd_tag`, es solo una aserción de seguridad, no el valor
que se usa.

### 6.3 Sparse checkout — 6 sabores

```
                              release.sh
                                  │
       ┌───────────┬────────────┼────────────┬───────────┬──────────────┐
       ▼           ▼            ▼            ▼           ▼              ▼
  work/init    init-multi      add          drop       expand      worktree
  <proyecto>   <a b c>       <path>        <path>                 add-branch
       │            │           │            │            │            │
  1 proyecto   multi-proy.  agregar shared  quitar del   checkout    rama
  + shared     exige        adicional al   set activo   completo    paralela,
  deps auto-   integration/*  set activo                (onboarding/ sparse
  detectados                                             CI/debug)   propio
                                                                    (ADR-0044,
                                                                   auto-sparse
                                                                    ADR-0048)
```

`cmd_work` (`release.sh:226-256`) arma el sparse-set como: `scripts` + directorio(s) del
proyecto + **shared deps detectados automáticamente** vía `shared_deps_of()`. Si son
≥2 proyectos, imprime el contrato de rama de integración (máx 2 semanas, merge `--no-ff`,
nunca squash).

> **Contrato exacto de `add`/`drop` (verificado con ejecución real, `release.sh:288-295`):**
> el argumento es `<path|proyecto>` — dos modos, no uno:
> - Si empieza con `shared/` o `scripts` → se usa tal cual como path literal
>   (ej. `add shared/libs/http-client`, el ejemplo del Sabor 2 más abajo).
> - Cualquier otro valor se trata como **nombre de proyecto** y se resuelve vía
>   `_project_path()` (que cae a `projects/<nombre>` sin `GITOPS_PROJECT_MAP`).
>
> Ejercicio verificado: `add proyecto-b` agrega correctamente `projects/proyecto-b`. Pero
> pasar ya el path completo (`add projects/proyecto-b`) hace que `_project_path()` le
> vuelva a anteponer `projects/`, dando `projects/projects/proyecto-b` — sin error, sin
> aviso, sparse-checkout simplemente agrega ese path (inexistente) silenciosamente. No es
> un bug de `release.sh` (el contrato `<path|proyecto>` está documentado en su propio
> mensaje de uso), pero es una confusión real y fácil de cometer — usa siempre el
> **nombre del proyecto a secas** con `add`/`drop`, nunca el path ya resuelto.

### 6.4 Hotfix — flujo completo

```
  Dev                 release.sh              Pipeline           Prod
   │                       │                       │                │
   │ hotfix proyecto-a     │                       │                │
   │ 1.4.2 fix-critico     │                       │                │
   ├──────────────────────►│                       │                │
   │                       │ require_tag_exists(proyecto-a@1.4.2)    │
   │                       │ checkout -b hotfix/proyecto-a/          │
   │                       │   fix-critico proyecto-a@1.4.2          │
   │                       │ (sparse reconfigurado auto p/proyecto-a)│
   │◄──────────────────────┤                       │                │
   │ fix + commit                                  │                │
   │ "fix(proyecto-a): ..."│                       │                │
   │ git push hotfix/proyecto-a/fix-critico         │                │
   ├───────────────────────┼──────────────────────►│                │
   │                       │                       │ tag proyecto-a@1.4.2-hotfix.1
   │                       │                       ├───────────────►│
   │                       │                       │  deploy directo a PROD
   │ cherry proyecto-a 1.4.2  (OBLIGATORIO)         │                │
   ├──────────────────────►│                       │                │
   │                       │ procesa TODOS los hotfix.N pendientes,  │
   │                       │ orden ascendente; cherry-pick a main    │
   │                       │ (para en conflicto real, skip si vacío) │
   │ check proyecto-a → confirma 0 pendientes       │                │
   ├──────────────────────►│                       │                │
```

`cmd_cherry` (`release.sh:517-598`) distingue un **conflicto real** de un **cherry-pick
vacío** (cambios ya presentes en trunk por otra vía): si `git cherry-pick` produce
`.git/CHERRY_PICK_HEAD` sin archivos en conflicto, hace `--skip` automático en vez de
pedir intervención manual.

### 6.5 `promote` — escape hatch, no flujo normal

`cmd_promote` (`release.sh:637-679`) solo acepta `dev|rc` (nunca `prod` — el tag final
de prod exige `require_trunk` + `require_clean` + verificación de hotfixes pendientes,
solo vía `cmd_tag`). Autoincrementa el sufijo `.N` buscando el máximo existente.

---

## 7. Análisis de `scripts/gitops-init.sh` — bootstrap en 5 pasos

```
  gitops-init.sh --target /repo --stack python,typescript
                 --project-map backend:backend,frontend:.
                              │
                              ▼
                  Validaciones: target≠ai-tooling,
                  es repo git, templates existen
                              │
                              ▼
              Resolver STACKS y PROJECT_MAP
              (flag → env → wizard interactivo)
                              │
                              ▼
        1/5  copiar release.sh + generar .gitops-env
                              │
                              ▼
        2/5  copiar check_adr_gate.py + install_hooks.sh
                              │
                              ▼
        3/5  generar CODEOWNERS desde template
                              │
                              ▼
        4/5  generar .claude/adr-gate.conf +
             .pre-commit-config.yaml (por stack)
                              │
                              ▼
        5/5  pre-commit install +
             pre-commit install --hook-type commit-msg
                              │
                              ▼
              Resumen final + próximos pasos
```

Puntos verificados en el código:
- **Idempotencia parcial**: si `scripts/release.sh` ya existe lo sobreescribe (con
  warning), pero `CODEOWNERS`, `.gitops-env` y `.pre-commit-config.yaml` **no se
  sobreescriben** si ya existen (`gitops-init.sh:210-233`, `256-263`, `292-294`).
- **`generate_adr_gate_rules()`** (líneas 138-153) detecta estructura real del repo
  destino (`vendor/`, `src/`, `projects/`, `.agents/`) y genera reglas
  `PREFIJO EXTENSION_REGEX` para `.claude/adr-gate.conf` — única fuente de verdad
  compartida entre el hook de Claude Code y el pre-commit de git.
- **`.pre-commit-config.yaml` generado por stack**: siempre incluye hooks genéricos
  (trailing-whitespace, check-yaml, `no-commit-to-branch` sobre el trunk) + bloque
  condicional por stack (`ruff` si `python`, `eslint`/`prettier` si `node|typescript`,
  `gofmt`/`golangci-lint` si `go`) + ADR gate + Conventional Commits siempre.
- **`--dry-run`** cubre las 5 secciones sin escribir nada real — usa las mismas
  funciones `copy_file`/`write_file` con rama condicional (`gitops-init.sh:47-69`).

---

## 8. Ambientes DEV / QA / PROD — cómo se materializan

**Los ambientes no son ramas — son el resultado de qué patrón de tag disparó el pipeline.**
Fuente: `templates/gitops/bitbucket-pipelines.yml.template:126-187`.

```
  push a main
       │
       ▼
  Detect changed paths (git diff origin/main~1 origin/main)
       │
       ▼
  parallel: Build+Test — solo proyectos con paths cambiados
       │
       ▼
  Deploy automático → DEV   (tag *@*-dev.*, auto, sin gate)
       │
       ▼
  ┌─────────────────────┐
  │ e2e OK + revisión QA │
  └─────────┬────────────┘
            │ [trigger manual]
            ▼
  tag *@*-rc.*  ──────────────────────►  deployment: QA
            │
            ▼
  ┌──────────────────────┐
  │ Aprobación humana      │
  └─────────┬──────────────┘
            │ [trigger manual]
            ▼
  tag *@[0-9]*.[0-9]*.[0-9]* (sin sufijo)
  (rechaza si detecta sufijo dev/rc/hotfix) ──►  deployment: Production


  hotfix/<proyecto>/*  ─ ─ ─►  tag *@*-hotfix.*  ──►  deployment: Production (manual)
```

Detalle de gates (`bitbucket-pipelines.yml.template`):

| Patrón de tag | Ambiente Bitbucket | Trigger | Línea |
|---|---|---|---|
| `*@*-dev.*` | `Dev` | automático | 129-137 |
| `*@*-rc.*` | `QA` | `manual` | 140-149 |
| `*@[0-9]*.[0-9]*.[0-9]*` (sin sufijo) | `Production` | `manual`, con step previo que **rechaza** si detecta sufijo `-dev/-rc/-hotfix` | 151-173 |
| `*@*-hotfix.*` | `Production` | `manual` | 176-186 |

El deploy real (`./scripts/deploy.sh $PROJECT $ENV $VERSION`) es un placeholder del
template — **no está en el repo un `deploy.sh` concreto**; el header del template deja
explícito que `DEPLOY_CMD` se personaliza por stack (`kubectl apply`, script propio, etc.).
No asumir un mecanismo de deploy específico sin verificarlo en el repo destino real.

---

## 9. Pipeline → artefactos concretos

Hay **dos templates de pipeline** con propósitos distintos — no confundirlos:

### 9.1 `bitbucket-pipelines.yml.template` — aplicaciones (deploy genérico)
Path-based change detection + `deployment: Dev/QA/Production` de Bitbucket. El artefacto
final depende de `DEPLOY_CMD`, que el template deja como placeholder. No hay evidencia en
el repo de qué registro/target usa (Docker Registry, Cloud Run, K8s) — se personaliza por
proyecto real.

### 9.2 `bitbucket-pipelines-library-publish.yml.template` — librerías (concreto: GCP)
Este template **sí especifica el artefacto exacto**: publica paquetes de
`shared/libs/` a **GCP Artifact Registry** vía **Workload Identity Federation (OIDC)**,
sin credenciales estáticas (`oidc: true` + `gcloud iam workload-identity-pools
create-cred-config`, líneas 32-39).

```
       Fuente                    CI (WIF/OIDC,               GCP Artifact
                                sin secrets estáticos)          Registry

  python/auth/            make publish              Registro PyPI
  pyproject.toml   ─────► (python-auth@X.Y.Z) ─────► privado

  typescript/auth/        make publish              Registro npm privado
  package.json     ─────► (typescript-auth@X.Y.Z) ─► (scope @NPM_SCOPE)

  go/auth/                 make publish              Go module proxy
  VERSION          ─────► (go-auth@X.Y.Z)    ─────►
```

| Tipo de artefacto | Trigger de publish | Versión aplicada |
|---|---|---|
| Paquete PyPI | branch `dev` (auto) o tag `python-<nombre>@*` | `dev`: base + `.dev.$BUILD_NUMBER` / tag: versión exacta del tag |
| Paquete npm (`@scope/nombre`) | branch `dev` (auto) o tag `typescript-<nombre>@*` | igual patrón `-dev.$BUILD_NUMBER` / versión de tag |
| Módulo Go (vía `VERSION` file) | branch `dev` (auto) o tag `go-<nombre>@*` | `v$(cat VERSION)-dev.$BUILD_NUMBER` / `v<tag>` |

**Regla clave del template:** en `main`, las librerías **no se publican automáticamente**
(solo corre el quality gate: test). La publicación real solo ocurre por **tag explícito**
`<proyecto>@<version>` — el mismo esquema que genera `release.sh tag`. Esto es consistente
con el contrato de ADR-0007: nada llega a un registro consumible sin pasar por
`bump` → PR revisado → `tag` desde trunk limpio.

---

## 10. CODEOWNERS y ownership (`templates/gitops/CODEOWNERS.template`)

Patrón por prioridad (más específico gana):

```
*                                        @equipo-platform          (catch-all)
/projects/proyecto-a/                    @equipo-proyecto-a
/projects/proyecto-b/                    @equipo-proyecto-b
/projects/proyecto-c/                    @equipo-proyecto-c
/shared/libs/auth/    @equipo-platform @equipo-proyecto-a @equipo-proyecto-b   (multi-approval)
/scripts/ , bitbucket-pipelines.yml, /docs/adr/, /.agents/   @equipo-platform
```

**Advertencia explícita del ADR-0007:** *"CODEOWNERS sin branch restrictions en Bitbucket
es documentación, no enforcement"* — hay que activar "Require approvals from Code Owners"
en Bitbucket para que el archivo tenga efecto real, no solo informativo.

---

## 11. Equipo de 20 personas en 4 proyectos — modelo de interacción

Este modelo aplica el contrato de ADR-0007/ADR-0044 a un caso concreto: **4 proyectos**
dentro del mismo monorepo, **~5 personas por proyecto** (20 total), más `shared/libs/`
como terreno común.

### 11.1 Estructura de equipos y ownership

| Equipo | Proyecto | Directorio | Tamaño típico |
|---|---|---|---|
| Equipo A | `proyecto-a` | `projects/proyecto-a/` | ~5 |
| Equipo B | `proyecto-b` | `projects/proyecto-b/` | ~5 |
| Equipo C | `proyecto-c` | `projects/proyecto-c/` | ~5 |
| Equipo D | `proyecto-d` | `projects/proyecto-d/` | ~5 |
| Platform (transversal) | `shared/libs/*` | `shared/libs/` | subset de cada equipo, revisor obligatorio |

### 11.2 Aislamiento por sparse checkout — qué ve cada desarrollador

```
              Monorepo completo (git ve TODO el historial siempre)
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
  Dev de Equipo A            Dev de Equipo B            Dev de Platform
  release.sh work            release.sh work            release.sh init-multi
  proyecto-a                 proyecto-b                 shared/libs/auth

  ve: scripts/ +             ve: scripts/ +             ve: scripts/ +
  projects/proyecto-a/ +     projects/proyecto-b/ +     shared/libs/auth
  shared/libs/auth           shared/libs/logging        (SOLO auth — sin
  (detectado automático)                                consumers, ver nota)
```

> **Corrección (2026-08-05, ver sección 18):** la columna de Platform arriba refleja el
> comportamiento real *después* del fix de `ADR-0049` — antes de ese fix, este comando
> **fallaba** (`ERROR: no existe projects/shared/libs/auth`). Además, `init-multi
> shared/libs/auth` **nunca** trae "TODOS sus consumers" automáticamente — esa capacidad
> no existe en `release.sh` (no hay lookup inverso shared→consumers). Si Platform necesita
> validar consumers, debe agregarlos manualmente: `add proyecto-a`, `add proyecto-b`.

Regla de oro repetida en la skill: *"lo que no ves en disco sigue existiendo en git"* —
`git log`/`git tag`/`git diff` siempre ven el monorepo completo, el sparse solo afecta
qué se materializa en disco.

### 11.3 Los 4 equipos trabajando en paralelo — una semana típica

```
  Equipo A    Equipo B    Equipo C   Equipo D   Platform    main       Pipeline
     │           │           │          │          │          │           │
     │ feature/pagos-v2 (3 días máx)               │          │           │
     ├────────────────────────────────────────────────────────►│         │
     │           │ feature/reportes (3 días máx)   │          │           │
     │           ├──────────────────────────────────────────────►│       │
     │           │           │          │ cambio en shared/libs/auth      │
     │           │           │          │          ├──────────►│         │
     │           │           │          │          │           │ shared cambió →
     │           │           │          │          │           │ testea TODOS
     │           │           │          │          │           │ los consumers
     │           │           │          │          │           ├─────────►│
     │◄──────────────────────────────────────────────────────────────────┤ build
     │           │◄─────────────────────────────────────────────────────┤ proyecto-a/b OK
     │           │           │          │          │           │           │
     │           │           │ integration/refactor-datos      │           │
     │           │           │ (equipo C+D, hasta 2 semanas)    │           │
     │           │           ├──────────►│          │           │          │
     │           │           │◄──────────┤ sync diario obligatorio         │
     │           │           │          │          │           │           │
     │ PR mergeado → tag proyecto-a@1.4.2           │           │          │
     ├────────────────────────────────────────────────────────►│          │

  proyecto-b, proyecto-c, proyecto-d NO se mueven — siguen en su propia versión.
```

**Punto crítico de coordinación:** solo el cambio en `shared/libs/auth` obliga a
sincronización real entre equipos (CI testea automáticamente a todos los consumers antes
del merge — no requiere que Equipo A y B se coordinen manualmente, el pipeline es el
mecanismo de coordinación). El resto del tiempo, los 4 equipos son independientes.

### 11.4 Concurrencia: hotfix urgente mientras hay features en progreso (ADR-0044)

Escenario real con 20 personas: es común que mientras Equipo A tiene 2-3 features
abiertos, llegue un hotfix crítico de prod para `proyecto-a`. Sin worktrees, el
desarrollador asignado tendría que `git stash` y perder su working tree visual.

```
  Dev de Equipo A
  working tree: feature/pagos-v2
         │
         │  worktree add-branch hotfix/proyecto-a/fix-auth
         │  ../wt-fix-auth  proyecto-a@1.4.2
         ▼
  ../wt-fix-auth
  (worktree aislado, sparse propio — replicado automático
   del sparse-set activo, ver ADR-0048)
         │
         │  fix + push + cherry
         ▼
        main

  El working tree de feature/pagos-v2 queda INTACTO todo el tiempo —
  no hace falta git stash.
```

Límite práctico documentado: **máx. 3-4 worktrees simultáneos** por persona — más indica
trabajo estancado, no paralelismo saludable. Con 20 personas, esto es una disciplina de
equipo (revisar `release.sh status` diariamente), no un límite del script.

### 11.5 Matriz de riesgo de conflicto entre los 4 equipos

| Área tocada | Frecuencia esperada de conflicto | Mitigación en el modelo |
|---|---|---|
| `projects/proyecto-X/` propio | Baja — cada equipo es dueño exclusivo (CODEOWNERS) | Sparse checkout — ni siquiera ven el código de los otros por defecto |
| `shared/libs/*` | Media-alta — punto de contacto real entre equipos | CI testea todos los consumers automáticamente antes del merge; CODEOWNERS exige aprobación de Platform + consumers |
| `integration/*` multi-proyecto (ej. refactor cross-equipo) | Ocasional, por diseño (Sabor 4) | Contrato explícito: `--no-ff`, nunca squash, `sync` diario obligatorio, vida máx 2 semanas |
| `scripts/`, `bitbucket-pipelines.yml`, `.agents/` | Baja pero de alto impacto si ocurre | CODEOWNERS: solo Platform aprueba; ADR gate bloquea edición sin ADR en staging |

### 11.6 Qué NO resuelve el modelo (para 20 personas)

- No hay enforcement automático del límite "3 días" para `feature/*` ni "2 semanas" para
  `integration/*` — son convenciones documentadas, no bloqueadas por script (no se
  encontró ningún check de antigüedad de rama en `release.sh` ni en los templates de
  pipeline revisados).
- La limpieza de tags `-dev.*` > 30 días es una recomendación de ADR-0007, no un job
  automatizado visible en los templates leídos.
- `release.sh worktree clean` tiene un falso negativo documentado si la rama local ya
  fue borrada (ADR-0044) — con 20 personas generando muchos worktrees, requiere revisión
  manual periódica (`release.sh status`).

---

## 12. Checklist de adopción (extraído de la skill, sin modificar)

### Prerequisito
- [ ] Branch restrictions en Bitbucket (CODEOWNERS sin esto es solo documentación)
- [ ] Decidir `master`→`main` o `GITOPS_TRUNK_BRANCH=master`

### Semana 1-2 → Semana 7+
Ver tabla completa en `.agents/skills/infrastructure/gitops-monorepo/SKILL.md:249-276`
(no se reproduce aquí completa para no duplicar la fuente de verdad — consultar el
SKILL.md directamente, es el documento canónico).

---

## 13. Gotchas conocidos (tabla completa, 14 filas verificadas en SKILL.md:398-417)

Dos filas nuevas desde el análisis original (2026-08-04): hallazgos de producción real de
`deacero/commons`, portados a esta tabla vía `ADR-0048` (ver sección 16).

| Situación | Problema | Solución implementada |
|---|---|---|
| macOS / Alpine | `grep -oP` no soportado | Script usa `grep -o` POSIX |
| CI shallow clone | `merge-base --is-ancestor` falla | `require_full_history()` hace `--unshallow` automático |
| Cherry-pick detection | `merge-base` no detecta cherry-picks | Fallback con `git cherry` (patch-id) |
| Repo sin `origin` | `git push origin` falla | Detección de remotes; `GITOPS_REMOTE` |
| Repo con `master` | `require_trunk` rechaza | `GITOPS_TRUNK_BRANCH=master` |
| Tags anotados | `rev-parse` da SHA del tag object | Script usa `^{commit}` siempre |
| `hotfix.10` vs `hotfix.2` | Sort lexicográfico desordena | Extrae N, `sort -n` |
| Multiple hotfixes | Solo se cherry-pickeaba el último | Itera todos en orden ascendente |
| Tag remoto no visible | `require_tag_not_exists` puede fallar | `cmd_tag` hace `fetch --tags` antes |
| Sparse activo + `work` a otro proyecto | dir no visible → "no existe" | Usa `git ls-tree` para verificar existencia |
| `project-map` con `.` (raíz) | `work` en cone mode incluye solo top-level | Esperado — usar `expand` si se necesita todo el árbol |
| `core.hooksPath` ya seteado | `pre-commit install` rechaza | `git config --unset-all core.hooksPath` y reintentar |
| **(nuevo)** macOS bash de sistema (3.2) | `mapfile`/`readarray` no existen | `release.sh` no los usa; en scripts nuevos (ej. `sync_skills.sh`) usar `while read` en vez de `mapfile` |
| **(nuevo)** `.claude/adr-gate.conf` con `adr_path: docs/adr` | `tr -d '/'` borraba el `/` interno → gate bloqueaba SIEMPRE | Usar `sed 's:/*$::'` (solo quita `/` final) — ya es lo que usa `adr-gate.sh` de este repo |

---

## 14. Qué NO fue revisado en este análisis (modo analysis, transparencia obligatoria)

- **`deploy.sh` real**: no existe en `ai-tooling` un script `deploy.sh` concreto — el
  template de pipeline de aplicaciones lo referencia como placeholder. No se puede
  documentar el mecanismo real de despliegue (K8s, Cloud Run, VM) sin verlo en un repo
  destino específico.
- **`.env` / secrets reales de WIF (`WIF_PROVIDER`, `GCP_DEPLOY_SA`, `GCP_PROJECT`)**: no
  se leyeron valores reales — son variables de repositorio de Bitbucket, fuera del
  alcance de este análisis de código y correctamente fuera del chat (dato sensible de
  configuración de producción).
- **Configuración real de Bitbucket** (branch restrictions, tag protection, entornos
  `Dev/QA/Production`): esto vive en la UI de Bitbucket, no en el repositorio de código;
  no verificable por lectura de archivos.
- **`.agents/skills/infrastructure/gitops-monorepo/SKILL.md` sección "Worktrees en
  workflows multi-agente"**: se leyó y se referencia en la sección 11.4, pero no se
  auditó el hook `worktree-isolation-gate.sh` en sí en este análisis original (fuera del
  alcance declarado entonces: skill + gitops-init.sh + release.sh).

> **Actualización (2026-08-04, sesión posterior):** el último punto de esta lista ("no se
> ejecutó ningún comando de `release.sh` contra un repo real") **ya no aplica** — la
> homologación con `commons` (sección 16) sí ejecutó `worktree add-branch`, `status`,
> `clean`, `rm`, `prune` contra el `release.sh` real de este repo (en un clon temporal) y
> contra el `release.sh` real de `commons` (en vivo, con limpieza completa posterior), y
> probó `block-dangerous.sh`/`worktree-isolation-gate.sh` con inputs reales. El resto de
> los puntos de esta lista (deploy.sh, secrets WIF, config de Bitbucket) sigue sin
> revisarse — fuera de alcance de cualquiera de las dos sesiones.

---

## 15. Referencias cruzadas

- `docs/adr/ADR-0007-gitops-monorepo-trunk-based.md` — decisión original
- `docs/adr/ADR-0044-worktree-gitops-integration.md` — extensión de worktrees (renumerado
  desde ADR-0008; **Estado: Aceptado** desde 2026-08-04 — la implementación ya existía,
  nunca se había marcado formalmente)
- `docs/adr/ADR-0048-homologacion-gitops-monorepo-commons.md` — (2026-08-04): puerto
  bidireccional de mejoras con `deacero/commons`, ver sección 16
- `docs/adr/ADR-0049-fix-project-path-shared-only.md` — nuevo (2026-08-05): fix del
  escenario "solo shared" roto en `_project_path()`, ver sección 18
- `docs/adr/ADR-0010-manifiesto-fuente-de-verdad-version.md` (referenciado por release.sh, no leído en este análisis — mencionado solo como pointer del propio código)
- `ai-notes/analysis/commons-gitops-analysis-2026-08-04.md` — análisis exhaustivo de
  `deacero/commons` (instancia de producción de este mismo patrón) que originó la
  homologación de la sección 16

---

## 16. Homologación con `deacero/commons` (2026-08-04, sesión posterior a este análisis)

Un análisis separado encontró que `deacero/commons` — una instancia real de producción de
este mismo patrón GitOps, bootstrapeada hace meses con `gitops-init.sh` — había evolucionado
de forma independiente desde entonces, sin canal de retorno hacia `ai-tooling`. Esa sesión
ejecutó una homologación bidireccional; lo que cambia esta guía:

### 16.1 Cambios en `release.sh` de este repo

```
  ANTES                                    DESPUÉS (ADR-0048)
  ┌──────────────────────────┐             ┌──────────────────────────────────┐
  │ cmd_worktree()            │             │ cmd_worktree()                    │
  │   add | add-branch | rm | │             │   add | add-branch | rm |         │
  │   prune | clean | list    │             │   prune | clean | list            │
  │                            │             │                                    │
  │   sin auto-sparse ✘        │   ───►      │   + _replicate_sparse_checkout()  │
  │                            │             │     en add/add-branch ✔           │
  │                            │             │     (portado de commons,          │
  │                            │             │      generalizado a rama-céntrico)│
  │                            │             │                                    │
  │ cmd_worktree clean/        │             │ mismo código + fix: `|| true`     │
  │ cmd_check con bug          │             │ tras `[[ $found -eq 0 ]] && ok`   │
  │ set -e + && sin guarda ✘   │             │ — ya no aborta con candidatos ✔   │
  └──────────────────────────┘             └──────────────────────────────────┘
```

**El bug de `set -e` (encontrado en sandbox, no en el análisis original):** con
`set -euo pipefail` activo, `[[ $found -eq 0 ]] && ok "..."` como sentencia standalone —
sin `else` ni `|| true` — hace que el script aborte silenciosamente cuando `found=1` (el
caso *útil*, cuando SÍ hay un worktree candidato a limpiar o un hotfix pendiente). Afectaba
tanto a `cmd_worktree clean` como a `cmd_check`. Reproducido en un repo git aislado antes de
tocar el archivo real; corregido con `|| true` al final de la línea.

### 16.2 `.agents/skills/infrastructure/gitops-monorepo/SKILL.md`

- 2 filas nuevas en la tabla de gotchas (sección 13 de esta guía, ya actualizada arriba).
- Nota agregada en la sección "Worktrees" documentando el auto-sparse.

### 16.3 Hallazgo colateral significativo: bug en `tools/check_adr_gate.py`

No estaba en el alcance original de esta guía (que excluye explícitamente `check_adr_gate.py`,
ver sección 14), pero se documenta aquí por su severidad: al comitear el trabajo de la
homologación, el pre-commit hook de este repo reportó "0 archivos guardados" pese a tener
`.agents/skills/.../SKILL.md` staged — exactamente el guarded pattern más sensible del gate.

Causa: `_normalise()` usaba `p.lstrip("./")` para quitar un prefijo `"./"` — pero
`str.lstrip(chars)` quita repetidamente cualquier carácter del *set* `{'.', '/'}` desde la
izquierda, no el substring literal. Para una ruta como `.agents/skills/x.md`, eso se come el
punto inicial (queda `agents/skills/x.md`), y el `fnmatch` contra `.agents/skills/**/*.md`
deja de matchear — **el gate quedaba silenciosamente inerte** para esa ruta. El hook bash
paralelo (`adr-gate.sh`, que usa `${FILE#./}` — expansión de parámetro de prefijo literal,
no `lstrip`) nunca tuvo este bug.

```
  ANTES                                          DESPUÉS
  ".agents/skills/x.md".lstrip("./")             while p.startswith("./"):
    → "agents/skills/x.md"  (¡se comió el punto!)     p = p[2:]
                                                   → ".agents/skills/x.md"  (intacto)

  fnmatch(".agents/skills/x.md" *sin punto*,     fnmatch(".agents/skills/x.md",
          ".agents/skills/**/*.md")                      ".agents/skills/**/*.md")
    → False  (gate NUNCA se activa)                → True  (gate funciona)
```

Corregido en `ai-tooling/tools/check_adr_gate.py`, y el mismo bug (heredado al portar este
archivo) en `commons/tools/check_adr_gate.py` y en `wpc-backend/tools/check_adr_gate.py`
(variante más simple, ahí sí explotable en producción real porque
`.agents/skills/**/*.md` es uno de sus 2 únicos guarded patterns hardcodeados).

### 16.4 Validación aplicada

Sandbox aislado (7 escenarios: add-branch con sparse activo/inactivo, add sobre rama
existente, rm, clean con/sin candidatos, prune, list) → clon temporal de `ai-tooling` real
(mismos 7 escenarios, confirmando integración con `trunk_branch()`/`resolve_remote()`/
`require_arg()` reales) → commit + push. 37 tests existentes en `tools/tests/` verificados
sin regresión tras el fix de `check_adr_gate.py`.

### 16.5 Qué NO se tocó en esta guía por la homologación

- `scripts/gitops-init.sh` — sin cambios en esta sesión.
- Los templates de `templates/gitops/` — sin cambios.
- El conteo de "16 comandos + 6 subcomandos de worktree" (sección 6.1) — no cambia; la
  homologación agregó comportamiento (auto-sparse) y corrigió un bug dentro de subcomandos
  ya existentes, no agregó subcomandos nuevos.

---

## 17. Validación funcional end-to-end de los ejercicios (segunda revisión)

Todo lo anterior (secciones 1-16) fue análisis de código. Esta sección documenta una
**segunda pasada de validación**: se construyó un sandbox real (repo bare + clon de trabajo,
con `shared/libs/auth`, `projects/proyecto-a`, `projects/proyecto-b`, manifiestos
`pyproject.toml`) y se ejecutó, contra el `release.sh` real de este repo, cada "ejercicio"
que la guía documenta — no solo se releyó el código, se corrió de verdad.

| # | Ejercicio (sección de origen) | Comando ejecutado | Resultado |
|---|---|---|---|
| 1 | `status` baseline (§6) | `release.sh status` | ✓ remote/trunk/branch/sha correctos, sin sparse |
| 2 | `work` + auto-detect de shared deps (§6.3) | `release.sh work proyecto-a` | ✓ sparse-set = `scripts` + `projects/proyecto-a` + `shared/libs/auth` (detectado automático) |
| 3 | `bump` (§6.2, contrato ADR-0010) | `release.sh bump proyecto-a 1.4.2` | ✓ escribe `version = "1.4.2"` en el manifiesto |
| 4 | `promote dev` + `promote rc` (§5) | `release.sh promote proyecto-a 1.4.2 dev` / `... rc` | ✓ crea `-dev.1` y `-rc.1`, autoincrementa sufijo, empuja a remoto |
| 5 | `tag` final (§5) | `release.sh tag proyecto-a` | ✓ lee 1.4.2 del manifiesto, crea `proyecto-a@1.4.2` |
| 6 | **El ejercicio exacto de la sección 5**: verificar mismo SHA | `git diff proyecto-a@1.4.2-rc.1 proyecto-a@1.4.2` | ✓ **diff vacío — confirmado**, los 3 tags apuntan al mismo commit |
| 7 | `versions` — clasificación por sufijo (§5) | `release.sh versions proyecto-a` | ✓ clasifica exactamente como la tabla de la sección 5: PROD/DEV/QA |
| 8 | `check` sin pendientes (§6.4) | `release.sh check proyecto-a` | ✓ "sin hotfixes pendientes" — confirma también el fix de `set -e` en el caso `found=0` |
| 9 | Flujo hotfix completo (§6.4, diagrama) | `hotfix` → fix+commit+push → tag simulado de CI → `check` (detecta pendiente) → `cherry` (aplica + re-check) | ✓ **coincide exactamente** con el diagrama de secuencia de la sección 6.4, paso a paso |
| 10 | `worktree add-branch` + auto-sparse (§11.4) | `worktree add-branch hotfix/proyecto-a/fix-auth ../wt-fix-auth proyecto-a@1.4.2` | ✓ sparse replicado automáticamente dentro del worktree, idéntico al set activo |
| 11 | `worktree list` / `clean` / `rm` / `prune` | — | ✓ los 4 subcomandos funcionan; limpieza completa sin residuos |
| 12 | `add`/`drop` con nombre de proyecto y con path `shared/` | `add proyecto-b`, `drop proyecto-b`, `add shared/libs/http-client` | ✓ ambos modos del contrato `<path\|proyecto>` funcionan — ver nota nueva en sección 6.3 |
| 13 | `init-multi` (Sabor 3) | `release.sh init-multi proyecto-a proyecto-b` | ✓ sparse con ambos proyectos + imprime contrato de `integration/*` (máx 2 semanas, `--no-ff`) |
| 14 | `expand` (Sabor 4) | `release.sh expand` | ✓ checkout completo, los 3 directorios top-level visibles |
| 15 | Líneas citadas de `gitops-init.sh` (§7) | `sed -n` sobre `copy_file`/`write_file`/`generate_adr_gate_rules` | ✓ líneas 48, 60, 138 y los rangos de idempotencia (210-233, 256-263, 292-294) correctos |
| 16 | Líneas citadas de `bitbucket-pipelines.yml.template` (§8) | `sed -n '126,187p'` | ✓ gates Dev/QA/Production y triggers manuales en las líneas citadas |
| 17 | Líneas citadas de OIDC en library-publish template (§9.2) | `sed -n '32,39p'` | ✓ bloque `gcloud iam workload-identity-pools create-cred-config` confirmado en 32-39 |

**Único hallazgo nuevo:** el contrato dual de `add`/`drop` (`<path|proyecto>`) puede
confundirse si se le pasa un path ya resuelto en vez del nombre de proyecto — documentado
como nota nueva al final de la sección 6.3, no es un bug de `release.sh` (el comportamiento
es exactamente el que su propio mensaje de uso declara).

**Todo lo demás verificado coincide exactamente con lo documentado en las secciones 1-16.**
Sandbox construido y destruido por completo en esta misma sesión — no queda ningún artefacto
de prueba en ningún repo real.

---

## 18. Auditoría de los 4 escenarios de sparse checkout para equipos (2026-08-05)

Pregunta que motivó esta sección: para un equipo de 20 personas en 4 proyectos + shared
(sección 11), ¿están los 4 escenarios de trabajo realmente soportados? — no solo
documentados, sino verificados funcionando contra el `release.sh` real.

| Escenario | Comando | Antes de esta sección | Después (2026-08-05) |
|---|---|---|---|
| **A. Un proyecto** | `work proyecto-a` | ✓ Documentado y verificado (§17) | Sin cambios |
| **B. Múltiples proyectos** | `init-multi proyecto-a proyecto-b` | ✓ Documentado y verificado (§17) | Sin cambios |
| **D. Múltiples proyectos + shared** | `init-multi proyecto-a proyecto-b` (shared se detecta solo, por proyecto) | ✓ Funciona — nunca estuvo roto | Sin cambios |
| **C. Solo shared (Platform)** | `init-multi shared/libs/auth` | ✗ **`ERROR: no existe projects/shared/libs/auth`** | ✓ **Arreglado** |

### 18.1 El bug: `_project_path()` no distinguía un path literal de un nombre de proyecto

```
  ANTES                                       DESPUÉS (ADR-0049)
  _project_path("shared/libs/auth")           _project_path("shared/libs/auth")
    │                                           │
    ▼                                           ▼
  sin GITOPS_PROJECT_MAP con esa key           empieza con "shared/" →
  → "projects/" + "shared/libs/auth"             return "shared/libs/auth" tal cual
  → "projects/shared/libs/auth"  ✘ (no existe)  → "shared/libs/auth"  ✓ (correcto)
```

`cmd_add`/`cmd_drop` ya tenían este caso especial inline
(`if path != shared/* && path != scripts*`) — nunca se centralizó en `_project_path()`,
que es la función que usan `cmd_work`/`init-multi`, `cmd_hotfix`, `shared_deps_of` y
`_manifest_kind_and_path`. Por eso `add shared/libs/http-client` (Sabor 3, sección 6.3)
siempre funcionó, pero `init-multi shared/libs/auth` (Sabor 2 del `SKILL.md`) nunca
funcionó bajo configuración default (sin `GITOPS_PROJECT_MAP` con una entrada explícita
para esa key exacta).

### 18.2 Hallazgo colateral: "descubre consumers automáticamente" no existe

El comentario del `SKILL.md` en el Sabor 2 (*"baja auth + todos sus consumers
automáticamente"*) no tiene ninguna función de soporte — `grep -c "consumer\|reverse"
scripts/release.sh` → `0`. `shared_deps_of()` solo resuelve en una dirección
(proyecto → sus shared deps que consume), no existe el lookup inverso (shared lib → qué
proyectos la consumen). Se corrigió la documentación del `SKILL.md` para no prometer una
capacidad inexistente — agregar el reverse-lookup real sería una *feature* nueva, fuera de
alcance de este fix (ver `ADR-0049`, sección "Fuera de alcance").

### 18.3 Fix aplicado — mismo contrato compartido, 3 repos

`_project_path()` gana un branch que retorna el path tal cual si empieza con `shared/` o
`scripts`, ANTES del lookup de `GITOPS_PROJECT_MAP` y del fallback `projects/<nombre>`.
Aplicado idéntico en:

- `ai-tooling/scripts/release.sh` — `ADR-0049-fix-project-path-shared-only.md`
- `deacero/commons/scripts/release.sh` — `ADR-0012-fix-project-path-shared-only.md`
  (sin superficie de uso hoy — este repo no tiene `shared/libs/` materializado — pero se
  porta para mantener el contrato byte-idéntico entre los 3 repos, principio ya
  establecido en `ADR-0008`/`ADR-0011` de `commons`)
- `wpc-backend/scripts/release.sh` — mismo fix, sin ADR (esa ruta no está guardada por el
  ADR-gate de ese repo)

### 18.4 Validación

Sandbox aislado con `shared/libs/auth` + `shared/libs/logging` + `projects/proyecto-a`
(consumidor de auth) + `projects/proyecto-b` (consumidor de auth) + `projects/proyecto-c`
(**NO consumidor de nada** — deliberado, para probar que "solo shared" de verdad no
depende de tener un proyecto que lo use):

- `init-multi shared/libs/auth` → sparse = `scripts + shared/libs/auth`, sin `proyecto-c`
  ni ningún otro proyecto — **confirma que el escenario C ya no requiere ser consumidor**.
- Regresión: `add shared/libs/logging`, `work proyecto-a` (escenario A), `init-multi
  proyecto-a proyecto-b` (escenarios B/D), `hotfix proyecto-a 1.0.0` con sparse en modo
  "solo shared" activo previamente — **los 5 sin cambios de comportamiento**.
- `bash -n` en los 3 `release.sh` reales tras aplicar el fix.

### 18.5 Corrección a la sección 11.2

El diagrama de la sección 11.2 mostraba "Dev de Platform ... ve: auth + TODOS sus
consumers (proyecto-a, proyecto-b)" — **eso nunca fue cierto** (ver 18.2). Se corrigió el
diagrama y se agregó una nota explícita ahí mismo.
