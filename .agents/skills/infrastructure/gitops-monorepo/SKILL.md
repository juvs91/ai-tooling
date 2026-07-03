---
name: gitops-monorepo
description: Estrategia GitOps monorepo Deacero-específica. Usar cuando se hable de monorepo, trunk-based development, sparse checkout, independencia de despliegue entre proyectos, tag por proyecto, o adopción del modelo tag=versión en prod.
version: "1.0.0"
triggers:
  - monorepo
  - trunk-based
  - sparse checkout
  - tag por proyecto
  - independencia de despliegue
  - release por proyecto
  - contaminar ambientes
  - ramas compartidas
  - gitops deacero
---
# GitOps Monorepo — Deacero

Ref: `docs/adr/ADR-0007-gitops-monorepo-trunk-based.md` | `docs/adr/ADR-0008-worktree-gitops-integration.md`

> **Diferencia con `gitops-expert`:** ese skill cubre principios genéricos GitOps/IaC.
> Este skill es la implementación **Deacero-específica**: trunk-based + sparse checkout + `tag = versión en prod por proyecto`.

---

## El problema

```
proyecto-a  ─┐
proyecto-b  ─┼──► rama dev ──► rama qa ──► rama prod
proyecto-c  ─┘
```

Para deployar `proyecto-a` hay que jalar también lo que esté en `dev` de los otros proyectos.
Los ambientes se contaminan. La causa raíz: mentalidad multirepo dentro del monorepo —
cada proyecto depende de en qué **rama** está parado, no de qué **versión** decidimos promover.

---

## La solución: `tag = versión en prod`

```
main:  A──B──C──D──E──F──G   (sigue creciendo)
                  │
                  └── tag: proyecto-a@1.4.2
                          └── esto y solo esto está en prod
```

---

## Estructura del monorepo

```
repo/
├── shared/
│   └── libs/
│       ├── auth/          @deacero/auth       ← mínimo 2 proyectos deben consumirla
│       └── logging/       @deacero/logging
├── projects/
│   ├── proyecto-a/
│   ├── proyecto-b/
│   └── proyecto-c/
└── scripts/
    └── release.sh
```

**Regla de shared:** si solo un proyecto la usa, no es shared. Va dentro del proyecto.

---

## Tag naming

```
proyecto-a@1.4.2              ← producción (inmutable)
proyecto-a@1.4.2-rc.1         ← aprobado en QA
proyecto-a@1.4.2-dev.1        ← build en DEV
proyecto-a@1.4.2-hotfix.1     ← hotfix aplicado en prod
```

---

## Ramas permitidas (solo estas)

| Tipo | Patrón | Vida | Origen |
|------|--------|------|--------|
| Trunk | `main` | Permanente | — |
| Feature | `feature/*` | máx 3 días | trunk |
| Integración | `integration/*` | máx 2 semanas | trunk |
| Hotfix | `hotfix/<proyecto>/<nombre>` | Días | tag de prod |

`develop`, `qa`, `staging` como ramas permanentes **no existen**. Los ambientes son pipelines, no ramas.

---

## Flujo completo de un cambio

```
commit en main
     │
     ▼
pipeline detecta paths cambiados
     │  solo construye y testea lo afectado
     │  si shared cambia → testea todos sus consumers
     ▼
deploy automático → DEV  (tag: proyecto-a@1.4.2-dev.1)
     │
e2e pasan, QA revisa
     │  [gate manual en Bitbucket]
     ▼
tag: proyecto-a@1.4.2-rc.1  (mismo SHA que DEV)
     │
     │  [gate de aprobación humana]
     ▼
tag: proyecto-a@1.4.2  → deploy PROD
     (misma imagen de DEV, solo re-etiquetada)
     proyecto-b sigue en su versión, no se mueve
```

---

## Sparse checkout — los 5 sabores

### Sabor 1: un solo proyecto
```bash
git clone --filter=blob:none --sparse git@bitbucket.org:deacero/monorepo.git
cd monorepo
./scripts/release.sh init proyecto-a
# baja projects/proyecto-a/ + shared que consume + scripts/
# el 80% del repo ni existe en disco

./scripts/release.sh sync        # rebase diario
./scripts/release.sh status      # ver qué tienes activo
./scripts/release.sh add proyecto-b   # agregar temporalmente
./scripts/release.sh drop proyecto-b  # quitar cuando no lo necesitas
```

### Sabor 2: solo shared (eres de plataforma)
```bash
./scripts/release.sh init-multi shared/libs/auth
# baja auth + todos sus consumers automáticamente
# verificas que tu cambio no rompe ningún consumer antes del PR
```

### Sabor 3: proyecto + toco shared
```bash
./scripts/release.sh init proyecto-a
./scripts/release.sh add shared/libs/http-client   # agregar shared adicional
./scripts/release.sh drop shared/libs/http-client  # quitar cuando terminas
```

### Sabor 4: refactor multi-proyecto
```bash
./scripts/release.sh init-multi proyecto-a proyecto-b proyecto-c
git checkout -b integration/refactor-auth
# sync diario obligatorio — la rama puede vivir varios días
./scripts/release.sh sync
# al terminar:
git checkout main && git merge --no-ff integration/refactor-auth
git push origin --delete integration/refactor-auth
```

### Sabor 5: checkout completo
```bash
./scripts/release.sh expand
# para: onboarding, debugging cross-proyecto, CI runner
# para desarrollo diario: usa sparse
./scripts/release.sh init <proyecto>  # volver a sparse
```

**Regla de oro:** lo que no ves en disco sigue existiendo en git. `git log`, `git tag`, `git diff` ven TODO el historial.

---

## Hotfix — cuando hay falla en prod

```bash
# 1. crear branch desde el tag de prod, NO desde main
./scripts/release.sh hotfix proyecto-a 1.4.2 fix-critico
# sparse configurado automáticamente para proyecto-a

# 2. hacer SOLO el fix
git commit -m "fix(proyecto-a): descripción"
git push origin hotfix/proyecto-a/fix-critico
# pipeline crea: proyecto-a@1.4.2-hotfix.1 y deploya a prod

# 3. OBLIGATORIO: cherry-pick a main
./scripts/release.sh cherry proyecto-a 1.4.2
# procesa TODOS los hotfixes pendientes en orden ascendente
# para si hay conflicto — resolver y re-correr

# 4. verificar
./scripts/release.sh check proyecto-a
```

---

## Variables de entorno

| Variable | Default | Cuándo setear |
|----------|---------|---------------|
| `GITOPS_REMOTE` | auto-detect (`deacero` > `origin` > `upstream`) | Repo con remote no estándar |
| `GITOPS_TRUNK_BRANCH` | `main` | Repo que aún usa `master` |
| `GITOPS_SCOPE` | `@deacero` | Proyecto con scope diferente |

```bash
# Ejemplo para repo con master y remote "deacero"
GITOPS_REMOTE=deacero GITOPS_TRUNK_BRANCH=master ./scripts/release.sh sync
```

---

## Estado de todos los ambientes

```bash
# tabla de tags por ambiente (DEV/QA/HOTFIX/PROD)
./scripts/release.sh versions proyecto-a
./scripts/release.sh versions          # todos los proyectos

# qué hay exactamente en prod
git tag -l "*@[0-9]*.[0-9]*.[0-9]*" | grep -v "dev\|rc\|hotfix" | sort

# diff entre QA y prod (debe ser vacío si son el mismo SHA)
git diff proyecto-a@1.4.2-rc.1 proyecto-a@1.4.2

# hotfixes pendientes de cherry-pick
./scripts/release.sh check
```

---

## Promote — escape hatch para rollback/re-promote

`promote` es un escape hatch manual. El flujo CI normal crea los tags automáticamente.
Úsalo solo para: re-promote después de un rollback, o cuando el pipeline falla.

```bash
# Re-crear tag de DEV (CI lo haría normalmente)
./scripts/release.sh promote proyecto-a 1.5.0 dev
# → crea proyecto-a@1.5.0-dev.2 (autoincrementa desde .1)

# Promote a QA desde trunk (solo si estás en main)
./scripts/release.sh promote proyecto-a 1.5.0 rc
# → crea proyecto-a@1.5.0-rc.1

# promote NO soporta prod — prod siempre via release.sh tag desde trunk limpio
```

---

## Checklist de adopción (por semana)

### Prerequisito — antes de Semana 1
- [ ] Configurar branch restrictions en Bitbucket (CODEOWNERS sin esto es solo documentación)
- [ ] Decidir: migrar `master` → `main` o setear `GITOPS_TRUNK_BRANCH=master`

### Semana 1-2
- [ ] Crear `CODEOWNERS` en raíz
- [ ] Crear `scripts/release.sh` (usar el de este repo como base)
- [ ] Setear `GITOPS_REMOTE` según el nombre del remote autoritativo
- [ ] NO mover `vendor/` si tiene bind mounts en docker-compose — solo documentar en CODEOWNERS

### Semana 3-4
- [ ] Crear `bitbucket-pipelines.yml` con detección de cambios por path
- [ ] Incluir `git fetch --unshallow` en primer step (CI usa shallow clone)
- [ ] Crear estructura `projects/` y `shared/libs/` si no existe

### Semana 5-6
- [ ] Eliminar ramas `dev`/`qa` globales
- [ ] Adoptar tags por proyecto
- [ ] Configurar tag protection en Bitbucket (patrón `*@*`)
- [ ] Agregar cleanup automático de tags `-dev.*` > 30 días

### Semana 7+
- [ ] Sparse checkout en todos los desarrolladores
- [ ] Versionado semántico de `shared/libs/`
- [ ] Evaluar firma de tags para prod (`git tag -s`, requiere git 2.34+)

---

## Bootstrap en un nuevo proyecto con `gitops-init.sh`

La forma recomendada de adoptar esta estrategia en otro repo es el script de bootstrap:

```bash
# Desde el directorio ai-tooling, apuntar al repo destino
bash scripts/gitops-init.sh \
  --target /ruta/a/mi-otro-repo \
  --stack "python,typescript" \
  --project-map "backend:backend,frontend:."

# Todas las opciones:
#   --target <dir>           directorio del repo destino
#   --stack <stacks>         stacks separados por coma: python, typescript, node, go
#   --project-map <map>      nombre:directorio separados por coma
#   --trunk master           si el repo usa master en vez de main
#   --scope @mi-empresa      si el scope npm/paquetes es diferente a @deacero
#   --skip-precommit         omite `pre-commit install` (útil en CI o setup sin Python)
#   --dry-run                muestra qué haría sin ejecutar nada

# El script hace 5 pasos automáticamente:
# 1. Copia scripts/release.sh al repo destino
# 2. Copia tools/check_adr_gate.py + tools/install_hooks.sh
# 3. Crea CODEOWNERS desde template (editar @equipo-* después)
# 4. Genera .pre-commit-config.yaml con hooks para los stacks indicados
# 5. Ejecuta `pre-commit install` (o avisa si --skip-precommit)
# 6. Crea .gitops-env con GITOPS_STACKS y GITOPS_PROJECT_MAP
```

Si no se pasan `--stack` ni `--project-map`, el script entra en modo **wizard interactivo** y hace las preguntas paso a paso.

---

## Bootstrapping un nuevo repo — guía de preguntas

Usa estas preguntas para determinar los valores de `--stack` y `--project-map` antes de correr el script:

### Pregunta 1: ¿Qué lenguajes usa el repo?
Esto determina `--stack`. Opciones: `python`, `typescript`, `node`, `go`. Se pueden combinar.

| Si el repo tiene... | Stack |
|---------------------|-------|
| `pyproject.toml`, `requirements.txt` o `setup.py` | `python` |
| `package.json` + `tsconfig.json` | `typescript` |
| `package.json` sin TypeScript | `node` |
| `go.mod` | `go` |
| Varios de los anteriores | combinados: `"python,typescript"` |

### Pregunta 2: ¿Los proyectos/servicios se despliegan de forma independiente?
Esto determina `--project-map`.

- **Sí** → cada uno necesita un nombre y un directorio en el mapa
- **No** (monolito puro) → un solo proyecto, directorio `.`

### Pregunta 3: ¿Cuál es el nombre semántico de cada proyecto?
El nombre aparece en los tags GitOps: `backend@1.0.0`, `frontend@1.0.0`.  
Ejemplos: `backend`, `frontend`, `api`, `worker`, `typescript-auth`, `python-auth`

### Pregunta 4: ¿En qué directorio vive cada proyecto?
- A la raíz del repo → `.`
- En un subdirectorio → `backend`, `python/auth`, `services/api`

### Ejemplos para guiar la conversación con el usuario

```
→ "¿Este repo tiene múltiples servicios que se despliegan por separado o es un monolito?"
→ "¿Hay código Python, TypeScript y/o Go?"
→ "Si etiquetaras una versión del backend solo, ¿qué nombre usarías? ej: backend@1.0.0"
→ "¿En qué directorio está el backend? ej: backend/, python/auth/, o en la raíz (.)"
```

### Ejemplos completos

```bash
# Repo con Next.js en raíz + FastAPI en backend/
bash scripts/gitops-init.sh \
  --target /ruta/a/repo \
  --stack "python,typescript" \
  --project-map "backend:backend,frontend:."

# Monorepo con tres auth libs en subdirectorios por lenguaje
bash scripts/gitops-init.sh \
  --target /ruta/a/commons \
  --stack "python,typescript,go" \
  --project-map "python-auth:python/auth,go-auth:go/auth,typescript-auth:typescript/auth"

# Monolito Python puro
bash scripts/gitops-init.sh \
  --target /ruta/a/api \
  --stack "python" \
  --project-map "api:."
```

---

### `GITOPS_PROJECT_MAP` — resolución de paths en `release.sh`

`release.sh` lee `GITOPS_PROJECT_MAP` (seteado en `.gitops-env`) para resolver el directorio de cada proyecto:

```bash
# Con GITOPS_PROJECT_MAP="backend:backend,frontend:."
bash scripts/release.sh tag backend 1.0.0       # apunta a backend/
bash scripts/release.sh tag frontend 1.0.0      # apunta a . (raíz)
bash scripts/release.sh hotfix backend 1.0.0 fix-auth
```

Sin `GITOPS_PROJECT_MAP`, usa el default: `projects/<nombre>` (estructura estándar de monorepo).

Para instalar `release.sh` manualmente sin el bootstrap:

```bash
mkdir -p scripts
cp /ruta/a/ai-tooling/scripts/release.sh scripts/
chmod +x scripts/release.sh
export GITOPS_SCOPE="@mi-empresa"   # si el scope es diferente a @deacero
```

---

## Gotchas conocidos

| Situación | Problema | Solución |
|-----------|----------|----------|
| macOS / Alpine | `grep -oP` no soportado | El script ya usa `grep -o` POSIX |
| CI shallow clone | `merge-base --is-ancestor` falla | El script hace `--unshallow` automático |
| Cherry-pick detection | `merge-base` no detecta cherry-picks | El script usa `git cherry` (patch-id) como fallback |
| Repo sin `origin` | `git push origin` falla | El script detecta remotes; setear `GITOPS_REMOTE` |
| Repo con `master` | `require_trunk` rechaza | Setear `GITOPS_TRUNK_BRANCH=master` |
| Tags anotados | `rev-parse` da SHA del tag object | El script usa `^{commit}` siempre |
| hotfix.10 vs hotfix.2 | Sort lexicográfico desordena | El script extrae el N y hace `sort -n` |
| Multiple hotfixes | Solo se cherry-pickeaba el último | El script itera todos en orden ascendente |
| Tag remoto no visible | `require_tag_not_exists` puede fallar | `cmd_tag` hace `git fetch --tags` antes de verificar |
| Sparse activo + `work` a otro proyecto | dir no visible → `no existe` | Corregido: usa `git ls-tree` para verificar existencia |
| `project-map` con `.` (raíz) | `work` en cone mode incluye solo top-level | Esperado: usar `expand` si necesitas todo el árbol |
| `core.hooksPath` ya seteado | `pre-commit install` rechaza instalar | `git config --unset-all core.hooksPath` y reintentar |

---

## Worktrees — Trabajo Paralelo de Ramas (ADR-0008)

**Problema que resuelve:** trabajar en dos ramas simultáneamente sin `git stash` ni perder contexto.

**No reemplaza sparse checkout** — son ortogonales: sparse controla qué archivos ves, worktree controla qué rama tienes activa en paralelo.

### Cuándo usar worktrees

| Caso | Comando |
|------|---------|
| Hotfix urgente con feature en progreso | `worktree add-branch` desde el tag de prod |
| `integration/*` > 2 días junto a trabajo en trunk | `worktree add-branch` desde trunk |
| Revisar una rama sin alterar tu working tree | `worktree add` |

### Flujo completo: hotfix con worktree

```bash
# Estás trabajando en feature/pagos-v2, llega hotfix urgente
./scripts/release.sh worktree add-branch hotfix/backend/fix-auth ../wt-fix-auth backend@1.4.2

# Trabajas en el hotfix SIN tocar tu working tree actual
cd ../wt-fix-auth
vim backend/auth.py
git commit -m "fix(backend): corrige validación de token"
git push origin hotfix/backend/fix-auth

# Limpias el worktree al terminar
cd ..
./scripts/release.sh worktree rm wt-fix-auth

# Cherry-pick al trunk (flujo normal)
./scripts/release.sh cherry backend 1.4.2
```

### Ritual de limpieza

```bash
./scripts/release.sh status           # muestra worktrees activos + huérfanos
./scripts/release.sh worktree clean   # candidatos a limpiar (ramas ya mergeadas)
./scripts/release.sh worktree prune   # limpia refs de dirs borrados manualmente
```

### Worktrees en workflows multi-agente

El Workflow tool de Claude Code soporta `isolation: 'worktree'` para agentes paralelos que escriben archivos — cada agente opera en su propio worktree temporal y el harness lo limpia al terminar:

```javascript
await parallel(subtasks.map(t => () =>
  agent(t.prompt, { isolation: 'worktree' })
))
```

El hook `.claude/hooks/worktree-isolation-gate.sh` (nuevo, ADR-0008) advierte si detecta `parallel(agent())` sin `isolation: 'worktree'`.

### Reglas de disciplina

1. **Nunca `rm -rf <worktree-dir>`** — usar `./scripts/release.sh worktree rm <path>` o `git worktree remove <path>`
   El hook `block-dangerous.sh` bloquea `rm -rf` sobre worktrees activos registrados.
2. Revisar `release.sh status` diariamente — incluye worktrees activos y huérfanos.
3. Máx. 3-4 worktrees simultáneos — más indica trabajo estancado.
4. Si se borró accidentalmente con `rm -rf`: correr `./scripts/release.sh worktree prune` para limpiar refs huérfanas.
