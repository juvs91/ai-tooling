# doclens

**Pre-procesa documentos (PDF/imágenes) para sesiones agénticas. Nunca mandes
el crudo al LLM.**

El backend Kimi (y otros) rechazan PDFs crudos con HTTP 400 y la sesión se
envenena. doclens procesa primero y genera un **sidecar** `<archivo>.inspect/`
con artifacts estructurados que el agente sí puede leer:

```
<archivo>.inspect/
├── index.md          # metadata + inventario de páginas + texto (capa de texto
│                     #   u OCR spa si es scan) + tablas en markdown +
│                     #   topología (bloques con coordenadas, dibujos vectoriales)
├── pages/page-N.png  # render de cada página (lectura visual del LLM)
└── figures/          # figuras embebidas extraídas + OCR del texto DENTRO
                      #   de figuras (si hay tesseract)
```

## Uso

```bash
python doclens.py <archivo> [--dpi 150]
# stdout incluye: INSPECT_DIR=<ruta del sidecar>
```

Idempotente: si el sidecar existe y es más reciente que el archivo, no
reprocesa. Exit codes: `0` ok · `2` formato no soportado · `3` dependencia
faltante.

## Setup por proyecto consumidor

```bash
python3 -m venv .venv-doclens
.venv-doclens/bin/pip install -r requirements.txt   # pymupdf, pdfplumber, pypdf, pillow
# opcional (OCR de scans y figuras): brew install tesseract tesseract-lang
```

## Versionado y sync

- `VERSION` = versión del motor. `SIDECAR_FORMAT_VERSION` (constante en
  `doclens.py`) = versión del contrato del sidecar; los adapters pueden
  detectar sidecars viejos.
- Los proyectos **no editan su copia local**: se sincroniza desde este toolbox
  con `scripts/sync-doclens.sh` (en cada proyecto consumidor).

## Adapters por agente (`adapters/`)

| Agente | Adapter | Qué hace |
|---|---|---|
| Claude Code | `claude-code/doc-guard.sh` | Hook PreToolUse/Read: intercepta PDF, corre doclens, redirige al digest |
| OpenCode | `opencode/doclens-tool.ts` | Custom tool nativo `doclens` (vía proactiva) |
| OpenCode | `opencode/guards-snippet.ts` | Bloque `tool.execute.before` defensivo para el plugin de guards |
| Kimi Work | `kimi-work/instrucciones.md` | Policy de sesión (no requiere código) |

## Proyectos consumidores

- **school-system** (`/Volumes/case-sensitive/school-system`) — origen del
  mecanismo; consume vía copia versionada (`scripts/doclens.py` +
  `scripts/sync-doclens.sh`).
