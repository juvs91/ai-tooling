#!/usr/bin/env python3
"""doclens — pre-procesa documentos (PDF/imágenes) para sesiones agénticas.

Filosofía: NUNCA mandar el documento crudo al LLM. Se procesa primero y se
genera un directorio sidecar `<archivo>.inspect/` con artifacts estructurados:

    index.md          metadata + inventario de páginas + texto (capa de texto
                      u OCR spa si el PDF es scan) + tablas (markdown) +
                      topología (bloques con coordenadas, dibujos vectoriales)
    pages/page-N.png  render de cada página (para lectura visual del LLM)
    figures/          imágenes/figuras embebidas extraídas + su OCR (si hay
                      tesseract) -> texto DENTRO de figuras

El LLM lee index.md y los PNG — nunca el PDF original (el backend Kimi lo
rechaza con HTTP 400 y envenena la sesión).

Uso:
    python doclens.py <archivo> [--dpi 150]

Idempotente: si el sidecar existe y es más reciente que el archivo, no
reprocesa (imprime la ruta y sale).

Origen: ai-tooling/doclens (toolbox compartido). Los proyectos consumen una
copia sincronizada via scripts/sync-doclens.sh — no editar la copia local.
"""

DOCLENS_VERSION = "1.0.0"
SIDECAR_FORMAT_VERSION = 1

import shutil
import subprocess
import sys
from pathlib import Path

MAX_TEXT_CHARS_PER_PAGE = 8000
MAX_TOPO_BLOCKS_PER_PAGE = 40
MIN_TEXT_FOR_SKIP_OCR = 20  # si la página tiene menos texto que esto -> es scan


def log(msg: str) -> None:
    print(f"[doclens] {msg}", file=sys.stderr)


def detect_kind(path: Path) -> str:
    """Detecta el tipo real por magic bytes, no por extensión."""
    head = path.read_bytes()[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG"):
        return "image"
    # fallback por extensión
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        return "image"
    return "unknown"


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(img_path: Path, lang: str = "spa") -> str:
    try:
        out = subprocess.run(
            ["tesseract", str(img_path), "stdout", "-l", lang],
            capture_output=True, text=True, timeout=60,
        )
        return out.stdout.strip()
    except Exception as e:  # noqa: BLE001
        log(f"OCR falló en {img_path.name}: {e}")
        return ""


def process_pdf(path: Path, outdir: Path, dpi: int) -> None:
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    pages_dir = outdir / "pages"
    figs_dir = outdir / "figures"
    pages_dir.mkdir(parents=True, exist_ok=True)

    md = []
    md.append(f"# doclens: {path.name}\n")
    md.append("## Metadata\n")
    md.append(f"- archivo: `{path}`")
    md.append(f"- tamaño: {path.stat().st_size / 1024:.1f} KB")
    md.append(f"- páginas: {doc.page_count}")
    md.append(f"- encriptado: {doc.is_encrypted}")
    md.append(f"- metadata PDF: {doc.metadata.get('producer') or 'n/a'}\n")
    md.append("## Contenido por página\n")

    zoom = dpi / 72
    any_fig = False

    for pno in range(doc.page_count):
        page = doc[pno]
        md.append(f"### Página {pno + 1}\n")
        md.append(f"- mediabox: {[round(v, 1) for v in page.rect]}")
        md.append(f"- render: `pages/page-{pno + 1}.png` ({dpi} dpi)")

        # render de página
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        pix.save(pages_dir / f"page-{pno + 1}.png")

        # capa de texto; si casi no hay -> scan -> OCR
        text = page.get_text("text").strip()
        source = "capa de texto"
        if len(text) < MIN_TEXT_FOR_SKIP_OCR and ocr_available():
            pix300 = page.get_pixmap(dpi=300)
            tmp = outdir / f"_ocr-page-{pno + 1}.png"
            pix300.save(tmp)
            text = ocr_image(tmp)
            tmp.unlink(missing_ok=True)
            source = "OCR (tesseract spa) — PDF sin capa de texto"
        md.append(f"- texto ({source}):\n")
        if text:
            md.append("```")
            md.append(text[:MAX_TEXT_CHARS_PER_PAGE])
            if len(text) > MAX_TEXT_CHARS_PER_PAGE:
                md.append(f"... [truncado: {len(text)} chars totales]")
            md.append("```\n")
        else:
            md.append("_(sin texto extraíble)_\n")

        # topología: bloques con bbox + dibujos vectoriales
        blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]
        drawings = page.get_drawings()
        md.append(
            f"- topología: {len(blocks)} bloques de texto, "
            f"{len(drawings)} elementos vectoriales (líneas/rectángulos/tablas dibujadas)"
        )
        for b in blocks[:MAX_TOPO_BLOCKS_PER_PAGE]:
            preview = " ".join(
                s["text"] for line in b.get("lines", []) for s in line.get("spans", [])
            )[:60]
            bbox = [round(v, 1) for v in b["bbox"]]
            md.append(f"  - `{bbox}` {preview}")
        if len(blocks) > MAX_TOPO_BLOCKS_PER_PAGE:
            md.append(f"  - ... +{len(blocks) - MAX_TOPO_BLOCKS_PER_PAGE} bloques más")
        md.append("")

        # tablas -> markdown
        try:
            tables = page.find_tables()
            if tables.tables:
                md.append(f"- tablas detectadas: {len(tables.tables)}")
                for ti, t in enumerate(tables.tables, 1):
                    md.append(f"\n**Tabla {ti}** `{[round(v, 1) for v in t.bbox]}`\n")
                    try:
                        md.append(t.to_markdown(clean=True))
                    except Exception:  # noqa: BLE001
                        md.append(str(t.extract()))
                md.append("")
        except Exception as e:  # noqa: BLE001
            log(f"find_tables p{pno + 1}: {e}")

        # figuras / imágenes embebidas -> extraer + OCR (texto en figuras)
        imgs = page.get_images(full=True)
        if imgs:
            md.append(f"- figuras embebidas: {len(imgs)}")
            figs_dir.mkdir(exist_ok=True)
            for n, img in enumerate(imgs, 1):
                xref = img[0]
                try:
                    extracted = doc.extract_image(xref)
                    ext = extracted["ext"]
                    fig_path = figs_dir / f"fig-p{pno + 1}-{n}.{ext}"
                    fig_path.write_bytes(extracted["image"])
                    rects = page.get_image_rects(xref)
                    bbox = [round(v, 1) for v in rects[0]] if rects else "n/a"
                    line = f"  - `figures/{fig_path.name}` bbox={bbox} {extracted['width']}x{extracted['height']}"
                    if ocr_available():
                        fig_text = ocr_image(fig_path)
                        if fig_text:
                            line += f"\n    - texto en figura (OCR): «{fig_text[:200]}»"
                    md.append(line)
                    any_fig = True
                except Exception as e:  # noqa: BLE001
                    log(f"figura xref={xref}: {e}")
            md.append("")

    if not any_fig:
        md.append("\n_(sin figuras embebidas — el contenido es texto/vectorial)_\n")

    md.append("\n---\n")
    md.append("**Cómo consumir esto:** lee este index.md para estructura/texto/")
    md.append("topología/tablas. Para revisión visual lee `pages/page-N.png`.")
    md.append("Para figuras sueltas lee los archivos en `figures/`.")
    md.append("**NO leas el PDF original.**")

    (outdir / "index.md").write_text("\n".join(md))
    doc.close()


def process_image(path: Path, outdir: Path) -> None:
    from PIL import Image

    outdir.mkdir(parents=True, exist_ok=True)
    im = Image.open(path)
    size_mb = path.stat().st_size / (1024 * 1024)

    md = [f"# doclens: {path.name}\n", "## Metadata\n"]
    md.append(f"- archivo: `{path}`")
    md.append(f"- formato real: {im.format} ({path.suffix} declarado)")
    md.append(f"- dimensiones: {im.size[0]}x{im.size[1]} px, modo {im.mode}")
    md.append(f"- tamaño: {size_mb:.2f} MB\n")

    if size_mb > 8 or max(im.size) > 4096:
        small = outdir / "downscaled.png"
        im2 = im.convert("RGB")
        im2.thumbnail((2048, 2048))
        im2.save(small)
        md.append(f"⚠️ imagen grande — usar `{small.name}` para el LLM (downscaled a {im2.size}).\n")
    else:
        md.append("La imagen es apta para lectura directa por el modelo de visión.")
        md.append("Para análisis visual léela directo; este sidecar solo aporta metadata.\n")

    (outdir / "index.md").write_text("\n".join(md))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = Path(sys.argv[1]).expanduser().resolve()
    dpi = 150
    if "--dpi" in sys.argv:
        dpi = int(sys.argv[sys.argv.index("--dpi") + 1])

    if not path.exists():
        log(f"no existe: {path}")
        return 1

    outdir = path.parent / f"{path.name}.inspect"
    if outdir.exists() and (outdir / "index.md").exists():
        if (outdir / "index.md").stat().st_mtime > path.stat().st_mtime:
            print(f"INSPECT_DIR={outdir}")  # cache hit
            return 0

    kind = detect_kind(path)
    log(f"{path.name}: tipo real = {kind}")
    outdir.mkdir(parents=True, exist_ok=True)

    if kind == "pdf":
        process_pdf(path, outdir, dpi)
    elif kind == "image":
        process_image(path, outdir)
    else:
        log("tipo no soportado")
        return 1

    print(f"INSPECT_DIR={outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
