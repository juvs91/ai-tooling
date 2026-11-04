# ADR-0080: Lector único de dependencias desde el manifiesto de cada servicio

- **Estado:** Aceptado
- **Fecha:** 2026-11-04
- **Autor:** jeguzman

---

## Contexto

En el GitOps monorepo (ADR-0007) las librerías compartidas se consumen desde el código del
monorepo, no como versiones publicadas. Que un servicio use una librería se declaraba a mano
en varios lugares:

- `trigger.paths.include` y el parámetro `dependsOn` (dos veces) en `<svc>-ci.yml`;
- `dependsOn` en `<svc>-release.yml`;
- el `COPY` del Dockerfile;
- el filtro de ruta de la policy de build validation en Azure Repos;
- `shared_deps_of()` de `scripts/release.sh`, que adivinaba por convención de nombres
  (`@company/<lib>` en `package.json`, `company-<lib>` en `pyproject.toml`).

Problemas concretos:

1. Las listas podían divergir. `ci-image.sh check-paths` solo comparaba dos de ellas.
2. No existía "where-used" (qué servicios usan una librería). ADR-0049 lo dejó fuera de alcance.
3. `release.sh` tenía `shared/libs` fijo, mientras los templates de Azure (ADR-0060) usan
   `_shared/libs`. Con ese layout, `release.sh work <svc>` no bajaba las librerías.
4. `shared_deps_of()` leía el manifiesto **del disco**. Con sparse checkout, la carpeta del
   servicio puede no estar materializada todavía.
5. Go no estaba soportado.

Se evaluó publicar primero cada librería (Azure Artifacts) y que los servicios fijen versión.
Se descartó como modelo por default: pierde el cambio atómico librería + consumidores y la
"one version rule", y regresa la deriva de versiones del multirepo.

## Decisión

La fuente única de "qué librerías usa un servicio" es **el manifiesto nativo del servicio**,
con la dependencia declarada **por ruta**:

| Manifiesto | Declaración |
|---|---|
| `pyproject.toml` | `[tool.uv.sources] pkg = { path = "../../_shared/libs/x" }` o `{ workspace = true }` |
| `package.json` | `"pkg": "file:../../_shared/libs/x"` (o `link:`) o `"workspace:*"` |
| `go.mod` | `replace mod => ../../_shared/libs/x` |
| `deps.txt` | Una ruta por línea, relativa a la raíz. Solo para carpetas sin manifiesto |

Las dependencias declaradas con `workspace` se resuelven por nombre de paquete contra los
manifiestos del repo. Se sigue la cadena (si una librería usa otra, también cuenta).

Un solo lector, `scripts/deps.py` (stdlib, Python ≥ 3.11, `# distributable: true`), que lee de
**git** (`--ref`, default `HEAD`) y no del disco:

- `paths <dir>...`: carpeta(s) + sus librerías;
- `libs <dir>...`: solo las librerías;
- `consumers <lib>`: where-used;
- `trigger-block <dir>`: el bloque `paths.include` para el YAML;
- `policy-filter <dir>`: el filtro de ruta para la policy de build validation.

Consumidores del lector:

- `release.sh` (`shared_deps_of`, sparse checkout, nuevo comando `consumers`);
- `ci-service.sh` (hash de imagen por contenido, también en el tag del release);
- `ci-python.sh check-paths`.

Los templates de Azure ya no reciben `dependsOn`. `trigger.paths.include` se queda en el YAML
porque Azure no acepta parámetros en los triggers, pero se valida contra el lector en cada
ejecución. `CI_DEPENDS_ON` se sigue aceptando como override para pipelines con templates
anteriores.

`release.sh` también:

- usa `GITOPS_SHARED_DIR` (default `shared/libs`) para reconocer rutas literales de librerías;
- cambia `git sparse-checkout init --cone` (deprecado) por `git sparse-checkout set --cone`;
- documenta `GITOPS_PROJECTS_DIR`, `GITOPS_PROJECT_MAP` y `GITOPS_SHARED_DIR`.

## Consecuencias

### Positivas

- Una dependencia se declara una vez, en el manifiesto que el lenguaje ya necesita.
- Where-used disponible: `release.sh consumers <lib>`.
- El sparse checkout y el CI usan la misma lista, y funcionan con el layout de Azure.
- El release calcula las dependencias como estaban en el tag, no en `HEAD`.

### Negativas / Costos

- **Cambio incompatible para quien dependía de la convención de nombres.** Un servicio que
  usa `company-auth` sin `[tool.uv.sources]` (o `@company/auth` sin `workspace:`/`file:`) deja
  de detectarse. Hay que declarar la fuente local, que de todos modos es lo que uv/pnpm
  necesitan para instalar desde el monorepo.
- `release.sh work`/`hotfix` requieren Python ≥ 3.11 en la máquina del developer.
- `trigger.paths.include` y el filtro de la policy siguen existiendo como copias; el lector
  las valida o las genera, pero no las elimina.
- `commons` comparte el contrato de `release.sh` (ADR-0034, ADR-0048). Hay que portarle
  `deps.py` y el nuevo `shared_deps_of`. Queda como seguimiento.

## Implementación

- `scripts/deps.py` y `tools/tests/test_deps.py`.
- `scripts/release.sh`: `shared_deps_of`, `_project_path`, `cmd_add`, `cmd_drop`, `consumers`,
  `sparse-checkout set --cone`, variables documentadas.
- `scripts/ci-service.sh`, `scripts/ci-python.sh`: lista desde `deps.py` si no hay
  `CI_DEPENDS_ON`.
- `templates/gitops/azure-pipelines/pipelines/**`: sin `dependsOn`; README actualizado.
- `tools/azure-sim/azsim/fixture.py`: el servicio declara la librería en `[tool.uv.sources]`.
- `.agents/skills/infrastructure/gitops-monorepo/SKILL.md`: detección por manifiesto y
  `consumers`.
- Documento para equipos: `docs/knowledge/gitops-monorepo-a-escala.md`.
- Plan: `ai-notes/plans/2026-11-04-lector-unico-dependencias.md`.
