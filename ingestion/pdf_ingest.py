from pathlib import Path

import pymupdf

from chunking import split_long
from docs_common import replace_chunks
from vision import describe, pix_to_png

MIN_PX = 150          # ignore icons and logos
MAX_IMAGES = 40       # per PDF, keeps a vision run bounded
SCANNED_CHARS = 50    # pages with less text than this are treated as scans
DRAWING_OPS = 40      # pages with this many vector shapes may be drawings


def _vision_chunk(png, pno, label, kind):
    text = describe(png)
    if not text:
        return None
    return {"heading": f"Page {pno} - {label}", "content": text, "page": pno, "source_type": kind}


def pdf_chunks(path: Path, vision=False):
    doc = pymupdf.open(path)
    chunks, seen, n_img = [], set(), 0

    for pno, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        for part in split_long(text, 1500) if text else []:
            chunks.append({"heading": f"Page {pno}", "content": part, "page": pno, "source_type": "pdf"})

        if not vision:
            continue
        got_image = False
        try:
            if len(text) < SCANNED_CHARS:   # scanned page: read the whole page
                png = pix_to_png(page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)))
                c = _vision_chunk(png, pno, "scanned page", "pdf_scan")
                if c:
                    chunks.append(c)
                continue
            for img in page.get_images(full=True):
                xref = img[0]
                if xref in seen or n_img >= MAX_IMAGES:
                    continue
                seen.add(xref)
                pix = pymupdf.Pixmap(doc, xref)
                if pix.width < MIN_PX or pix.height < MIN_PX:
                    continue
                c = _vision_chunk(pix_to_png(pix), pno, "image", "pdf_image")
                if c:
                    chunks.append(c)
                    n_img += 1
                    got_image = True
            # vector diagrams are not embedded images: render the page instead
            if not got_image and n_img < MAX_IMAGES and len(page.get_drawings()) > DRAWING_OPS:
                png = pix_to_png(page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)))
                c = _vision_chunk(png, pno, "diagram", "pdf_drawing")
                if c:
                    chunks.append(c)
                    n_img += 1
        except Exception as e:
            print(f"    vision failed on page {pno}: {e}")
    return chunks


def ingest_file(path: Path, vision=False):
    return replace_chunks(path, pdf_chunks(path, vision))