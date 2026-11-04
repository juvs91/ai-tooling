# ADR-0081: Migración sin romper al lector de dependencias por ruta

- **Estado:** Aceptado
- **Fecha:** 2026-11-04
- **Autor:** jeguzman

---

## Contexto

ADR-0080 hizo del manifiesto de cada servicio la fuente única de sus librerías, declaradas
**por ruta**. Eso dejaba dos problemas:

1. Un servicio que pedía la librería solo por nombre (`metalsa-auth` sin `[tool.uv.sources]`,
   `@company/auth` con versión) dejaba de detectarse. Antes `shared_deps_of()` lo adivinaba por
   prefijo. Para ese servicio, el sparse checkout no bajaba la librería y el hash de imagen no
   la incluía.
2. `deps.py` necesita Python ≥ 3.11 (`tomllib`), y el `python3` de macOS es 3.9.

## Decisión

**Dependencias pedidas por nombre.** Si un manifiesto pide un paquete que existe en el repo
pero no declara su ruta, `deps.py` lo cuenta igual y avisa con el bloque que falta:
- Python: `[project].dependencies`, `optional-dependencies` y `dependency-groups`;
- Node: cualquier versión que no sea `file:`/`link:`/`workspace:`;
- Go: `require` sin `replace`.

El nombre se compara con el que declara el manifiesto de la librería (`[project].name`,
`name`, `module`), no con un prefijo. Si un servicio usa a propósito la versión publicada, se le
cuenta de más (un build o una descarga extra); nunca falta una librería.

- `deps.py audit` lista todas las dependencias tomadas por nombre, con el bloque a agregar.
- En Azure, `ci-python.sh check-paths` convierte los avisos en warnings del pipeline.
- `DEPS_STRICT=1` los vuelve error. Se activa como variable del pipeline cuando `audit` quede
  limpio.

**Python ≥ 3.11 en la máquina del developer.** `release.sh` elige el intérprete:
`python3.14`…`python3.11`, `python3` si ya es ≥ 3.11, `uv run --no-project --python 3.12`
(uv descarga uno) o un error que dice qué instalar. Probado con solo el Python 3.9 de macOS.

## Consecuencias

### Positivas

- Ningún servicio pierde sus librerías al actualizar: la migración se puede hacer gradual,
  guiada por `audit`.
- `release.sh` funciona en un Mac sin Python moderno si hay `uv`.

### Negativas / Costos

- Para resolver por nombre hay que leer todos los manifiestos del repo (blobs pequeños; con
  partial clone se bajan la primera vez).
- Un servicio que use a propósito la versión publicada se cuenta de más hasta que se excluya.
- `DEPS_STRICT=1` es manual: hay que activarlo cuando `audit` quede limpio.

## Implementación

- `scripts/deps.py`: `Repo.find`/`implicit_dep`, `audit`, `DEPS_STRICT`.
- `scripts/release.sh`: `_python`, `_deps`.
- `scripts/ci-python.sh`: avisos como warnings del pipeline.
- Tests: `tools/tests/test_deps.py` (`TestImplicitByName`) y `tools/tests/test_ci_pylib.py`.
