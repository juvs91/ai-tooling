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

El sidecar trae su propio `.gitignore` (`*`): analizar documentos en cualquier
repo no ensucia git.

## Estructura — igual en ai-tooling y en cada consumidor (ADR-0076)

```
doclens/
├── doclens.py, requirements.txt, VERSION, README.md, sync.sh, adapters/, .gitignore
│                     # versionados; en consumers = espejo exacto de ai-tooling/doclens
└── .venv/            # local, no versionado (lo crea sync.sh)
```

## Uso

```bash
doclens/.venv/bin/python doclens/doclens.py <archivo> [--dpi 150]
# stdout incluye: INSPECT_DIR=<ruta del sidecar>
```

En Claude Code no hace falta llamarlo: el hook `doc-guard` intercepta el `Read`
de un PDF y redirige al sidecar.

Idempotente: si el sidecar existe y es más reciente que el archivo, no
reprocesa. Exit codes: `0` ok · `2` formato no soportado · `3` dependencia
faltante.

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
