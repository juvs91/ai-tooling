# Plan: homologar doclens (misma carpeta `doclens/` en el toolbox y en los consumidores), ADR-0076
Estado: ejecutado (2026-10-30) — ADR-0076

## Contexto
Hoy doclens tiene dos estructuras distintas, y eso ya causó un bug: doc-guard nunca encontraba el motor
en ai-tooling y caía siempre al bloqueo manual (corregido con un fallback en e72db42).

| Archivo | Toolbox (ai-tooling) | Consumidor |
|---|---|---|
| Motor | `doclens/doclens.py` | `scripts/doclens.py` |
| Dependencias | `doclens/requirements.txt` | `scripts/requirements-doc-tools.txt` |
| Versión | `doclens/VERSION` | `scripts/.doclens-version` |
| venv | (a mano) `scripts/.venv-doc-tools` | `scripts/.venv-doc-tools` |

Hay 13 archivos que dependen de las rutas viejas: `doc-guard.sh` (y su copia en el adapter), `doclens/sync.sh`,
`auto-sync-daily.sh`, `harness-doctor.sh`, los adapters de OpenCode y Kimi, `.opencode/*` del toolbox,
README, CLAUDE.md y los tests W14/W15.

**Decisiones del usuario:**
- **Espejo completo:** el consumidor recibe todo `doclens/` del toolbox: `doclens.py`, `requirements.txt`,
  `VERSION`, `README.md`, `sync.sh` y `adapters/`.
- **Migración con respaldo:** los consumidores con la estructura vieja pasan a `doclens/` en el sync diario.

En esta máquina no hay consumidores de doclens (school-system está en otra), así que la migración se prueba
con fixtures.

## Estructura final (idéntica en toolbox y consumidor)
```
doclens/
├── doclens.py, requirements.txt, VERSION, README.md, sync.sh, adapters/   (versionados, espejo)
├── .gitignore        (nuevo, versionado: ".venv/")
└── .venv/            (local, no versionado; lo crea/repara `bash doclens/sync.sh`)
```
`bash doclens/sync.sh` hace lo mismo en ambos lados: si el destino es el propio toolbox (o un consumidor
corriendo su copia espejo), no copia nada y solo asegura el venv y el smoke test.

## Cambios

### 1. `doclens/sync.sh`: espejo + migración (núcleo)
- **Fuente del espejo:** `git -C <toolbox> ls-files -- doclens`, es decir, solo archivos versionados (nunca
  `.venv/`, `__pycache__`, ni `*.inspect/`). Si el toolbox no es un repo git, usa `find` excluyendo esos
  nombres.
- **Detección de cambios por contenido**, no por `VERSION`: el consumidor está al día si cada archivo
  coincide (`cmp`) y no sobra ninguno. Así un cambio en un adapter sin subir la versión también se
  propaga.
- **Reemplazo seguro:**
  1. Arma la copia nueva en `"$REPO_ROOT/.doclens-sync.XXXXXX"` (con `mktemp -d` de 6 X).
  2. Si el `doclens/` actual difiere, respalda todo (salvo `.venv/`) en
     `.claude/.harness-backup-<ts>-doclens/`.
  3. Pasa `.venv/` a la copia nueva con `mv` y reemplaza el directorio.
  4. Si algo falla, el `doclens/` anterior queda intacto.
- **Migración de la estructura vieja** (se aplica cuando hay rastros nuestros: `scripts/.doclens-version`,
  o un `scripts/doclens.py` que contenga `DOCLENS_VERSION`):
  - copia `scripts/doclens.py`, `scripts/requirements-doc-tools.txt` y `scripts/.doclens-version` al mismo
    respaldo y los quita;
  - si `doclens/.venv` no existe y `scripts/.venv-doc-tools/bin/python` sí, **mueve el venv** (con `mv`,
    sin reinstalar ni usar la red). Un venv movido sigue funcionando con `bin/python`.
  - Un `scripts/doclens.py` ajeno (sin esas marcas) **no se toca**.
- **Aviso de referencias viejas:** busca con `git ls-files | xargs grep -l` las menciones a
  `scripts/doclens.py` o `.venv-doc-tools` en archivos del consumidor, fuera de `doclens/` y de los
  respaldos (por ejemplo, un `.opencode/tools/doclens.ts` copiado a mano). Las lista en la salida, que
  auto-sync guarda en `sync.log`.
- **venv:** usar `"$VENV/bin/python" -m pip` en lugar de `bin/pip` (el shebang de pip queda roto en un venv
  movido). El smoke test importa desde `doclens/`.
- **`--check`** (para el doctor), con estos mensajes:
  - `doclens al día`
  - `update disponible`
  - `estructura vieja: migración pendiente`
  - `toolbox: solo venv`

### 2. `doclens/doclens.py`: carpetas `.inspect/` que se ignoran solas
Al crear `<archivo>.inspect/`, escribir dentro un `.gitignore` con `*` (igual que hace `.ruff_cache`). Así
analizar documentos en cualquier repo no ensucia git, sin tocar el `.gitignore` raíz del consumidor. Se
sube `DOCLENS_VERSION` y `VERSION` a 1.1.0.

### 3. `doc-guard.sh` (+ copia en `doclens/adapters/claude-code/`)
- **Rutas principales:** `$ROOT/doclens/doclens.py` y `$ROOT/doclens/.venv/bin/python`.
- **Respaldo de transición:** el par viejo `scripts/doclens.py` + `scripts/.venv-doc-tools`. Cubre a los
  consumidores con `sync_doclens:false` o con el hook actualizado antes de migrar.
- **Mensaje manual:** si existe `doclens/sync.sh`, indica `bash doclens/sync.sh` (crea el venv); si no, las
  instrucciones viejas. Se quita el fallback `REQS` de e72db42.
- Las dos copias quedan idénticas (`cmp`), y un test lo comprueba.

### 4. Listo para usar sin pasos manuales (toolbox y consumidores)
Objetivo: leer un PDF funciona desde la primera vez, sin crear venvs ni instalar nada a mano.
- **Consumidores (ya casi listo, se verifica):** en el `SessionStart` asíncrono, auto-sync hace tres cosas:
  1. `install-hooks` registra `doc-guard` (verificado: "INSTALAR .claude/hooks/doc-guard.sh");
  2. `doclens/sync.sh` deja el espejo listo (`sync_doclens` es true por defecto);
  3. **crea el venv en segundo plano**, sin bloquear la sesión.
- **Toolbox:** auto-sync no corre aquí (`consumer-only`), así que se agregan dos vías:
  - `init.sh` llama a `bash doclens/sync.sh` (junto al paso de `install_hooks.sh`, con `|| log_warn`);
  - **arranque perezoso en doc-guard**, que también sirve de red de seguridad en los consumidores.
- **Arranque perezoso en doc-guard:** si existe `doclens/doclens.py` pero el venv falta o está roto, corre
  `bash "$ROOT/doclens/sync.sh"` (que en modo propio solo asegura el venv) y luego procesa el PDF.
  - Usa un lock (`mkdir doclens/.venv.lock`) para que dos lecturas simultáneas no creen dos venvs.
    `sync.sh` toma **el mismo** lock al crear el venv. Si el usuario lee un PDF mientras el auto-sync de la
    primera sesión todavía instala en segundo plano, doc-guard responde "doclens se está preparando
    (primera vez, ~30 s); reintenta en unos segundos" en lugar de lanzar una segunda instalación. Un lock
    de más de 10 minutos se considera huérfano y se libera.
  - Si falla (por ejemplo, sin red), escribe `doclens/.venv.failed` y no reintenta durante 1 hora; mientras
    tanto da el mensaje manual (el hook tiene timeout de 120 s).
  - `doclens/.gitignore` cubre `.venv*`.
- **Creación del venv en `sync.sh`, robusta:**
  - con `uv` disponible: `uv venv` + `uv pip install` (más rápido, y comprobado que funciona tras el proxy
    corporativo);
  - si no: `python3 -m venv` + `python -m pip`;
  - salud = el smoke test importa `fitz`, `pdfplumber`, `pypdf` y `PIL`. No basta con que exista
    `bin/python`; si falla, se recrea.
- **OCR opcional:** sin `tesseract` todo funciona salvo el OCR de PDFs escaneados y figuras. El smoke test lo
  informa (`OCR: no disponible — brew install tesseract tesseract-lang`), sin fallar.

### 5. Convivencia con venvs y Python propios del consumidor
- **Aislamiento:** el venv de doclens (`doclens/.venv`) está separado del de cada proyecto (`.venv/`,
  poetry, conda…): ni lo reutiliza ni le instala nada. Así PyMuPDF (licencia **AGPL**), pdfplumber y
  compañía nunca entran como dependencias del proyecto, y no hay choques de versiones con sus librerías.
- **Elección del intérprete base**, explícita y en este orden:
  1. `$DOCLENS_PYTHON` (override opcional);
  2. `/usr/bin/python3`;
  3. `python3` del PATH.

  Se toma el primero que exista y sea ≥3.9, y se le pasa como ruta absoluta a `uv venv --python` o a
  `<python> -m venv`. Además:
  - se ejecuta con `VIRTUAL_ENV`, `CONDA_PREFIX` y `PYTHONPATH` sin definir, para que un venv o conda
    activado del proyecto no se mezcle;
  - `UV_PYTHON_DOWNLOADS=never` y la ruta explícita evitan que un `.python-version` del consumidor (uv o
    pyenv) haga que uv descargue otro Python o que un shim de pyenv falle por una versión no instalada.
- **`DOCLENS_PYTHON` también en doc-guard:** si el consumidor ya tiene un intérprete con las dependencias
  y prefiere no crear otro venv, doc-guard usa ese en lugar de `doclens/.venv` (validado con el mismo smoke
  test).
- **Venv viejo `scripts/.venv-doc-tools`:** es nuestro y se mueve (§1). Cualquier otro venv del consumidor
  no se toca.
- **Linters del consumidor:** con el espejo completo, `doclens/doclens.py` y los `.ts` de los adapters
  quedan versionados en el consumidor, así que su ruff, mypy o pre-commit podrían revisarlos. Se documenta
  cómo excluir `doclens/` (no se edita la configuración del consumidor). Los tests verifican que
  `doclens.py` pasa ruff con la configuración por defecto, para no exportar avisos.

### 6. Resto de referencias
- `harness-doctor.sh` §5: usa `sync.sh --check` (una sola fuente de verdad). Cualquier resultado distinto
  de "al día" o "toolbox" produce un WARN.
- `auto-sync-daily.sh` §3: solo se actualizan los comentarios; la llamada a `sync.sh` no cambia.
- Adapters de OpenCode (`doclens-tool.ts`, `guards-snippet.ts`), `.opencode/tools/doclens.ts`,
  `.opencode/plugins/guards.ts:47` y `adapters/kimi-work/instrucciones.md`: pasan a las rutas nuevas.
- `doclens/README.md`: sección de estructura y de consumidores. Agregar `doclens/.gitignore`.
- **Toolbox:** mover mi venv `scripts/.venv-doc-tools` → `doclens/.venv` (lo hace el propio `sync.sh`).
  En `.gitignore`, quitar la línea `scripts/.venv-doc-tools/` y dejar `*.inspect/`, que sigue cubriendo
  carpetas viejas.
- `CLAUDE.md` (fila de doc-guard), `docs/adr/ADR-0076-…` + `index.md`, `AI_LEARNING.md`, y este plan en
  `ai-notes/plans/2026-10-30-homologar-doclens.md`.

## Archivos
`doclens/{sync.sh, doclens.py, VERSION, README.md, .gitignore(nuevo), adapters/claude-code/doc-guard.sh,
adapters/opencode/*.ts, adapters/kimi-work/instrucciones.md}`, `.claude/hooks/doc-guard.sh`,
`.opencode/{tools/doclens.ts, plugins/guards.ts}`, `scripts/harness-doctor.sh`,
`.claude/hooks/auto-sync-daily.sh` (solo comentarios), `init.sh`, `.gitignore`, `CLAUDE.md`,
`tests/harness/{doclens-tests.sh(nuevo), worktree-tests.sh, run-tests.sh}`, `docs/adr/…`, `ai-notes/…`.

## Verificación
1. **`tests/harness/doclens-tests.sh`** (nuevo, lo invoca `run-tests.sh`). El venv es un stub de
   `bin/python` que responde a `-c` y `-m pip`: así los tests no usan red.
   - consumidor nuevo → `doclens/` idéntico a lo versionado en el toolbox (lista de archivos + `cmp`) y
     `.venv` preservado;
   - segunda corrida → "al día", sin cambiar mtimes ni crear respaldo;
   - un adapter cambiado en el toolbox sin subir `VERSION` → se propaga;
   - un archivo que sobra en el consumidor → se elimina, con respaldo;
   - consumidor con la estructura vieja → archivos viejos respaldados y quitados, venv movido (el stub
     conserva su marca) y aviso por la referencia en `.opencode/tools/doclens.ts`;
   - un `scripts/doclens.py` ajeno → intacto;
   - el toolbox mismo → no copia, solo asegura el venv; `--check` devuelve "toolbox";
   - auto-sync de punta a punta en un consumidor viejo → migra, y el doctor deja de avisar;
   - las dos copias de `doc-guard.sh` son idénticas;
   - `.inspect/.gitignore` creado. Requiere dependencias reales: corre si existe `doclens/.venv` y, si no,
     imprime "omitido".
   - arranque perezoso: si falta el venv, doc-guard corre `sync.sh` (stub) y procesa el PDF; con el lock
     tomado no lanza un segundo arranque; con `.venv.failed` reciente no reintenta y da el mensaje manual;
   - un venv roto (el stub falla el smoke test) → `sync.sh` lo recrea.
   - con `VIRTUAL_ENV` apuntando a un venv del proyecto, el venv nuevo se crea con el Python base elegido
     y el venv del proyecto queda intacto (`cksum` de su `pyvenv.cfg` y de `site-packages`);
   - `DOCLENS_PYTHON` → doc-guard lo usa y no crea `doclens/.venv`;
   - un `.python-version` en el consumidor que pide una versión inexistente no rompe la creación.
   - `ruff check doclens/doclens.py` limpio con la configuración por defecto.
2. **worktree-tests W14/W15:** se pasan a la estructura nueva, más un caso de la estructura vieja vía el
   respaldo de transición.
3. Suite completa en macOS, Linux GNU, busybox y git 2.22 (Docker, como antes).
4. **En vivo en ai-tooling:**
   - **listo para usar:** con el venv borrado, un `Read` de PDF lo crea solo (arranque perezoso con `uv`) y
     redirige al digest;
   - **consumidor desde cero:** una carpeta nueva con `.ai-tooling`, corriendo `auto-sync-daily` real (con
     red) → `doclens/` espejo + venv real + `doc-guard` registrado, y luego el hook procesa un PDF real;
   - `bash doclens/sync.sh` mueve el venv;
   - un `Read` del PDF de prueba redirige al digest;
   - `git status` limpio (`.inspect/` y `.venv/` ignorados);
   - el doctor sin avisos.
5. `./scripts/task-verify.sh` → commit → push.

## Límites (van al ADR)
- Si un consumidor tiene un adapter de OpenCode o Kimi copiado a mano con las rutas viejas, el sync
  **solo avisa**: no edita archivos fuera de `doclens/`.
- Si el consumidor tenía `scripts/doclens.py` versionado en git, la migración deja cambios sin commitear
  (borrado + `doclens/` nuevo), igual que hoy deja cambios cuando sube de versión.
- Dos sesiones sincronizando a la vez: cada una arma su copia en un directorio temporal único; el último
  `mv` gana y el contenido es idéntico.
