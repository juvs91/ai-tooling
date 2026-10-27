# Templates GitOps — Azure Pipelines

Modelo de ADR-0007 (trunk-based, tag por proyecto, CI por paths) sobre **Azure DevOps**:
imágenes a **Azure Container Registry** y librerías Python a **Azure Artifacts**.
Diseño y razones en [ADR-0060](../../../docs/adr/ADR-0060-azure-pipelines-imagen-por-contenido.md).
Los templates de Bitbucket (`../bitbucket-pipelines*.yml.template`) siguen igual.

## Contenido

```
pipelines/
├── templates/
│   ├── steps/checkout.yml               fetchDepth 0 + tags (los defaults de Azure son shallow y sin tags)
│   ├── jobs/python-ci.yml               uv + ruff + pytest + check-paths
│   ├── jobs/acr-build.yml               imagen por contenido, tag -dev / -hotfix automático
│   ├── stages/service-release.yml       tag git → imagen → Environment (dev | qa | prod)
│   └── stages/python-lib-release.yml    tag git → Azure Artifacts (@Local | Prerelease | Release)
├── services/example-ventas-api-{ci,release}.yml
└── libs/example-auth-{ci,release}.yml
```

Los `.yml` **solo cablean** (ADR-0061): cada step es `bash scripts/ci-<x>.sh <comando>` y
la lógica vive en scripts (en `scripts/` del repo destino, `# distributable: true`):

| Script | Qué decide / hace | Tests |
|---|---|---|
| `ci-service.sh` | Steps del servicio: hash, build de validación, publish, tags `-dev`/`-hotfix`, plan y publish del release | `tools/tests/test_ci_service.py` |
| `ci-python.sh` | CI Python: `check-paths`, uv, ruff, pytest | `tools/tests/test_ci_pylib.py` |
| `ci-pylib.sh` + `ci_pyproject.py` | Release de libs: validar tag, build, upload (409 = re-run), promoción a view | `tools/tests/test_ci_pylib.py` |
| `ci-image.sh` | Hash de contenido, parseo de tags, origen de prod (`prod-source` + `same-content`), versión del manifest, PEP 440, siguiente hotfix, `check-paths` | `tools/tests/test_ci_image.py` |
| `ci-acr.sh` | `ensure-ctx` (build solo si falta), `promote` (re-tag sin rebuild, idempotente, inmutable), locks de tag y manifest | `tools/tests/test_ci_acr.py` |
| `ci-lib.sh` | Helpers: outputs `##vso`, `logissue`, `ci_require` | (vía los anteriores) |

Cableado YAML ↔ scripts validado estáticamente en `tools/tests/test_azure_templates.py`
(steps de una línea, `ci_require` cableados, outputs que existen) y **ejecutando** con el
simulador local.

## Simulador local

`tools/azure-sim/` corre estos mismos `.yml` y scripts contra un ACR (registry real con
locks), un feed de Artifacts (pypiserver) y un agente `ubuntu:24.04`, todo en Docker.
Detalle y límites en [`tools/azure-sim/README.md`](../../../tools/azure-sim/README.md).

```bash
tools/azure-sim/sim up          # una vez
tools/azure-sim/sim run all     # 9 escenarios: PR, merge, rc→prod, hotfix, lib, auth-error…
tools/azure-sim/sim down
```

## Flujo

| Evento git | Pipeline | Resultado |
|---|---|---|
| PR a `main` | `<svc>-ci` vía branch policy *Build validation* | tests + `docker build` sin push |
| merge a `main` | `<svc>-ci` | tests → `<repo>:ctx-<hash>` (build solo si no existe, bloqueado) → tag `<svc>@X.Y.Z-dev.<BuildId>` |
| tag `-dev.N` | `<svc>-release` | `ctx` → `X.Y.Z-dev.N` → Environment dev |
| tag `-rc.N` (`release.sh promote … rc`) | `<svc>-release` | `ctx` → `X.Y.Z-rc.N` → Environment qa (approval) |
| tag `X.Y.Z` (`release.sh tag`) | `<svc>-release` | digest del último `-rc.N` si el contenido no cambió → `X.Y.Z` → prod (approval) |
| push a `hotfix/<svc>/*` | `<svc>-ci` | tests → tag `X.Y.Z-hotfix.N` |
| tag `-hotfix.N` | `<svc>-release` | build si falta → prod (approval) → `release.sh cherry` obligatorio |

La imagen se identifica por **contenido** (tree hashes git del servicio + libs), no por
commit: `release.sh tag` taggea el HEAD de trunk, que puede no haber construido nada
para este servicio. Prod recibe el **mismo digest** que aprobó QA.

## Instalación en un repo

1. Copiar `pipelines/` a la raíz y los scripts `scripts/ci-*.sh` + `scripts/ci_pyproject.py`
   (o `scripts/install-hooks.sh <repo>`, que copia los scripts distributables).
2. Por servicio: copiar los dos ejemplos, renombrar a `<iniciativa>-<servicio>-{ci,release}.yml`
   y ajustar `serviceName`, `servicePath`, `dependsOn` (también en `trigger.paths.include`),
   `acrName`, `imageRepository`, `azureSubscription`, `environments`.
3. Dockerfile del servicio con contexto = **raíz del repo** y `COPY` explícito del servicio y
   sus libs (si no, un cambio en la lib no entra a la imagen).

## Configuración en Azure DevOps (una vez)

| Qué | Dónde | Nota |
|---|---|---|
| Service connection Azure RM con **workload identity federation** | Project settings → Service connections | rol **AcrPush** sobre el ACR (alcanza para push y locks) |
| Pipelines `<svc>-ci` y `<svc>-release` | Pipelines → New → Existing YAML | `az pipelines create` |
| Build validation en `main` con filtro de path por servicio | Repos → Branches → `main` → policies | `pr:` en YAML se ignora en Azure Repos |
| Environments `<svc>-dev`, `<svc>-qa`, `<svc>-prod` | Pipelines → Environments | approvals en qa y prod |
| Permisos de "<Proyecto> Build Service" | Repos → Security | Contribute + **Create tag** (tags `-dev` / `-hotfix` automáticos) |
| Feed con views Prerelease / Release | Artifacts | Build Service como **Feed Publisher** |
| Purga de `ctx-*` y `-dev.*` viejos | ACR → Tasks (`acr purge`) | los tags bloqueados se saltan solos |

## Pendiente de validar en un pipeline sandbox

El simulador cubre el flujo y los scripts; esto solo lo valida Azure real (ver límites en ADR-0061):

No documentado en Microsoft Learn (consultado 2026-10-14):

- `trigger.paths` en pushes de tags (por eso el release es un pipeline aparte, sin paths).
- Varios tags en un solo push → ¿un run por tag?
- Patrones de tag con `@` (`'ventas-api@*'`).
- Check *Branch control* con `refs/tags/*` (hay defensa en profundidad en las condiciones).
- `docker buildx imagetools create` contra ACR con el token de `az acr login` (contra un registry OCI
  real ya se validó en el simulador: el re-tag conserva el digest).
- Nombre normalizado del paquete en la URL REST de promoción a views.
