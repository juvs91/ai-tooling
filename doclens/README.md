# doclens

**Pre-procesa documentos (PDF/imágenes) para sesiones agénticas. Nunca mandes
el crudo al LLM.**

El backend Kimi (y otros) rechazan PDFs crudos con HTTP 400 y la sesión se
envenena. doclens procesa primero y genera un **digest** con artifacts
estructurados que el agente sí puede leer, en un **caché por usuario**
(ADR-0077), nunca junto al documento:

```
${DOCLENS_CACHE:-~/.cache/doclens}/<sha256 del contenido>.inspect/   (0700)
├── index.md          # metadata + inventario de páginas + texto (capa de texto
│                     #   u OCR spa si es scan) + tablas en markdown +
│                     #   topología (bloques con coordenadas, dibujos vectoriales)
├── pages/page-N.png  # render de cada página (lectura visual del LLM)
├── figures/          # figuras embebidas extraídas + OCR del texto DENTRO
│                     #   de figuras (si hay tesseract): <fig>.ocr.txt completo
│                     #   y <fig>.ocr.tsv con cada palabra y su caja (px, ADR-0078);
│                     #   una imagen suelta: figures/image.ocr.txt y .ocr.tsv
├── figures/…faces.tsv  # cajas de caras (x0 y0 x1 y1 score, px) con OpenCV YuNet (ADR-0079)
└── .format           # versión de formato del digest: si no es la actual (o se hizo sin caras), se regenera
```

Por qué un caché por usuario (ADR-0077): un repo o zip no confiable no puede
plantar ahí symlinks (que harían a doclens escribir en `~/.bashrc`) ni digests
falsos; el texto de un PDF privado no queda legible para otros usuarios; y no se
ensucia ningún repo. La raíz se valida (propia, no symlink, 0700) o no se usa.
Cada `index.md` empieza marcando su contenido como **no confiable** (datos, no
instrucciones) — el texto de un documento es un canal de prompt injection.

## Estructura — igual en ai-tooling y en cada consumidor (ADR-0076)

```
doclens/
├── doclens.py, requirements.in/.txt, VERSION, README.md, sync.sh, adapters/, .gitignore
│                     # versionados; en consumers = espejo exacto de ai-tooling/doclens
└── .venv/            # local, no versionado (lo crea sync.sh)
```

## Uso

```bash
doclens/.venv/bin/python doclens/doclens.py <archivo> [--dpi 150]
# stdout incluye: INSPECT_DIR=<ruta del digest en el caché>
```

En Claude Code no hace falta llamarlo: el hook `doc-guard` intercepta el `Read`
de un PDF y redirige al digest.

Idempotente por **contenido**: la clave del caché es el sha256 del archivo, así
que un PDF ya visto no se reprocesa y uno reemplazado (aunque conserve la fecha)
sí. Cada digest se genera en un directorio privado nuevo y se publica con un
rename atómico. Exit codes: `0` ok · `1` no existe · `2` formato no soportado ·
`3` dependencia faltante · `4` PDF con contraseña · `5` caché inseguro ·
`6` no es un archivo regular · `7` excede el tamaño máximo.

Caras (ADR-0079): OpenCV YuNet, modelo en `doclens/models/` verificado por sha256; `sync.sh` rehace el venv si falta
`cv2` o si cambió el lock.

OCR (ADR-0078): idiomas `DOCLENS_OCR_LANG` (por omisión `spa+eng`, solo los instalados;
`brew install tesseract tesseract-lang`).

Límites contra documentos hostiles (variables de entorno): `DOCLENS_MAX_MB`
(200), `DOCLENS_MAX_PAGES` con render (500; el texto es completo), área máxima
por render 40 Mpx, DPI 36–300, `DOCLENS_CACHE_DAYS` (30, purga de digests sin
uso), `DOCLENS_MAX_JOBS` (3 procesos a la vez), `DOCLENS_MAX_OCR_PAGES` (50),
`DOCLENS_MAX_SECONDS` (1800, tiempo total incluido tesseract),
`DOCLENS_MAX_FIGURES` (200, figuras distintas; una imagen repetida en varias páginas
se extrae una vez), `DOCLENS_MAX_DIGEST_MB` (500, disco total del digest); figuras
de más de 40 Mpx se omiten sin decodificarlas; en el hook, CPU ≤ 900 s y memoria ≤ 4 GB en
Linux. doc-guard decide sobre la ruta canónica (`~`, `..`, symlinks) y detecta
PDFs sin extensión `.pdf` por su contenido (`%PDF-`).

## Dependencias: versiones fijas con hashes (ADR-0077)

`requirements.txt` es un lock generado desde `requirements.in` y se instala con
`--require-hashes` (una versión distinta o un paquete alterado no se instala).
Para actualizar:

```bash
uv pip compile --universal --python-version 3.9 --generate-hashes \
  doclens/requirements.in -o doclens/requirements.txt
```

## Listo para usar (sin pasos manuales)

- **Consumers:** `auto-sync-daily.sh` (SessionStart, en segundo plano) corre
  `sync.sh` del toolbox: deja `doclens/` como espejo, migra la estructura vieja
  (`scripts/doclens.py`, con respaldo en `.claude/.harness-backup-*-doclens/`) y
  crea `doclens/.venv`.
- **ai-tooling:** `init.sh` crea el venv; si falta, `doc-guard` lo prepara en el
  primer `Read` de un PDF.
- Manual (ambos): `bash doclens/sync.sh` (`--force` reintenta tras un fallo,
  `--check` solo reporta).
- El venv es **propio y aislado**: nunca usa ni modifica el venv del proyecto
  (`.venv`, poetry, conda) — PyMuPDF es AGPL y no debe entrar como dependencia
  del proyecto. Python base: `$DOCLENS_BASE_PYTHON`, si no `/usr/bin/python3`, si
  no `python3` del PATH (≥3.9), sin heredar `VIRTUAL_ENV`/`CONDA_PREFIX`; con `uv`
  si está disponible.
- `DOCLENS_PYTHON`: un intérprete que YA tiene las dependencias; sync y doc-guard
  lo usan en lugar de `doclens/.venv` (que entonces no se crea).
- doc-guard nunca espera más de 90 s (`DOCLENS_HOOK_WAIT`): un hook que vence su
  timeout NO bloquea en Claude Code y el PDF crudo pasaría. Lo que tarda más
  (venv la primera vez, documento enorme) sigue en segundo plano y el Read se
  bloquea con "reintenta en un momento".
- OCR opcional (scans y texto en figuras): `brew install tesseract tesseract-lang`.

## Versionado y sync

- `VERSION` = versión del motor. `SIDECAR_FORMAT_VERSION` (constante en
  `doclens.py`) = versión del contrato del sidecar; los adapters pueden
  detectar sidecars viejos.
- Los proyectos **no editan su copia local**: el sync compara por contenido y
  la reemplaza (respaldando lo que pisa). Los cambios se hacen aquí y llegan a
  los consumers al **commitearlos**: el espejo es HEAD de ai-tooling, no su
  working tree.
- `sync.sh <repo>` usa `<repo>` tal cual (en un monorepo, el subproyecto). Si
  `doclens/` del destino es un symlink a otra carpeta, no se toca.
- Si el linter del consumer (ruff, mypy, pre-commit) revisa `doclens/`,
  exclúyelo en su configuración (p. ej. `extend-exclude = ["doclens"]` en ruff).

## Adapters por agente (`adapters/`)

| Agente | Adapter | Qué hace |
|---|---|---|
| Claude Code | `claude-code/doc-guard.sh` | Hook PreToolUse/Read: intercepta PDF, corre doclens, redirige al digest |
| OpenCode | `opencode/doclens-tool.ts` | Custom tool nativo `doclens` (vía proactiva) |
| OpenCode | `opencode/guards-snippet.ts` | Bloque `tool.execute.before` defensivo para el plugin de guards |
| Kimi Work | `kimi-work/instrucciones.md` | Policy de sesión (no requiere código) |

## Proyectos consumidores

- **school-system** — origen del mecanismo. Usaba la estructura vieja
  (`scripts/doclens.py`); el sync la migra a `doclens/` y avisa si algún archivo
  (p. ej. un adapter de OpenCode copiado a mano) sigue usando la ruta vieja.
