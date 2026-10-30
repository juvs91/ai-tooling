# ADR-0076: doclens homologado — misma estructura en ai-tooling y consumers, listo para usar

- **Estado:** Aceptado — implementado 2026-10-30
- **Fecha:** 2026-10-30
- **Autor:** Juve + Claude
- **Relacionado:** ADR-0072 (sync del harness), ADR-0073 (worktrees), plan
  `ai-notes/plans/2026-10-30-homologar-doclens.md`

---

## Contexto

doclens (procesa PDFs e imágenes antes de que el LLM los lea; el hook es `doc-guard.sh`) tenía dos
estructuras de archivos:

| | ai-tooling | consumer |
|---|---|---|
| motor | `doclens/doclens.py` | `scripts/doclens.py` |
| dependencias | `doclens/requirements.txt` | `scripts/requirements-doc-tools.txt` |
| versión | `doclens/VERSION` | `scripts/.doclens-version` |
| venv | (a mano) | `scripts/.venv-doc-tools` |

La diferencia ya causó un bug: en ai-tooling, doc-guard buscaba `scripts/doclens.py` y siempre caía al
bloqueo manual. Además:
- nadie creaba el venv en ai-tooling;
- los digests `*.inspect/` ensuciaban git;
- la sincronización solo comparaba `VERSION`, así que un adapter modificado sin subir la versión no llegaba
  a los consumers.

## Decisión

1. **Una sola estructura, también en los consumers** (decisión del usuario: espejo completo):
   ```
   doclens/{doclens.py, requirements.txt, VERSION, README.md, sync.sh, adapters/, .gitignore}  ← versionados
   doclens/.venv/                                                                              ← local
   ```
   `doclens/sync.sh` copia los archivos **versionados** de `ai-tooling/doclens` (`git ls-files`).
   - Compara por **contenido**, no por `VERSION`.
   - Respalda lo que pisa o borra en `.claude/.harness-backup-<ts>-doclens/`.
   - Reemplaza archivo por archivo con `mv` desde un staging en el mismo filesystem, sin tocar `.venv/`
     ni el lock.
   - Si el destino es su propio repo (ai-tooling, o un consumer corriendo su copia), no copia nada: solo
     asegura el venv. Así, `bash doclens/sync.sh` significa lo mismo en todos lados.
2. **Migración de la estructura vieja con respaldo** (decisión del usuario).
   - Solo aplica cuando hay rastros nuestros: `scripts/.doclens-version`, o un `scripts/doclens.py` con
     `DOCLENS_VERSION`.
   - Los 3 archivos van al respaldo y se quitan; el venv se **mueve** (sin red; `bin/python` sigue
     funcionando).
   - Un `scripts/doclens.py` ajeno no se toca.
   - Avisa, sin editarlos, de los archivos versionados que siguen usando la ruta vieja. No cuentan el propio
     doc-guard (que la usa a propósito como transición) ni `ai-notes/` y `docs/`.
3. **Listo para usar, sin pasos manuales:**
   - **Consumers:** `auto-sync-daily` (`SessionStart`, en segundo plano) registra doc-guard, deja el espejo
     y crea el venv.
   - **ai-tooling:** lo prepara `init.sh`. Además, **doc-guard lo crea solo** la primera vez que se lee un
     PDF (`sync.sh --venv`), y eso también sirve de red de seguridad en los consumers.
   - Un lock compartido (`doclens/.venv.lock`; se considera huérfano a los 10 min) evita dos instalaciones
     simultáneas. Mientras otro proceso instala, doc-guard responde "se está preparando".
   - Si la instalación falla, queda `doclens/.venv.failed` y no se reintenta durante 1 h (`--force`
     reintenta).
   - El venv se da por sano solo si importa `fitz`, `pdfplumber`, `pypdf` y `PIL`; uno roto se recrea.
4. **El venv de doclens nunca toca el Python del proyecto.** Es propio y aislado: PyMuPDF es AGPL y no
   debe entrar como dependencia del proyecto.
   - Python base: `$DOCLENS_PYTHON`, luego `/usr/bin/python3`, luego `python3` del PATH (≥3.9). Se pasa
     como ruta absoluta.
   - Se ejecuta sin `VIRTUAL_ENV`, `CONDA_PREFIX`, `PYTHONPATH` ni `PYTHONHOME`, y con
     `UV_PYTHON_DOWNLOADS=never` y `uv --no-config`: un `.python-version` de uv o pyenv del consumer no
     interfiere.
   - Usa uv si está disponible y, si no, `venv` + `python -m pip`.
   - En doc-guard, `DOCLENS_PYTHON` permite usar un intérprete que ya tenga las dependencias.
5. **Los digests se ignoran solos:** `<archivo>.inspect/.gitignore` = `*` (doclens 1.1.0). No hace falta
   tocar el `.gitignore` raíz de ningún repo.
6. **doc-guard y doctor:**
   - doc-guard usa `doclens/` y, solo como transición, el par viejo de `scripts/`.
   - El doctor usa `sync.sh --check` como única fuente de verdad.
   - Los adapters de OpenCode y Kimi pasan a las rutas nuevas, también con arranque perezoso.

## Alternativas descartadas

- **Copiar a los consumers solo lo necesario para ejecutar:** es menos ruido, pero no es "la misma
  estructura" (el usuario eligió el espejo completo).
- **Instalar las dependencias en el venv del proyecto:** mezcla licencias y versiones.
- **Mover todo el directorio en un solo `mv`:** rompería un venv en plena creación por otro proceso.

## Consecuencias y límites

- La migración deja cambios sin commitear en el consumer si tenía `scripts/doclens.py` versionado (lo
  mismo que ya pasaba al subir de versión).
- Los adapters copiados a mano con rutas viejas solo generan un aviso; no se editan archivos fuera de
  `doclens/`.
- El linter del consumer podría revisar `doclens/`. `doclens.py` pasa ruff con las reglas por defecto, y el
  README indica cómo excluirlo.
- Hace falta Python ≥3.9 (PyMuPDF). En un sistema con Python más viejo, doclens no queda disponible y
  doc-guard da el mensaje manual.
- Las actualizaciones llegan desde el clon local de ai-tooling (`git pull` ahí), igual que el resto del
  harness.
- El reemplazo es por archivo: si se interrumpe a mitad, la carpeta queda mezclada hasta la siguiente
  corrida, que converge por contenido; el respaldo conserva lo anterior.
- OCR (scans y texto en figuras) requiere tesseract, que no se instala solo; el smoke test lo avisa.

## Segunda validación (revisión adversarial independiente, 2026-10-30)

Un revisor independiente reprodujo 9 hallazgos, y se confirmó uno más que él no pudo verificar. Todos
están corregidos y cubiertos por tests (D14–D22):

- **Fail-open por timeout, el más grave:** según la documentación de Claude Code, un hook PreToolUse que
  vence su `timeout` **no bloquea**, y el Read sigue. Si crear el venv o procesar un PDF enorme tardaba más
  de 120 s, el PDF crudo llegaba al modelo.
  - Ahora doc-guard tiene **un único plazo de 90 s** para todo el hook.
  - Lo que no termina sigue en segundo plano, en su propio grupo de procesos, y el Read se bloquea con
    "reintenta".
  - Un lock por documento evita lanzar dos procesos.
- **`doclens/` como symlink:** el espejo escribía y borraba a través de él, fuera del repo. Ahora un symlink
  al propio toolbox cuenta como modo propio (solo venv), y uno a cualquier otro sitio da error sin tocar
  nada.
- **Monorepo:** sync subía a la raíz git, mientras que doc-guard y auto-sync usan `CLAUDE_PROJECT_DIR`.
  Ahora un destino explícito se usa tal cual.
- **`DOCLENS_PYTHON` tenía dos significados:** ahora siempre es "intérprete ya listo" en sync y en
  doc-guard. El Python base para crear el venv pasa a `DOCLENS_BASE_PYTHON`.
- **Rutas con `[` o `*`:** `find -path "$VENV*"` las tomaba como patrón y borraba el lock y directorios de
  otro proceso. Ahora usa rutas relativas.
- **Espejo desde HEAD, no desde el working tree:** lo borrado o editado sin commitear en ai-tooling ya no
  llega a los consumers. Esto corrige también la salida silenciosa por `pipefail` cuando el último archivo
  faltaba.
- **El lock guarda el PID:** si el dueño murió (por ejemplo con SIGKILL), se libera de inmediato, sin
  esperar 10 minutos.
- **Mensajes:** un PDF dañado ya no manda a "reinstalar el venv" y tampoco relanza la instalación. Si la
  carpeta del documento es de solo lectura, el digest va a un temporal estable.
- **Casos que dejaban pasar el PDF:** sin `jq`, los PDFs se bloquean igual, y un PDF real dentro de una
  carpeta `*.inspect/` se procesa. El fallback sin git ya no copia `.DS_Store`, `*.orig` ni caches.

## Tercera validación: regresiones de la segunda (2026-10-30)

Una revisión enfocada solo en lo que la segunda validación pudo haber roto. Partió de la suite
**original** de c02d00c: corrida contra el código nuevo, solo falló por un texto cambiado a propósito.
Encontró 6 regresiones menores, todas corregidas y cubiertas en D23. Esos tests fallan con el código de
90fa067 (14 fallos):

- **Archivo de estado viejo con un PID reutilizado:** el PDF quedaba en "reintenta" para siempre. Ahora el
  envoltorio borra el pidfile al terminar, y un pidfile de más de 30 min se ignora.
- **Carpeta de estado sin permiso de escritura:** se bloqueaban todos los PDFs. Ahora se verifica `-w`/`-O`
  y, si no, se usa un temporal propio.
- **Latencia y estado acumulado:**
  - lo que no es PDF ni imagen grande sale antes de cualquier preparación (40 ms, igual que antes);
  - un PDF con digest vigente responde desde bash, sin lanzar procesos: 86 ms, cuando c02d00c tardaba
    98 ms y 90fa067 322 ms;
  - el sondeo arranca en 50 ms;
  - el estado de más de 1 día se purga.
- **Nombres con `\`, comillas o tabuladores en `doclens/`:** abortaban todo el sync. Ahora se lista con
  `ls-tree -z`, y un archivo que no se puede copiar se omite con un aviso.
- **`DOCLENS_PYTHON` con el significado anterior** (Python sin dependencias): ahora no falla. sync avisa y
  usa `doclens/.venv`; doc-guard prueba ese intérprete y, si no sirve, usa el venv. Acepta nombres sin ruta
  (`python3`).
- **`sync.sh "" --check`:** un primer argumento vacío vuelve a contar como "sin destino".

Probado en vivo, sin stubs:
- sin venv, un PDF de 300 páginas creó el venv con uv y se procesó completo dentro del plazo;
- un consumer con la estructura vieja y un venv viejo real se migró con auto-sync real, el venv movido
  siguió funcionando y el PDF se procesó;
- un consumer nuevo quedó listo en ~6 s.

## Cuarta validación: uso real de punta a punta (2026-10-30)

Un revisor independiente probó con el motor real los escenarios de un día normal:
- muchos PDFs, repetidos, modificados, con acentos y espacios, enormes, cifrados, vacíos o en realidad
  imágenes;
- 5 sesiones a la vez sobre el mismo PDF;
- `git pull` del toolbox y cambio de rama en el consumer;
- venv borrado o corrupto.

En ningún caso el hook salió con un código distinto de 0/2 ni tardó más que el plazo. Encontró 7 problemas,
todos corregidos y cubiertos en D24. Los tests fallan con ebf032d (12 fallos):

1. **El caché se decidía por fecha** (problema heredado del motor original). Un PDF reemplazado con fecha
   vieja (`cp -p`, unzip, rsync, Finder) servía el digest del documento ANTERIOR sin aviso. Ahora el caché
   compara por **contenido**: sha256 en `.source`, tanto en `doclens.py` como en el camino rápido del hook.
   `.source` se escribe al final y el índice de forma atómica.
2. **Imagen con extensión `.pdf`:** el modelo quedaba sin salida, porque el índice decía "léela directo" y
   el hook la bloqueaba. Ahora se genera `as-image.png` y el hook apunta a ella.
3. **Procesos largos:**
   - El proceso en curso se reconoce por su comando (`doclens-bg` en `ps -ww`), no por la edad del
     pidfile, así que ya no se lanza un duplicado a los 30 min.
   - El mensaje dice cuánto lleva, y pasada 1 h responde "probablemente colgado" con la ruta manual, en
     lugar de un "reintenta" sin fin.
   - Corregidos dos bugs que salieron al escribir estos tests:
     - el código de salida 4 de doclens (contraseña) se confundía con "sigue corriendo", y ahora el
       "sigue corriendo" interno es 124;
     - `detach`, al ser función, dejaba un bash intermedio como `$!`; ahora usa `exec`.
4. **Al reprocesar se limpian `pages/` y `figures/`:** quedaban páginas viejas del documento anterior.
5. **No se respalda lo que git ya guarda sin cambios:** los respaldos ya no se acumulan al cambiar de rama,
   y el sync avisa que `doclens/` quedó sin commitear. El bloqueo de `git switch` mientras haya cambios del
   sync sin commitear es inherente a versionar el espejo: hay que commitearlo.
6. **Mensajes:**
   - un PDF con contraseña de apertura da un mensaje claro (código 4);
   - la metadata de cifrado sale de `metadata.encryption`;
   - si falla la creación del venv, el hook muestra la causa;
   - la ruta manual ya no usa un `/tmp` fijo y aclara que depende de las herramientas instaladas.
7. **Una imagen grande que doclens no puede abrir** ahora se bloquea con su motivo, en vez de pasar.

## Verificación

- `tests/harness/doclens-tests.sh` (57 casos): espejo, no-op, propagación sin subir `VERSION`, archivos
  sobrantes, migración y venv movido, archivo ajeno intacto, modo toolbox, venv aislado de un
  `VIRTUAL_ENV`/`CONDA_PREFIX`/`.python-version` del proyecto, venv roto, lock, fallo y `--force`,
  arranque perezoso de doc-guard, `DOCLENS_PYTHON`, auto-sync real, motor real y ruff.
- Mutaciones: venv sin aislar, archivo ajeno tomado como nuestro, doc-guard reportado como ruta vieja y
  espejo verificado solo por lista: las 4 detectadas.
- Suite completa en verde en macOS, Linux GNU, busybox y git 2.22.
- En vivo: sin venv, un `Read` de PDF en ai-tooling creó el venv real con uv y redirigió al digest.
