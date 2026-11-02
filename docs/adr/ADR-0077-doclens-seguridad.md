# ADR-0077: Endurecimiento de seguridad de doclens: caché por usuario, sin shell, dependencias fijas

- **Estado:** Aceptado — implementado 2026-11-02
- **Fecha:** 2026-11-02
- **Autor:** Juve + Claude
- **Relacionado:** ADR-0076 (doclens homologado), plan `ai-notes/plans/2026-11-02-doclens-seguridad.md`

---

## Contexto

Revisión de seguridad independiente sobre doclens: `doc-guard.sh`, `doclens.py`, `sync.sh` y los adapters.
Cada hallazgo se reprodujo; los dos HIGH, también por separado. Ninguno de los dos HIGH requiere prompt
injection: basta con abrir un PDF de un repo o un zip preparado. Los dos vienen del diseño original
(school-system).

### Modelo de amenazas

| Activo | Por qué importa |
|---|---|
| Archivos del usuario (`~/.bashrc`, `~/.ssh`, repos) | Escribir en un rc de shell equivale a ejecutar código |
| Confidencialidad de PDFs privados | El digest contiene todo su texto y sus renders |
| Integridad de "lo que dice el documento" | El modelo actúa según el digest |
| Disponibilidad de la sesión | Un PDF crudo hacia Kimi la mata; CPU, RAM y disco |

**Actores:**
- **A1:** un documento malicioso (contenido y nombre).
- **A2:** un repo o zip no confiable (symlinks, digests plantados).
- **A3:** un modelo bajo prompt injection, que controla `file_path`.
- **A4:** otro usuario local en una máquina Linux compartida (`/tmp`).
- **A5:** una dependencia comprometida en PyPI.

**STRIDE:**
- **Spoofing:** un digest plantado.
- **Tampering:** escritura o borrado a través de symlinks.
- **Information disclosure:** permisos 644 o un `/tmp` compartido.
- **DoS:** bombas de render, `/dev/zero`, archivos enormes.
- **Elevation of privilege:** inyección de comandos y escritura en el rc del shell.

### Hallazgos

| # | Sev. | Hallazgo |
|---|---|---|
| 1 | HIGH | `doclens.py` escribía `<pdf>.inspect/` siguiendo symlinks. Un repo con `doc.pdf.inspect/.index.md.tmp -> ~/.bashrc` lograba que el digest, con el texto del PDF, terminara en el rc (verificado con `curl …\| sh`). Si el sidecar era un symlink, `rmtree` borraba carpetas ajenas. |
| 2 | HIGH | Guard de OpenCode: `` execSync(`… "${filePath}"`) ``. Un archivo `x$(cmd).pdf` ejecutaba `cmd` (verificado). |
| 3 | MEDIUM | Evasión del hook. Un nombre `a.txt<NL>b.pdf` (la extensión se calculaba por línea). Sin jq, `q\"x.pdf` o `pdf` pasaban. |
| 4 | MEDIUM | Digests con permisos 644/755: el texto de PDFs privados quedaba legible para todos. |
| 5 | MEDIUM | Fallback en `/tmp/doclens/<sha1(ruta)>`: predecible y pre-creable por otro usuario. |
| 6 | MEDIUM | DoS. Bomba de render (700 bytes → 1,9 GB de RSS). `/dev/zero` se leía para siempre (reproducido sin querer en las pruebas: 4 procesos al 98 % de CPU). `read_bytes()` cargaba el archivo completo. |
| 7 | LOW | Estado del hook: seguía un symlink y caía a `/tmp`, donde purgaba archivos del usuario. |
| 8 | LOW | Un digest plantado junto al PDF con el hash correcto se servía como si fuera auténtico. |
| 9 | LOW | `sync.sh` escribía a través de symlinks dentro de `doclens/`, como `.venv.failed`. |
| 10 | INFO | `requirements` sin pin ni hashes. `DOCLENS_HOOK_WAIT` se evaluaba en `$(( ))`. Fences de markdown que el texto podía cerrar. |

## Decisión

1. **Digests en un caché por usuario, direccionado por contenido** (decisión del usuario). Cierra #1, #4,
   #5 y #8.
   - Ubicación: `${DOCLENS_CACHE:-${XDG_CACHE_HOME:-~/.cache}/doclens}/<sha256>.inspect/`.
   - La raíz se valida: directorio real (no symlink), del usuario y sin permisos de grupo u otros. Si no
     cumple, no se usa (exit 5).
   - `umask 077`. Cada digest se genera en un `mkdtemp` (privado y nuevo) y se publica con un `rename`
     atómico.
   - Ya no se escribe nada junto al documento, así que un repo no puede plantar nada en el caché.
   - Purga por antigüedad (`DOCLENS_CACHE_DAYS`).
2. **Sin strings de shell con datos del modelo** (cierra #2): `execFileSync(bin, [args])` en el guard de
   OpenCode y en el snippet. El `dpi` del tool queda acotado.
3. **doc-guard** (cierra #3 y #7):
   - La extensión se calcula sobre la cadena completa.
   - Sin jq, el JSON se decodifica con python3. Si tampoco hay python3, o si falla, se bloquea cualquier
     cosa que pueda ser un PDF.
   - El estado vive en un directorio real, propio y 0700 (un 755 heredado y propio se endurece). Si no es
     posible, un `mktemp -d` de un solo uso. Nunca se usa `/tmp` pelado.
   - El atajo de caché solo aplica a archivos regulares.
   - `DOCLENS_HOOK_WAIT` acepta solo dígitos y como máximo 100.
   - `LAST_ERR` se sanea, y la ruta manual no usa nombres predecibles.
4. **Límites de recursos** (cierra #6):
   - archivos regulares de hasta `DOCLENS_MAX_MB` (200); la lectura de cabecera es de 8 bytes;
   - DPI entre 36 y 300; renders de 40 Mpx como máximo; renders de las primeras `DOCLENS_MAX_PAGES` (500)
     páginas;
   - en el envoltorio, `ulimit -t 900` y `-v 4 GB` (en Linux), y como máximo `DOCLENS_MAX_JOBS` (3)
     procesos.
5. **`sync.sh`** (cierra #9): se niega si hay symlinks dentro de `doclens/` (salvo `.venv/`), y hace `rm`
   antes de escribir `.venv.failed`.
6. **Dependencias fijas con hashes** (decisión del usuario; cubre A5): `requirements.txt` es un lock
   generado con `uv pip compile --universal --python-version 3.9 --generate-hashes` a partir de
   `requirements.in`, y se instala con `--require-hashes`. `pymupdf<2`: la 2.x es una versión mayor que
   arrastra onnxruntime y modelos de layout, y se evaluará por separado.
7. **Prompt injection:** cada `index.md` empieza marcando su contenido como no confiable, y las fences son
   más largas que cualquier racha de backticks del texto extraído.

Versión de doclens: 2.0.0 (cambia la ubicación de los digests) y `SIDECAR_FORMAT_VERSION` 2.

## Modelo de confianza (lo que NO se defiende)

El código del repo y del toolbox se ejecuta: el venv, `doclens/sync.sh`, `requirements` y el `local_path`
del marker `.ai-tooling`. Tiene el mismo nivel de confianza que los hooks del propio repo, protegido por el
diálogo de confianza de Claude Code al abrir un repo. No se hace fuzzing de PyMuPDF: la defensa son los
límites de recursos y las versiones fijas. La purga del caché es por antigüedad, sin cuota de disco.

## Verificación

- `tests/harness/doclens-tests.sh` D25 reproduce cada ataque:
  - symlinks plantados con el motor real, y víctima intacta;
  - digest plantado ignorado;
  - análisis multilínea de `exec*` con `filePath`;
  - nombre con salto de línea;
  - sin jq: escapes, python3 roto, y sin python3;
  - caché 0777 o como symlink;
  - `/dev/zero`, y ningún proceso colgado;
  - bomba de render ≤ 40 Mpx;
  - `DOCLENS_HOOK_WAIT` con `$(cmd)`;
  - estado del hook como symlink, sin escribir ni purgar a través de él;
  - estado 755 heredado endurecido;
  - `.venv.failed` como symlink;
  - symlink dentro de `doclens/`;
  - requirements con hashes.
- **Contra 38d9594 (antes del fix): 25 fallos.** Los 2 tests de D25 que pasan en ambas versiones son
  legítimos: uno de regresión y otro de una ruta que solo existe en el código nuevo. Al verificarlos aparecieron 4 tests vacíos o mal
  enfocados, y se corrigieron:
  - un stub en lugar del motor real;
  - `grep` por línea sobre un `execSync` multilínea;
  - `/usr/bin/jq` de macOS 15 en el PATH;
  - la purga de `find` que no sigue un symlink usado como punto de partida.
- Los tests usan su propio `TMPDIR` y terminan sus procesos en segundo plano al final, también cuando corren
  contra código vulnerable.
- Instalación real con `--require-hashes`: funciona, y con los hashes alterados no se instala nada.
