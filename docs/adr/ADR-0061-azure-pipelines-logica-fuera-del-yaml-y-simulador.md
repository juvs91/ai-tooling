# ADR-0061: Azure Pipelines — lógica fuera del YAML y simulador local

- **Estado:** Propuesto
- **Fecha:** 2026-10-15
- **Autor:** juvs91
- **Relacionados:** ADR-0060 (imagen por contenido), ADR-0007 (GitOps monorepo)

---

## Contexto

Los templates de ADR-0060 tenían ~170 líneas de bash y un heredoc de Python incrustados
en los `.yml` (`inlineScript`, `bash: |`). Solo se validaban con `bash -n` tras reemplazar
las expresiones de Azure por valores dummy. Hasta hoy no se había ejecutado nada de
punta a punta: no hay organización de Azure DevOps ni ACR disponibles, y los tests de
`ci-acr.sh` usan un `az`/`docker` simulado.

Problemas concretos:

1. La lógica incrustada no se puede probar de forma unitaria: un error de quoting o una
   variable mal cableada (`$(ctx.ctx)`, `stageDependencies…`) solo aparece en Azure.
2. El cableado YAML↔script (nombres de outputs, variables de entorno, condiciones) no
   tiene cobertura: el test estático valida la sintaxis, no el flujo.
3. Varias decisiones de ADR-0060 dependen de comportamiento que hay que observar en un
   registry real (`imagetools create`, digests, locks), no en un simulador de `az`.

## Decisión

### 1. El YAML solo cablea

Cada step `bash:` / `AzureCLI@2` es **una sola** llamada `bash scripts/ci-<x>.sh <comando>`
(en AzureCLI@2: `scriptLocation: scriptPath` + `arguments`). Las entradas llegan:

- como **variables del job** (parámetros del template → `CI_*`), que Azure expone como env;
- por el `env:` del step para salidas de steps anteriores (`CI_CTX: $(ctx.ctx)`) y secretos
  (`SYSTEM_ACCESSTOKEN: $(System.AccessToken)` — Azure no expone secretos como env solo);
- las predefinidas del agente (`BUILD_SOURCEBRANCH`, `BUILD_BUILDID`, …) directo del entorno.

| Script | Comandos |
|---|---|
| `ci-lib.sh` (se fuentea) | `ci_output` (`##vso[task.setvariable …;isOutput=true]`), `ci_warn`/`ci_die` (`logissue`), `ci_require` (exit 2 si falta una variable) |
| `ci-service.sh` | `validate-tag`, `context`, `validate-build`, `publish-ctx`, `tag-dev`, `tag-hotfix`, `plan-release`, `publish-release`, `deploy-placeholder` |
| `ci-python.sh` | `check-paths`, `install-uv`, `lint`, `test` |
| `ci-pylib.sh` | `validate`, `build`, `upload`, `promote-view` |
| `ci_pyproject.py` | `set-version`, `name` |

`ci-image.sh` y `ci-acr.sh` (ADR-0060) no cambian: los nuevos scripts los orquestan. La
salida siempre son logging commands `##vso[...]`. El agente real y el simulador los leen
igual, así que no hay un "modo local" distinto que pueda divergir.

`test_azure_templates.py` hace cumplir la regla: cada step es una llamada a
`scripts/ci-*.sh` con un comando que existe, cada `ci_require` del comando está cableado
(env del step, variables del job o predefinidas) y cada `$(step.var)` /
`stageDependencies.S.j.outputs['step.var']` apunta a un step con ese `name:`.

### 2. Simulador local (`tools/azure-sim/`)

Corre los **mismos** `.yml` y `scripts/` contra servicios reales en Docker:

| Pieza | Qué emula | Cómo |
|---|---|---|
| `azsim/compile.py` | expansión de templates al encolar | `template:`, parámetros tipados con default, `${{ if/elseif/else/each }}`, stepList |
| `azsim/runtime.py` | agente + orquestador | stages/jobs/steps con `dependsOn`/`condition`, `$[ ]`, macros, outputs, checkout, tasks |
| `azsim/sim.py` | Azure Repos + triggers | origin bare, pipelines registrados leídos del commit, tags empujados que disparan releases |
| `registry:2` + nginx + `azure_mock.py` | ACR | push/pull OCI reales; **locks aplicados en el servidor** (PUT/DELETE de manifests) |
| `pypiserver` + `azure_mock.py` | Azure Artifacts | upload real con 409 en re-upload; API de promoción a views |
| `agent/` (ubuntu:24.04) | `ubuntu-latest` | bash, git, jq, python 3.12 tipo toolcache, docker CLI + buildx, shim de `az acr` |

Criterio de fidelidad: lo que el simulador no sabe emular **falla con error explícito**
(función de expresión desconocida, task no soportada, `extends`, templates de otro repo).
Nunca lo ignora. Se emulan a propósito comportamientos de Azure que suelen causar bugs:
`'false'` es truthy, sin `persistCredentials` el push del agente falla, los jobs
`deployment` no hacen checkout implícito, `PublishTestResults` sin archivos solo avisa y
los secretos no llegan al env sin mapeo explícito.

Nueve escenarios cubren el flujo de ADR-0060: PR, merge, re-run sin rebuild, rc → prod
con el mismo digest, hotfix, inmutabilidad, release de la lib, error de auth y
`trigger.paths` que no coincide con `dependsOn`.

## Consecuencias

**Positivas**
- La lógica de pipeline se prueba como cualquier script (31 tests unitarios con git real).
- El cableado se valida dos veces: de forma estática y ejecutando. El simulador detecta,
  por ejemplo, un output renombrado (`plan.source` → `plan.src`) que el test estático deja
  pasar.
- `docker buildx imagetools create`, los digests y los locks se observan contra un
  registry OCI real.
- Un template nuevo se valida en local antes de tocar Azure.

**Negativas / límites (lo que solo Azure puede validar)**
- El compilador cubre el subconjunto de sintaxis que usan estos templates. La expansión
  real de Azure solo se comprueba con la API de preview (`previewRun: true`).
- No se emulan: autenticación de ACR con workload identity ni el rol AcrPush; approvals y
  *Branch control* de Environments (se auto-aprueban); varios tags en un solo push;
  `paths` en pushes de tags; la normalización real del nombre de paquete en la API de
  views; `batch: true`.
- El simulador hay que mantenerlo: si un template usa sintaxis nueva, el simulador falla
  hasta extenderlo. Es el comportamiento buscado.
- Los repos destino ahora necesitan los scripts nuevos además de `ci-image.sh` y
  `ci-acr.sh` (`install-hooks.sh` ya copia también `scripts/*.py`).
