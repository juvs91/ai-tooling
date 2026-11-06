# ADR-0083: `release.sh` pregunta la carpeta de un proyecto a un script del repo (`GITOPS_PROJECT_RESOLVER`)

- **Estado:** Aceptado
- **Fecha:** 2026-11-06
- **Autor:** jeguzman

---

## Contexto

`release.sh` encuentra la carpeta de un proyecto en `GITOPS_PROJECT_MAP` (`nombre:carpeta`) o, si no está ahí, en `GITOPS_PROJECTS_DIR/<nombre>`. Desde ADR-0082 lee ambas de `.gitops-env`, como datos: nunca ejecuta ese archivo.

Un repo consumidor (ai_by_design) ya escribe la carpeta de cada proyecto en otra tabla: la que usan su detección de cambios, su pipeline y sus checks. Además, desde el 2026-11-06 tiene proyectos anidados (`intelligence-quoting/price-spread`, `intelligence-quoting/ballpark/api`). Con solo el mapa, la carpeta de cada proyecto anidado quedaba escrita dos veces: en la tabla y en `.gitops-env`. Las dos copias se desfasan cuando se mueve una carpeta. `.gitops-env` no puede calcular el mapa con `$(…)`, porque se lee como datos.

## Decisión

**`GITOPS_PROJECT_RESOLVER`**: la ruta, relativa a la raíz, de un script del repo. Recibe un nombre (de proyecto o prefijo de tag) e imprime su carpeta, o sale con un código distinto de 0 si no lo conoce. `release.sh` lo consulta en `_project_path`:

1. ruta literal (libs compartidas, `scripts`);
2. `GITOPS_PROJECT_MAP`;
3. **`GITOPS_PROJECT_RESOLVER`**;
4. `GITOPS_PROJECTS_DIR/<nombre>`.

- **Es una ruta, no un comando.** Debe cumplir `^[A-Za-z0-9._/-]+$`, sin `..`, no absoluta y existente en el repo. Si no cumple, `release.sh` se detiene sin ejecutar nada. Se corre como `sh <raíz>/<ruta> <nombre>`.
- Si el resolvedor falla o no imprime nada, se sigue con `GITOPS_PROJECTS_DIR`. Sin la variable, nada cambia.

## Consecuencias

### Positivas

- Un repo con su propia tabla de proyectos no repite carpetas en `.gitops-env`. El mapa queda para nombres que no son proyectos (por ejemplo `claude:.claude`).
- Proyectos anidados sin configuración extra.

### Negativas / Costos

- `release.sh` ejecuta un script del repo. Es la misma confianza que ya se le da al propio `release.sh` y a `deps.py`, pero hay que mantener ese script.
- El resolvedor debe ser rápido y no escribir a stdout más que la carpeta.

## Implementación

- `scripts/release.sh`: `_project_path` y la ayuda.
- Tests: `tests/gitops/release-tests.sh`, R18 (carpeta anidada por nombre y por alias; un nombre desconocido cae en `GITOPS_PROJECTS_DIR`) y R18b (un valor con `;` se rechaza y no se ejecuta). 34 de 34.
- Consumidor de referencia: ai_by_design, `scripts/project_dir.sh`, que lee su `projects.tsv`.
