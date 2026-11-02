# Plan: doclens con OCR completo y posiciones de palabras (ADR-0078)
Estado: ejecutado (2026-11-02) — ADR-0078

## Contexto

Juventino aprobó el 2026-11-02, desde la sesión de ai_by_design, la propuesta de usar doclens para la información
de las figuras que entran a la KDB. Hay que tapar nombres de personas dentro de las imágenes y transcribir el
texto de cada figura.

Lo que falta en doclens 2.0.0 está en ADR-0078:
- OCR de imágenes sueltas;
- texto completo;
- posiciones por palabra;
- `spa+eng`;
- invalidar los digests de un formato anterior.

## Cambios

1. `doclens/doclens.py`:
   - `ocr_words()` lee el TSV de tesseract y arma el texto por línea.
   - `write_ocr()` deja `<img>.ocr.txt` y `<img>.ocr.tsv`.
   - Se usa en las figuras de PDF, en las páginas escaneadas (solo texto) y en `process_image`.
   - Se agrega `DOCLENS_OCR_LANG`.
   - `SIDECAR_FORMAT_VERSION = 3`, con el archivo `.format`. Un digest de otro formato se regenera y se reemplaza
     de forma atómica.
2. `doclens/VERSION` pasa a 2.1.0. Se actualizan `doclens/README.md` y la fila de doc-guard en `CLAUDE.md`, si
   cambia lo que hace.
3. `tests/harness/doclens-tests.sh`, solo con el motor real y tesseract instalado:
   - una imagen con texto da `.ocr.txt` y `.ocr.tsv` con cajas;
   - un digest con `.format` viejo se regenera.
4. Se instala `brew install tesseract tesseract-lang` en la Mac (2026-11-02).

## Verificación

- `bash tests/harness/doclens-tests.sh` pasa completo.
- Sobre una captura real del lote de ai_by_design: el TSV trae palabras con caja, y el texto coincide con lo que
  se ve en la imagen.
- `doclens/sync.sh` en ai_by_design deja el espejo idéntico y el venv listo.
