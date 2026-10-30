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

Idempotente: si el sidecar corresponde al MISMO contenido (sha256 en .source),
no reprocesa (imprime la ruta y sale). No usa fechas: un PDF reemplazado con
fecha vieja se reprocesa.

Origen: ai-tooling/doclens (toolbox compartido). Los proyectos consumen una
espejo de doclens/ (misma estructura, ADR-0076) vía doclens/sync.sh — no editar la copia local.
"""

DOCLENS_VERSION = "1.2.0"
SIDECAR_FORMAT_VERSION = 1

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
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
    if head.startswith((b"\xff\xd8\xff", b"\x89PNG")):
        return "image"
    # fallback por extensión
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        return "image"
    return "unknown"


def sidecar_dir(path: Path) -> Path:
    """<archivo>.inspect/ junto al documento; si esa carpeta no admite escritura
    (solo lectura, montaje ajeno), un directorio temporal estable por documento."""
    beside = path.parent / f"{path.name}.inspect"
    if beside.is_dir() or os.access(path.parent, os.W_OK):
        return beside
    key = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "doclens" / key / f"{path.name}.inspect"


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}


class PasswordProtectedError(Exception):
    """PDF con contraseña de apertura: no se puede leer sin ella."""


def fingerprint(path: Path) -> str:
    """sha256 del contenido. El caché NO usa fechas: un PDF reemplazado con fecha vieja
    (cp -p, unzip, rsync -a, Finder) debe reprocesarse."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reset_sidecar(outdir: Path) -> None:
    """Quita lo generado por un procesamiento anterior (páginas o figuras que ya no
    existen en el documento nuevo no deben quedar como si fueran actuales)."""
    for name in ("pages", "figures"):
        shutil.rmtree(outdir / name, ignore_errors=True)
    for name in ("index.md", "downscaled.png", "as-image.png", ".source"):
        (outdir / name).unlink(missing_ok=True)


def write_index(outdir: Path, md: list[str]) -> None:
    """Escritura atómica: quien lea index.md nunca ve uno a medio escribir."""
    tmp = outdir / ".index.md.tmp"
    tmp.write_text("\n".join(md))
    os.replace(tmp, outdir / "index.md")


def ignore_sidecar(outdir: Path) -> None:
    """El sidecar se ignora a sí mismo (como .ruff_cache): analizar documentos en
    cualquier repo no ensucia git ni obliga a tocar su .gitignore raíz."""
    gitignore = outdir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# generado por doclens: sidecar local, no versionar\n*\n")


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(img_path: Path, lang: str = "spa") -> str:
    try:
        out = subprocess.run(
            ["tesseract", str(img_path), "stdout", "-l", lang],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return out.stdout.strip()
    except Exception as e:  # noqa: BLE001
        log(f"OCR falló en {img_path.name}: {e}")
        return ""


def process_pdf(path: Path, outdir: Path, dpi: int) -> None:
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    if doc.needs_pass:
        doc.close()
        raise PasswordProtectedError(path.name)
    pages_dir = outdir / "pages"
    figs_dir = outdir / "figures"
    pages_dir.mkdir(parents=True, exist_ok=True)

    md = []
    md.append(f"# doclens: {path.name}\n")
    md.append("## Metadata\n")
    md.append(f"- archivo: `{path}`")
    md.append(f"- tamaño: {path.stat().st_size / 1024:.1f} KB")
    md.append(f"- páginas: {doc.page_count}")
    md.append(f"- encriptado: {(doc.metadata or {}).get('encryption') or 'no'}")
    md.append(f"- metadata PDF: {(doc.metadata or {}).get('producer') or 'n/a'}\n")
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

    write_index(outdir, md)
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

    if path.suffix.lower() not in IMAGE_SUFFIXES:
        # p. ej. un PNG con extensión .pdf: el agente no puede "leerla directo" (el hook
        # bloquea por extensión) → copia con la extensión correcta
        copy = outdir / "as-image.png"
        im2 = im.convert("RGB")
        im2.thumbnail((2048, 2048))
        im2.save(copy)
        md.append(f"⚠️ el archivo es una imagen {im.format} con extensión `{path.suffix}`: lee `{copy.name}`.\n")
    elif size_mb > 8 or max(im.size) > 4096:
        small = outdir / "downscaled.png"
        im2 = im.convert("RGB")
        im2.thumbnail((2048, 2048))
        im2.save(small)
        md.append(f"⚠️ imagen grande — usar `{small.name}` para el LLM (downscaled a {im2.size}).\n")
    else:
        md.append("La imagen es apta para lectura directa por el modelo de visión.")
        md.append("Para análisis visual léela directo; este sidecar solo aporta metadata.\n")

    write_index(outdir, md)


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

    outdir = sidecar_dir(path)
    index = outdir / "index.md"
    source = outdir / ".source"
    digest = fingerprint(path)
    if index.exists() and source.exists() and source.read_text().strip() == digest:
        ignore_sidecar(outdir)  # sidecars de versiones previas a 1.1.0
        print(f"INSPECT_DIR={outdir}")  # cache hit: mismo contenido
        return 0

    kind = detect_kind(path)
    log(f"{path.name}: tipo real = {kind}")
    outdir.mkdir(parents=True, exist_ok=True)
    ignore_sidecar(outdir)
    reset_sidecar(outdir)

    try:
        if kind == "pdf":
            process_pdf(path, outdir, dpi)
        elif kind == "image":
            process_image(path, outdir)
        else:
            log("tipo no soportado")
            return 2
    except PasswordProtectedError:
        log("PDF protegido con contraseña de apertura: no se puede leer sin ella")
        return 4
    # .source al final: si el proceso se interrumpe, el próximo Read reprocesa
    source.write_text(digest + "\n")

    print(f"INSPECT_DIR={outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
