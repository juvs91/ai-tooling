# ADR-0035: Cerrar las fuentes de verdad restantes del ADR gate en el bootstrap

- **Estado:** Aceptado
- **Fecha:** 2026-08-02
- **Autor:** jeguzman

---

## Contexto

Una sesión de trabajo en `commons` encontró que su copia de `tools/check_adr_gate.py`
(el hook de git pre-commit) ignoraba `.claude/adr-gate.conf` y usaba rutas hardcodeadas
heredadas de `ai-tooling`, mientras que `.claude/hooks/adr-gate.sh` (el hook `PreToolUse` de
Claude Code) sí leía ese conf — dos capas de enforcement con dos fuentes de verdad distintas.
Lo documentaron en `docs/adr/ADR-0009-unificacion-fuente-verdad-adr-gate.md` de `commons`, y
dejaron explícitamente fuera de alcance corregir `.pre-commit-config.yaml` y
`scripts/gitops-init.sh`, señalando que ese defecto lo distribuye `ai-tooling` (el repo
origen) a cada repo nuevo que bootstrapea, y que corregirlo ahí era tarea de un ADR futuro.

Al investigar si `ai-tooling` tenía el mismo problema, encontramos que **no** —
`tools/check_adr_gate.py` de este repo ya leía `.claude/adr-gate.conf` (función
`_load_conf_rules()`), incluso con soporte para la directiva `adr_path:` que la versión de
`commons` no porta. El único desajuste real aquí era cosmético: el docstring del módulo
seguía describiendo el comportamiento hardcodeado-únicamente, sin mencionar el conf.

Sí encontramos dos problemas reales, independientes del anterior:

1. **`scripts/gitops-init.sh` nunca genera `.claude/adr-gate.conf`** en el repo destino —
   en su lugar, `generate_adr_pattern()` inspeccionaba la estructura del repo (`vendor/`,
   `src/`, `projects/`, `.agents/`) y horneaba un regex estático directamente en el
   `files:` del hook `adr-gate` de `.pre-commit-config.yaml` generado. Esa heurística nunca
   llegaba a `.claude/adr-gate.conf`, así que un repo bootstrapeado terminaba con
   `check_adr_gate.py` cayendo a sus defaults hardcodeados de `ai-tooling`
   (`vendor/claude-code-proxy/`, `.agents/skills/`) — rutas que casi nunca aplican al repo
   nuevo — mientras el YAML generado usaba un regex distinto y más ajustado a esa
   estructura. Tres piezas debían coincidir (bash hook, python hook, YAML `files:`) y solo
   dos lo hacían.
2. **El hook `adr-gate` del `.pre-commit-config.yaml` generado tenía `pass_filenames: false`
   y `check_adr_gate.py` no calculaba los archivos por sí mismo** cuando no recibía
   `--changed-files` — es decir, invocado tal cual lo deja el framework `pre-commit` (sin
   argumentos), el gate revisaba una lista vacía y siempre pasaba, sin importar qué reglas
   tuviera configuradas. Confirmado corriendo el hook generado en un repo de prueba aislado
   antes de decidir el fix.

---

## Decisión

1. **`tools/check_adr_gate.py`** — se corrige el docstring para describir el comportamiento
   real (lee `.claude/adr-gate.conf`; fallback documentado). Se agrega `_staged_files()`:
   cuando no se pasó `--changed-files` explícito y stdin tampoco trajo nada útil, calcula los
   archivos directamente vía `git diff --cached --name-only` (y `--diff-filter=A` para
   nuevos) — el mismo cálculo que ya hacía a mano el hook crudo de `tools/install_hooks.sh`,
   ahora también disponible cuando el framework `pre-commit` invoca el script sin argumentos.
   Verificado en un repo de prueba: sin este fallback, el gate pasaba silenciosamente con 0
   archivos revisados; con él, detecta correctamente lo staged.
2. **`tools/install_hooks.sh`** — se agrega detección `PYTHON_BIN="python3"` con fallback a
   `python` (mismo problema de portabilidad que ya existía en `scripts/release.sh` con
   `sed -i`, para el mismo tipo de entorno: macOS sin shim de `python`).
3. **`templates/gitops/.pre-commit-config.yaml.template`** y la generación equivalente en
   **`scripts/gitops-init.sh`** — el hook `adr-gate` deja de tener `files:` con un regex
   horneado; pasa a `always_run: true` sin filtro. La decisión de "¿esta ruta está guardada?"
   vive enteramente en `check_adr_gate.py`/`adr-gate.sh` leyendo `.claude/adr-gate.conf`, no
   en un regex generado una sola vez al bootstrap.
4. **`scripts/gitops-init.sh`** — se elimina `generate_adr_pattern()` (sin caller tras el
   punto 3) y se reemplaza por `generate_adr_gate_rules()` + la generación real de
   `.claude/adr-gate.conf` en el repo destino, usando la misma heurística de directorios que
   antes solo alimentaba al YAML. Si el archivo ya existe, no se sobreescribe (mismo criterio
   que ya usa el script para `.pre-commit-config.yaml`). Verificado con un `--dry-run` y una
   corrida real contra un repo git aislado: el conf se genera con las reglas correctas y
   `check_adr_gate.py` las respeta sin flags explícitos.

### Fuera de alcance (no-goals)

- No se homologa la directiva `adr_path:` en la versión de `commons` (fuera de este repo).
- No se toca `CLAUDE.md` — su descripción del ADR-gate sigue siendo válida como
  comportamiento *default* quando no hay `.claude/adr-gate.conf`.
- No se backfillea `docs/adr/index.md` con los ADRs anteriores que ya faltaban del índice.
- No se corrige la numeración duplicada de `ADR-0008-*` (dos archivos existentes,
  preexistente y no relacionado a esta decisión).

---

## Consecuencias

### Positivas

- Un repo nuevo bootstrapeado por `gitops-init.sh` queda con **una sola** fuente de verdad
  de rutas guardadas (`.claude/adr-gate.conf`), leída de forma idéntica por las dos capas de
  enforcement (Claude Code y git pre-commit/CI) — ya no puede haber una tercera heurística
  divergente horneada en el YAML.
- El gate deja de pasar silenciosamente con 0 archivos cuando se invoca sin argumentos
  (framework `pre-commit`) — cierra el path muerto real, no solo el hipotético.
- `install_hooks.sh` funciona en máquinas donde solo existe `python3` en PATH.

### Negativas / Costos

- `check_adr_gate.py` ahora invoca `git diff --cached` como parte de su propia lógica (antes
  era responsabilidad exclusiva del caller) — acopla el script a estar dentro de un repo git
  cuando se le invoca sin argumentos; se degrada a lista vacía si `git` falla, no lanza
  excepción.
- Repos que ya tengan un `.pre-commit-config.yaml` generado por una versión anterior de este
  script conservan el `files: 'regex-horneado'` viejo hasta que lo regeneren manualmente
  (`rm .pre-commit-config.yaml && re-corre gitops-init.sh`) — no hay migración automática.

---

## Alternativas consideradas

### Alternativa A: reescribir `check_adr_gate.py` de `ai-tooling` para calcar el de `commons` línea por línea

Descartada tras leer el archivo completo: `commons` en realidad tiene una versión más simple
que la de `ai-tooling` (sin soporte de `adr_path:`), no una más avanzada — copiarla habría
sido una regresión de funcionalidad, no un fix.

### Elegida: conservar el conf-loading ya existente en `ai-tooling`, corregir solo el docstring y agregar el fallback de `git diff --cached`; cerrar la brecha real (tercera fuente de verdad + `pass_filenames` muerto) en `gitops-init.sh` y los templates.

Ver también: `docs/adr/ADR-0007-gitops-monorepo-trunk-based.md`, y en `commons`:
`docs/adr/ADR-0009-unificacion-fuente-verdad-adr-gate.md` (decisión original que motivó esta
investigación, aunque su premisa sobre `ai-tooling` resultó parcialmente desactualizada).
