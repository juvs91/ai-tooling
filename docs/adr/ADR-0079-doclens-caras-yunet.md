# ADR-0079: doclens — cajas de caras con OpenCV YuNet

- **Estado:** Aceptado — 2026-11-02
- **Fecha:** 2026-11-02
- **Autor:** Juve + Claude
- **Relacionado:** ADR-0078 (OCR con cajas), ADR-0077 (seguridad y lock con hashes), ADR-0076 (espejo); plan
  `ai-notes/plans/2026-11-02-doclens-caras-yunet.md`. El consumidor que lo pide es ai_by_design: tapar caras en las
  imágenes que entran a su KDB.

---

## Contexto

- **El pedido:** ai_by_design tapa los nombres y las caras de las capturas de Teams y de las fotos antes de versionarlas.
- **Lo que ya hay:** doclens 2.2.0 da el OCR con cajas (ADR-0078), pero no las caras.
- **Prueba descartada:** se probó Vision de macOS desde un ayudante en Swift. Juventino pidió mantener todo en Python,
  así que ese ayudante se retiró.

Opciones en Python:
- **OpenCV Haar:** el modelo viene dentro de OpenCV, pero se le escapan más caras y da más falsos positivos.
- **OpenCV YuNet:** detecta mejor las caras pequeñas o de lado, pero necesita un archivo de modelo.

Juventino eligió YuNet el 2026-11-02.

## Decisión

1. **Modelo y dependencia:**
   - **Dependencia:** se agrega `opencv-python-headless` al lock con hashes (`requirements.in` y `requirements.txt`).
   - **Modelo:** se versiona en `doclens/models/face_detection_yunet_2023mar.onnx` (232,589 bytes; sha256
     `8f2383e4…2552fa4`, igual al oid del LFS de `opencv/opencv_zoo`).
   - **Verificación:** doclens comprueba el sha256 antes de usar el modelo. Si no coincide, no busca caras y el digest
     lo dice.
2. **Salida:** cada imagen suelta y cada figura de PDF tiene `<img>.faces.tsv` (`x0 y0 x1 y1 score`, en píxeles de esa
   imagen). El `index.md` lleva una sección "Caras" con la cantidad, o con el motivo si no se buscaron.
3. **Cómo detecta:**
   - corre sobre la imagen completa y sobre una cuadrícula de 3×3 recortes de la mitad del tamaño, ampliados 2 veces,
     porque en la imagen completa de una pantalla se pierden las caras pequeñas;
   - une las cajas que se solapan más de la mitad;
   - usa umbral 0.6 y NMS 0.3;
   - una imagen de más de `MAX_PAGE_PIXELS` no se procesa.
4. **Formato 5:** un digest hecho sin poder buscar caras se marca `5-sin-caras` y se rehace cuando ya se puede, en lugar
   de quedarse en la caché.
5. **`sync.sh`:**
   - `deps_ok` exige `cv2`, así que un venv anterior a este cambio se rehace;
   - el venv guarda el sha256 del lock (`.lock-sha256`): si el lock cambia, se rehace.

## Consecuencias

- **Versión:** doclens pasa a 2.3.0. El venv crece por OpenCV y numpy, y el repo crece ~230 KB por el modelo.
- **Lo que no hace:** doclens no tapa nada. Da las cajas, y cada consumidor decide qué hacer con ellas.
- **Pruebas:** no hay una prueba automática con una cara real, porque no se versiona la foto de una persona. Sí se
  prueban el formato, el modelo alterado, la regeneración y el venv. La detección se verificó a mano con cuadros
  reales del lote de ai_by_design.
