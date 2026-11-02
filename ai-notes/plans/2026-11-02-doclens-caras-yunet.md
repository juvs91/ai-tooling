# Plan: doclens con cajas de caras (OpenCV YuNet, ADR-0079)
Estado: ejecutado (2026-11-02) — ADR-0079

## Contexto

Juventino pidió el 2026-11-02, desde la sesión de ai_by_design, que se tapen las caras de los participantes en las
imágenes que entran a la KDB, "puro python" y con YuNet. La detección vive en doclens. El tapado y los recuadros
manuales los hace el consumidor, solo sobre las imágenes que se extraen.

## Cambios

1. `doclens/requirements.in` y `doclens/requirements.txt`: `opencv-python-headless` (4.14.0.94), lock con hashes.
2. `doclens/models/face_detection_yunet_2023mar.onnx`: sha256 verificado contra el oid de LFS de opencv_zoo.
3. `doclens/doclens.py` 2.3.0:
   - `faces_available()`, `detect_faces()` (imagen completa más 9 recortes ampliados) y `write_faces()`;
   - sección "Caras" en imágenes sueltas y en figuras de PDF;
   - formato 5, y `-sin-caras` cuando no se pudo buscar.
4. `doclens/sync.sh`: `deps_ok` con `cv2` y sello `.lock-sha256`.
5. `tests/harness/doclens-tests.sh`:
   - 4 pruebas de caras;
   - la prueba de figuras duplicadas ya no cuenta `.faces.tsv`.

## Verificación

- La suite pasa 185 de 185 y `ruff` queda limpio.
- Cuadros reales de ai_by_design: un cuadro de Teams (9 caras), un chat de Teams (9, incluidos avatares) y un cuadro
  de reunión (4). En una foto de pizarrón no hay caras y no encontró ninguna.
