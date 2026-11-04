# Lector único de dependencias + documento para compartir

Estado: ejecutado (2026-11-04) — rama feat/deps-lector-unico

## Contexto

En el GitOps monorepo, que un servicio use una librería compartida se declara a mano en varios lugares:
- `trigger.paths.include` y `dependsOn` (×2) en `<svc>-ci.yml`;
- `dependsOn` en `<svc>-release.yml`;
- el `COPY` del Dockerfile;
- el filtro de la policy de build validation;
- `shared_deps_of()` de `release.sh`, que lo adivina por convención de nombres.

Además:
- no existe "where-used" (qué servicios usan una librería);
- `release.sh` tiene `shared/libs` fijo (líneas 106, 197, 206, 303, 328), mientras Azure usa `_shared/libs`;
- no hay soporte para Go.

Decisión del usuario: las librerías se consumen **desde el código del monorepo** (sin versiones publicadas) y la fuente única es **el manifiesto nativo de cada servicio**, en el lenguaje que sea. También pidió un `.md` en `docs/` para compartir con los equipos todo lo hablado.

Resultado esperado: declarar una dependencia en un solo archivo, el manifiesto. El sparse checkout, el CI, el hash de imagen y el where-used salen de ahí.

## Paso 0: registrar el plan

Copiar este plan a `ai-notes/plans/2026-11-04-lector-unico-dependencias.md` (CLAUDE.md lo pide antes de ExitPlanMode, pero en plan mode solo se puede escribir el plan file).

## Paso 1: ADR-0080 "Lector único de dependencias desde el manifiesto"

`docs/adr/ADR-0080-lector-unico-dependencias.md`, con el formato de ADR-0049 (Estado/Fecha/Autor, Contexto, Decisión, Consecuencias, Implementación) y su entrada en `docs/adr/index.md` (Domain `GitOps`). Se hace primero porque el paso 6 toca `.agents/skills/` (ADR gate).

## Paso 2: `scripts/deps.py` (nuevo, stdlib, `# distributable: true`)

Se hace en Python porque hay que leer TOML y JSON, y en bash eso sería frágil:
- usa `tomllib`, así que requiere Python ≥ 3.11; si la versión es menor, falla con un mensaje claro;
- sigue el precedente de `scripts/ci_pyproject.py`, que ya es distributable;
- `install-hooks.sh` lo copia solo, gracias a la marca.

**Lee de git, no del disco.** Por default usa `git show <ref>:<path>` con `--ref HEAD`. Así funciona aunque el servicio aún no esté en el sparse set (hoy `shared_deps_of` lee del disco y no ve nada si la carpeta no está materializada), y el release puede calcular las dependencias **en el tag** (`--ref <tag>`).

**Declaración explícita por ruta**, no por convención de nombres:

| Manifiesto | Cómo se declara la librería |
|---|---|
| `pyproject.toml` | `[tool.uv.sources] pkg = { path = "../../_shared/libs/auth" }` o `{ workspace = true }`; con `workspace = true` se resuelve buscando el `[project].name` de los miembros del workspace de la raíz |
| `package.json` | `"pkg": "file:../../_shared/libs/x"` o `"workspace:*"`; con `workspace:*` se resuelve por el `name` de los `package.json` de las librerías |
| `go.mod` | `replace mod => ../../_shared/libs/x` |
| `deps.txt` | Una ruta por línea, solo para servicios sin manifiesto (Terraform, solo Dockerfile) |

Reglas comunes:
- Solo se emiten rutas dentro de `GITOPS_SHARED_DIR` (default `shared/libs`; en Azure, `_shared/libs`).
- Es **transitivo**: si una librería usa otra, también se incluye, porque el hash de imagen lo necesita.

Comandos:
- `deps.py paths <dir> [--ref R]`: imprime el servicio + sus librerías (transitivas), ordenadas y una por línea. Es lo que necesitan `context-hash`, el sparse checkout y `check-paths`.
- `deps.py libs <dir> [--ref R]`: solo las librerías.
- `deps.py consumers <lib> [--ref R]`: el where-used. Recorre los manifiestos con `git ls-tree`/`ls-files` y devuelve los servicios que dependen de la librería, directa o transitivamente.
- `deps.py check-trigger <pipeline.yml> <dir>`: falla si `trigger.paths.include` no coincide con `paths`, e imprime el bloque correcto para pegar. Reutiliza la lógica de `ci-image.sh check-paths` (`:157-182`).
- `deps.py policy-filter <dir>`: imprime el filtro de ruta para la policy de build validation. No llama a `az`.

## Paso 3: los pipelines de Azure calculan las dependencias en tiempo de ejecución

- Quitar el parámetro `dependsOn` de `templates/jobs/python-ci.yml`, `templates/jobs/acr-build.yml` y `templates/stages/service-release.yml`, y de los ejemplos en `services/` y `libs/`.
- Los scripts obtienen la lista con `deps.py`:
  - `ci-service.sh` (`:42`, `:63`, `:118`) usa `deps.py paths "$CI_SERVICE_PATH"`, y en el release `--ref "$tag"`;
  - `ci-python.sh check-paths` (`:21-28`) pasa a `deps.py check-trigger`;
  - `CI_DEPENDS_ON` desaparece de `ci-lib.sh`/`ci_words` donde ya no se use.
- `trigger.paths.include` se queda en el YAML porque Azure no acepta parámetros en los triggers. Pero queda como lista **verificada**: `check-trigger` corre en cada PR.
- `ci-image.sh check-paths` se mantiene para compatibilidad, delegando en `deps.py` o marcado como deprecado.

## Paso 4: `release.sh` usa el mismo lector

- `shared_deps_of()` (`:185-209`) pasa a ser una llamada a `python3 "$SCRIPT_DIR/deps.py" libs "$proj_dir"`. Se elimina la búsqueda de texto por `GITOPS_SCOPE`.
- Reemplazar el `shared/*` fijo (`:106`, `:303`, `:328`) por `${GITOPS_SHARED_DIR:-shared/libs}`, con su raíz.
- Documentar `GITOPS_PROJECTS_DIR`, `GITOPS_PROJECT_MAP` y `GITOPS_SHARED_DIR` en el header (`:25-28`) y en el help (`:886-888`).
- Agregar `release.sh consumers <lib>`, que delega en `deps.py consumers`. Así Platform puede hacer `work` con todos los consumidores de una librería.
- Cambiar `git sparse-checkout init --cone` por `set` (`:223`, `:310`, `:722`): `init` está deprecado y `set` ya activa cone.

## Paso 5: simulador y fixtures

- `tools/azure-sim/azsim/fixture.py:23`: el servicio debe declarar la librería: `dependencies = ["metalsa-auth"]` + `[tool.uv.sources] metalsa-auth = { path = "../../_shared/libs/auth" }`. Hoy tiene `dependencies = []`, así que un lector por manifiesto no encontraría nada.
- `scenarios.py:243-252` (`s_paths_drift`) debe seguir fallando cuando falta la librería en `trigger.paths`.

## Paso 6: documentación del repo

- `templates/gitops/azure-pipelines/README.md` (`:70-71`): reemplazar "ajusta `dependsOn` y repítelo en `trigger.paths`" por "declara la librería en el manifiesto; `deps.py check-trigger` te da el bloque de `trigger.paths`".
- `.agents/skills/infrastructure/gitops-monorepo/SKILL.md`: la sección de sparse checkout y la de shared pasan a la detección por manifiesto, con `consumers`. Requiere el ADR-0080 en staging.

## Paso 7: documento para compartir

`docs/knowledge/gitops-monorepo-a-escala.md`, en Markdown que se lea bien en Azure Repos. Los diagramas van en ASCII, sin depender de mermaid.

Contenido (lo de la página publicada, más lo nuevo):
1. Cómo lo resolvió Google: Piper, CitC, Borg (+ Blaze/TAP/OWNERS/Rosie), con datos de 2015 y fuentes.
2. Nuestro modelo: tag por proyecto, trunk-based, ambientes = pipelines; diagrama ASCII del flujo con varios equipos sobre Azure DevOps.
3. Equivalencias Google ↔ nosotros.
4. Analogía de la planta: bóveda de planos, where-used, revisión liberada.
5. Bajar solo lo tuyo: clone (normal/shallow/partial), sparse checkout en modo cone (reglas, árbol de ejemplo, `--sparse-index`), LFS (ejemplo de `.gitattributes`, cuándo vale la pena).
6. Beneficios y contras, **cada contra con su mitigación**: where-used y lista única con `deps.py`; libs desde el monorepo en vez de versiones publicadas, explicando por qué.
7. Cómo se declara una dependencia (tabla por lenguaje) y cómo se empieza.
8. Fuentes.

Agregar la liga en `docs/adr/ADR-0080` y en `ai-notes/analysis/gitops-monorepo-guia-2026-08-04.md` (sección de referencias).

## Fuera de alcance (seguimiento)

- **Portar `deps.py` y el nuevo `shared_deps_of` a `commons`.** `release.sh` es contrato compartido (ADR-0034, ADR-0048). Se deja anotado en el ADR.
- **Opción B** (un solo pipeline con matriz dinámica de servicios afectados).
- **Crear o actualizar las policies con `az`.**

## Archivos

- Nuevos: `scripts/deps.py`, `tools/tests/test_deps.py`, `docs/adr/ADR-0080-lector-unico-dependencias.md`, `docs/knowledge/gitops-monorepo-a-escala.md`, `ai-notes/plans/2026-11-04-lector-unico-dependencias.md`.
- Modificados: `scripts/release.sh`, `scripts/ci-service.sh`, `scripts/ci-python.sh`, `scripts/ci-image.sh`, `templates/gitops/azure-pipelines/pipelines/**` (templates + ejemplos), `templates/gitops/azure-pipelines/README.md`, `tools/azure-sim/azsim/fixture.py`, `tools/tests/test_azure_templates.py`, `tools/tests/test_ci_service.py`, `.agents/skills/infrastructure/gitops-monorepo/SKILL.md`, `docs/adr/index.md`, `ai-notes/AI_LEARNING.md`.

## Verificación

1. `tools/tests/test_deps.py` (unittest, repos git temporales):
   - Python con `path` y `workspace`, Node con `file:` y `workspace:*`, Go con `replace`, `deps.txt`;
   - dependencias transitivas;
   - `consumers`;
   - `--ref` contra un tag viejo;
   - un servicio fuera del sparse set;
   - `GITOPS_SHARED_DIR=_shared/libs`;
   - Python < 3.11 da un error claro.
2. `python3 -m unittest discover -s tools/tests -v`: todos los tests de CI y templates en verde.
3. `bash tests/harness/run-tests.sh`: sin regresiones.
4. `tools/azure-sim/sim up && tools/azure-sim/sim run all`: los 9 escenarios, incluido `s_paths_drift`. Corre en Docker, lo que evita el problema de `mapfile` en el bash 3.2 de macOS.
5. Manual en un clon temporal con sparse checkout:
   - `release.sh work ventas-api` baja `_shared/libs/auth`;
   - `release.sh consumers _shared/libs/auth` lista `ventas/api`;
   - `bash -n scripts/release.sh` y `shellcheck`.
6. `./scripts/task-verify.sh` antes de reportar. Actualizar `AI_LEARNING.md` y la línea `Estado:` del plan.
