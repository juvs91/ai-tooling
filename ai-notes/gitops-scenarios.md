# GitOps Monorepo — Escenarios y Flujos
# Ref: ADR-0007-gitops-monorepo-trunk-based.md
# Ref: templates/gitops/bitbucket-pipelines.yml.template
# Ref: scripts/release.sh
#
# Notación genérica:
#   <project>  → nombre del repositorio/monorepo
#   <pkg>      → nombre del paquete (backend, frontend, common, etc.)
#   <ver>      → versión semántica (1.2.3)
#   <env>      → ambiente (dev, qa, prod)

══════════════════════════════════════════════════════════════════════
1. ESTRUCTURA DEL MONOREPO
══════════════════════════════════════════════════════════════════════

  <project>/
  ├─ backend/          paquete autónomo  → tag backend@<ver>
  ├─ frontend/         paquete autónomo  → tag frontend@<ver>
  ├─ common/           utilería shared   → tag common@<ver>
  ├─ scripts/
  │    release.sh      CLI de GitOps (tag, hotfix, cherry, promote)
  │    deploy.sh       deploy por paquete y ambiente
  ├─ .gitops-env       GITOPS_TRUNK_BRANCH, GITOPS_SCOPE, GITOPS_PROJECT_MAP
  └─ bitbucket-pipelines.yml  (o .github/workflows/)

  Regla de oro:
    un tag = un paquete = un deploy
    nadie despliega sin tag


══════════════════════════════════════════════════════════════════════
2. SPARSE CHECKOUT — 4 VARIANTES
══════════════════════════════════════════════════════════════════════

  CASO A — solo un paquete (ej: backend)
  ──────────────────────────────────────
  git clone --filter=blob:none --no-checkout \
      git@github.com:org/<project>.git
  git sparse-checkout init --cone
  git sparse-checkout set backend/ common/
  git checkout main

    <project>/
    ├─ backend/    ✓ en disco
    ├─ common/     ✓ en disco  (dependencia compartida)
    └─ frontend/   ✗ no descargado

  # también disponible via release.sh:
  ./scripts/release.sh work backend


  CASO B — solo frontend
  ──────────────────────
  git sparse-checkout set frontend/ common/
  git checkout main

    <project>/
    ├─ backend/    ✗ no descargado
    ├─ common/     ✓ en disco
    └─ frontend/   ✓ en disco

  ./scripts/release.sh work frontend


  CASO C — backend + frontend (sin full clone)
  ─────────────────────────────────────────────
  git sparse-checkout set backend/ frontend/ common/
  git checkout main

    <project>/
    ├─ backend/    ✓ en disco
    ├─ common/     ✓ en disco
    └─ frontend/   ✓ en disco

  ./scripts/release.sh work backend frontend


  CASO D — full clone (referencia, sin sparse)
  ─────────────────────────────────────────────
  git clone git@github.com:org/<project>.git
  # todo en disco, sin filtros

  ./scripts/release.sh expand    # expande sparse existente a full


  AHORRO de sparse vs full:
  ┌─────────────┬────────────┬─────────────┐
  │             │ full clone │ sparse (1)  │
  ├─────────────┼────────────┼─────────────┤
  │ archivos    │    100%    │   ~35-40%   │
  │ tiempo      │   base     │  ~60% menos │
  │ disco       │   base     │  ~60% menos │
  └─────────────┴────────────┴─────────────┘


══════════════════════════════════════════════════════════════════════
3. CHANGE DETECTION EN PIPELINE
══════════════════════════════════════════════════════════════════════

  push a main
       │
       ▼
  git fetch --unshallow   ← CI usa shallow clone por default
       │
       ▼
  detect-changes step
  ┌──────────────────────────────────────────────────────────┐
  │ CHANGED=$(git diff --name-only HEAD~1 HEAD)              │
  │                                                          │
  │ grep "^backend/"  → BUILD_BACKEND=true  | false         │
  │ grep "^frontend/" → BUILD_FRONTEND=true | false         │
  │ grep "^common/"   → BUILD_COMMON=true   | false         │
  └──────────────────────────────────────────────────────────┘
       │
       ▼  artifacts: variables.txt
       │
  ┌────┴──────────────────────────────────────┐
  │         parallel conditional jobs         │
  │                                           │
  │  BUILD_BACKEND=true?                      │
  │  ├─ YES → build + test backend/           │
  │  │        + deploy backend → DEV          │
  │  └─ NO  → skip (0 tiempo, 0 costo)        │
  │                                           │
  │  BUILD_FRONTEND=true?                     │
  │  ├─ YES → build + test frontend/          │
  │  │        + deploy frontend → DEV         │
  │  └─ NO  → skip                            │
  │                                           │
  │  BUILD_COMMON=true?                       │
  │  ├─ YES → build + test common/            │
  │  │        (no deploy directo)             │
  │  └─ NO  → skip                            │
  └───────────────────────────────────────────┘

  Regla de changesets (bitbucket-pipelines.yml):
    condition:
      changesets:
        includePaths:
          - "backend/**"
          - "common/**"   ← common siempre incluido como trigger
                              porque es dependencia de todos


══════════════════════════════════════════════════════════════════════
4. ESCENARIO A — CAMBIO EN UN SOLO PAQUETE
══════════════════════════════════════════════════════════════════════

  feature/fix-login → PR → merge a main
       │
       ▼  pipeline detecta: BUILD_BACKEND=true, resto=false
       │
       ├─ build + test backend/      ✓
       ├─ build + test frontend/     skip
       └─ deploy backend → DEV       ✓
       │
       ▼  DEV validado → release
       │
  ./scripts/release.sh tag backend 1.2.1
       │
       ▼  crea y pushea: backend@1.2.1
       │
  pipeline tag: backend@1.2.1
  ┌─────────────────────────────────────┐
  │ PROJECT = backend                   │
  │ VERSION = 1.2.1                     │
  │                                     │
  │ job: deploy backend → PROD          │
  │      trigger: manual (gate humano)  │
  │      ./scripts/deploy.sh backend prod 1.2.1
  └─────────────────────────────────────┘

  frontend: no tocado, no desplegado.


══════════════════════════════════════════════════════════════════════
5. ESCENARIO B — CAMBIO SOLO EN COMMON (utilería compartida)
══════════════════════════════════════════════════════════════════════

  feature/update-auth-types → PR → merge a main
       │
       ▼  pipeline detecta: BUILD_COMMON=true
          common/* está en includePaths de backend Y frontend
          → BUILD_BACKEND=true, BUILD_FRONTEND=true también
       │
       ├─ build + test common/     ✓
       ├─ build + test backend/    ✓  (puede romper con common nuevo)
       ├─ build + test frontend/   ✓  (ídem)
       └─ deploy backend → DEV     ✓
          deploy frontend → DEV    ✓
       │
       ▼  DEV validado → 3 tags OBLIGATORIOS, en orden:
       │
  ./scripts/release.sh tag common   0.3.0
  ./scripts/release.sh tag backend  1.2.2   ← bump por common nuevo
  ./scripts/release.sh tag frontend 2.1.1   ← bump por common nuevo
       │
       ▼  3 pipelines en paralelo:
       │
  common@0.3.0         backend@1.2.2        frontend@2.1.1
  ─────────────────    ─────────────────    ─────────────────
  validate + test      deploy → PROD        deploy → PROD
  gh release           trigger: manual      trigger: manual

  REGLA: si common cambia → bump obligatorio de TODOS los consumers.
         No se puede dejar un consumer apuntando a common viejo en prod.


══════════════════════════════════════════════════════════════════════
6. ESCENARIO C — CAMBIO EN UN PAQUETE + COMMON SIMULTÁNEO
══════════════════════════════════════════════════════════════════════

  feature/refactor-pagos → PR → merge a main
  (modifica: backend/ + common/)
       │
       ▼  pipeline detecta: BUILD_COMMON=true, BUILD_BACKEND=true
          frontend no cambió código, pero common sí → BUILD_FRONTEND=true
       │
       ├─ build + test common/     ✓
       ├─ build + test backend/    ✓  (doble motivo: propio + common)
       ├─ build + test frontend/   ✓  (solo por common)
       └─ deploy todos → DEV       ✓
       │
       ▼  DEV validado → tags en orden:
       │
  1.  ./scripts/release.sh tag common   0.4.0
  2.  ./scripts/release.sh tag backend  1.3.0   ← cambio propio + common
  3.  ./scripts/release.sh tag frontend 2.1.2   ← solo common nuevo
       │
       ▼  3 pipelines, mismo patrón que Escenario B

  Si release de common@0.4.0 falla → NO taggear backend ni frontend.
  Si release de backend@1.3.0 falla → NO taggear frontend.
  (dependencia de orden: base primero, consumers después)


══════════════════════════════════════════════════════════════════════
7. ESCENARIO D — CAMBIO EN TODOS LOS PAQUETES
══════════════════════════════════════════════════════════════════════

  feature/mega-refactor → PR → merge a main
  (modifica: backend/ + frontend/ + common/)
       │
       ▼  pipeline detecta: BUILD_COMMON=true, BUILD_BACKEND=true,
                             BUILD_FRONTEND=true
       │
       ├─ build + test common/     ✓
       ├─ build + test backend/    ✓  (doble motivo)
       ├─ build + test frontend/   ✓  (doble motivo)
       └─ deploy todos → DEV       ✓
       │
       ▼  DEV validado → tags en orden:
       │
  1.  ./scripts/release.sh tag common   0.5.0
  2.  ./scripts/release.sh tag backend  1.4.0   ← cambio propio + common
  3.  ./scripts/release.sh tag frontend 3.0.0   ← cambio propio + common
       │
       ▼
  3 pipelines en paralelo (pasos 2 y 3 pueden correr juntos
  si common@0.5.0 ya completó su pipeline de validación)

  common@0.5.0     backend@1.4.0        frontend@3.0.0
  ─────────────    ──────────────────   ──────────────────
  validate         deploy → PROD        deploy → PROD
  release          trigger: manual      trigger: manual

  Orden de aprobación manual recomendado:
    1. common  → sin deploy propio, solo validar
    2. backend → manual gate en pipeline
    3. frontend → manual gate en pipeline


══════════════════════════════════════════════════════════════════════
8. NEW FEATURE — FLUJO COMPLETO CON TAGS
══════════════════════════════════════════════════════════════════════

  git checkout -b feature/<nombre>
  # ... desarrollar, commitear ...
  git push origin feature/<nombre>
       │
       ▼  pipeline feature/* :
          build + test solamente, sin deploy
       │
       ▼  PR → revisión → merge a main
       │
       ▼  pipeline main:
          detect-changes → build + test → deploy → DEV
          auto-tag DEV: <pkg>@<build>-dev.1
       │
       ▼  validación en DEV
       │
  # Promoción a QA (RC):
  ./scripts/release.sh promote <pkg> <ver> rc
       │
       ▼  crea tag: <pkg>@<ver>-rc.1
          pipeline *@*-rc.* → deploy → QA (trigger: manual)
       │
       ▼  validación en QA
       │
  # Release a PROD:
  ./scripts/release.sh tag <pkg> <ver>
       │
       ▼  crea tag: <pkg>@<ver>   (sin sufijo = producción)
          pipeline *@N.N.N → deploy → PROD (trigger: manual)

  RESUMEN DE TAGS POR AMBIENTE:
  ┌──────────────────────┬────────────┬──────────────┐
  │ Tag                  │ Ambiente   │ Trigger      │
  ├──────────────────────┼────────────┼──────────────┤
  │ <pkg>@1.5.0-dev.3    │ DEV        │ automático   │
  │ <pkg>@1.5.0-rc.1     │ QA         │ manual       │
  │ <pkg>@1.5.0          │ PROD       │ manual       │
  └──────────────────────┴────────────┴──────────────┘


══════════════════════════════════════════════════════════════════════
9. HOTFIX — FLUJO COMPLETO CON TAGS
══════════════════════════════════════════════════════════════════════

  CONTEXTO: bug crítico en prod en backend@1.3.0
  NO se puede esperar el ciclo normal de feature → QA → prod.

  PASO 1 — crear rama hotfix desde el tag de prod:
  ──────────────────────────────────────────────────
  ./scripts/release.sh hotfix backend 1.3.0
       │
       ▼  internamente:
          git checkout -b hotfix/backend/1.3.1 backend@1.3.0
          (branch desde el tag exacto, no desde main)
       │
       ▼  fix en hotfix/backend/1.3.1
          pipeline hotfix/* : build + test solamente
       │
  PASO 2 — taggear el hotfix:
  ─────────────────────────────
  ./scripts/release.sh tag backend 1.3.1
       │
       ▼  crea tag: backend@1.3.1-hotfix.1
          pipeline *@*-hotfix.* → deploy → PROD (trigger: manual)
       │
  PASO 3 — cherry-pick a main (OBLIGATORIO):
  ────────────────────────────────────────────
  ./scripts/release.sh cherry backend 1.3.1
       │
       ▼  cherry-pick del fix a trunk
          garantiza que el hotfix no se pierda en el próximo release

  BRANCHING DIAGRAM:
  ──────────────────
  main  ────●────────────────────────────●────▶
            │                            ↑
            │ (fork en tag prod)    cherry-pick
            │
            └─▶ hotfix/backend/1.3.1
                      │
                      ●  fix aplicado
                      │
                      ▼  tag: backend@1.3.1-hotfix.1
                      ▼  deploy → PROD (manual gate)

  TAGS DE HOTFIX:
  ┌─────────────────────────────┬────────────┬──────────────┐
  │ Tag                         │ Ambiente   │ Trigger      │
  ├─────────────────────────────┼────────────┼──────────────┤
  │ backend@1.3.1-hotfix.1      │ PROD       │ manual       │
  └─────────────────────────────┴────────────┴──────────────┘

  RECORDATORIO DEL PIPELINE al deploy hotfix:
    "ejecutar: ./scripts/release.sh cherry backend 1.3.1"

  Verificar hotfixes pendientes de cherry-pick:
    ./scripts/release.sh check backend


══════════════════════════════════════════════════════════════════════
10. GUARDRAILS DEL FLUJO GITOPS
══════════════════════════════════════════════════════════════════════

  Regla                    Mecanismo                  Bypass
  ────────────────────────────────────────────────────────────────────
  No commit en main        no-commit-to-branch hook   git commit --no-verify
                                                       (desde terminal)
  ADR antes de arq.        adr-gate.sh (PreToolUse)   [skip-adr] en commit msg
  No --no-verify en CC     block-no-verify setting    terminal directa
  No secrets en archivos   protect-secrets.sh          —
  Deploy solo con tag      pipeline solo en tags *@*  tag manual obligatorio
  PROD requiere humano     trigger: manual en pipeline  —
  Common bump obligatorio  convención de equipo        documentar excepción
  Cherry-pick obligatorio  release.sh check + reminder  —
  Tag de PROD sin sufijo   validación en pipeline      rechaza si lleva -dev/-rc
  Push a main sin PR       branch protection (GitHub)  git push <rama>:main
                           o CODEOWNERS (Bitbucket)
