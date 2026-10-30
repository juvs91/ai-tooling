# doclens — policy para Kimi Work

Kimi Work no tiene hooks de tool ni plugins de proyecto: el guardrail es esta
policy de sesión. Pégala en las instrucciones del proyecto o recuérdala al
abrir sesiones que toquen documentos.

## Regla

**Nunca leer un PDF crudo con ReadMediaFile ni adjuntarlo al backend.** El
backend Kimi rechaza PDFs con HTTP 400 y la sesión se envenena (todos los
mensajes siguientes fallan).

## Procedimiento

1. Antes de leer cualquier PDF o imagen de documento (> 8 MB), correr:

   ```bash
   doclens/.venv/bin/python doclens/doclens.py <archivo>
   ```

   (si falta `doclens/.venv`: `bash doclens/sync.sh` primero — crea el venv.
   Si el proyecto aún tiene la estructura vieja `scripts/doclens.py`, el sync de
   ai-tooling la migra a `doclens/`).

2. Leer el sidecar, nunca el original:
   - `<archivo>.inspect/index.md` — metadata, texto/OCR, tablas en markdown,
     topología por página con coordenadas.
   - `<archivo>.inspect/pages/page-N.png` — lectura visual con ReadMediaFile.
   - `<archivo>.inspect/figures/` — figuras extraídas + su OCR.

3. Imágenes sueltas grandes: verificar tamaño primero (`ls -l`); si > 10 MB,
   doclens genera copia downscaled — leer esa, nunca modificar el original.

## Notas

- doclens es idempotente: correrlo dos veces no reprocesa si el sidecar está
  fresco.
- OCR de scans y texto dentro de figuras requiere `tesseract` con idioma `spa`.
