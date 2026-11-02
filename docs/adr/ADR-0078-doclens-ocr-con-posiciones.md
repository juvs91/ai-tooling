# ADR-0078: doclens — OCR completo y con posiciones de palabras, también en imágenes sueltas

- **Estado:** Aceptado — 2026-11-02
- **Fecha:** 2026-11-02
- **Autor:** Juve + Claude
- **Relacionado:** ADR-0076 (doclens homologado), ADR-0077 (seguridad), plan
  `ai-notes/plans/2026-11-02-doclens-ocr-posiciones.md`; consumidor que lo pide: ai_by_design (KDB, plan
  `ai-notes/plans/2026-11-02-ingesta-kdb-lote-quoting.md` de ese repo)

---

## Contexto

ai_by_design ingesta capturas, fotos y figuras de decks a su KDB. Para eso necesita dos cosas de cada imagen:

- **el texto que tiene dentro**, para transcribir la tarjeta de la figura;
- **dónde está cada palabra**, para tapar los nombres de personas antes de versionar la imagen.

Hoy doclens 2.0.0 no alcanza:

- Una imagen suelta solo da metadatos (`process_image`); no hay OCR.
- El OCR de una figura de PDF entra al `index.md` cortado a 200 caracteres (`clean(fig_text)`) y sin posiciones.
- El OCR usa solo `spa`, y buena parte del material está en inglés.
- La clave de la caché es solo el sha256 del contenido. Un digest hecho con un formato anterior se sigue sirviendo
  aunque al nuevo le falten archivos.

## Decisión

1. **OCR por palabra:** `tesseract … tsv` (nivel 5: palabra, caja `left top width height`, confianza). Por cada
   imagen OCR-eada, el digest guarda junto a ella:
   - `<imagen>.ocr.txt`: el texto completo, reconstruido por línea desde el TSV;
   - `<imagen>.ocr.tsv`: el TSV de tesseract tal cual. Las coordenadas son píxeles de esa imagen.

   El `index.md` sigue mostrando un extracto corto y apunta a los dos archivos.
2. **Imágenes sueltas:** también pasan por OCR. Se escribe `figures/image.ocr.txt` y `.ocr.tsv`, en píxeles de
   la imagen original. La imagen en sí no se copia: se lee directo, como hasta hoy.
3. **Idiomas:** `DOCLENS_OCR_LANG`, por omisión `spa+eng`, limitado a los idiomas que tesseract tenga instalados.
   Si no hay ninguno, se usa el idioma por omisión de tesseract.
4. **Versión de formato en el digest:** cada digest guarda `.format` con `SIDECAR_FORMAT_VERSION` (pasa a 3).
   - Si el digest es de otro formato, es un *miss*: se regenera y se reemplaza de forma atómica. El viejo se mueve
     a un nombre temporal y se borra.
   - La ruta sigue siendo `<sha256>.inspect`, así que doc-guard no cambia.
5. **Los límites no cambian:** `MAX_OCR_PAGES`, `MAX_SECONDS`, timeout de 60 s por imagen y figuras de más de
   40 Mpx omitidas.

## Consecuencias

- **Versión:** doclens pasa a 2.1.0. Los consumers lo reciben con `doclens/sync.sh`, que compara por contenido.
- **Caché:** los digests 2.0.0 se regeneran la primera vez que se vuelven a pedir.
- **Lo que no cambia:** doclens no tapa nada. Solo da las posiciones, y cada consumidor decide qué hacer con
  ellas (ai_by_design: tapar nombres de su tabla de roles antes de versionar la imagen).
- **Más disco:** el `.ocr.tsv` ocupa unos KB por figura.
