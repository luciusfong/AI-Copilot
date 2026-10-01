from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from chunking import chunk_markdown
from docs_common import replace_chunks
from vision import describe, pix_to_png
import pymupdf

MIN_BYTES = 10_000
MAX_IMAGES = 30


def _blocks(doc):
    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, doc)
        elif child.tag.endswith("}tbl"):
            yield Table(child, doc)


def docx_to_markdown(doc):
    lines = []
    for b in _blocks(doc):
        if isinstance(b, Paragraph):
            text = b.text.strip()
            if not text:
                continue
            style = (b.style.name if b.style is not None else "") or ""
            if style == "Title":
                lines.append(f"# {text}")
            elif style.startswith("Heading"):
                digits = "".join(ch for ch in style if ch.isdigit())
                lines.append(f"{'#' * min(int(digits or 1), 6)} {text}")
            elif "List" in style:
                lines.append(f"- {text}")
            else:
                lines.append(text)
            lines.append("")
        else:   # table: one line per row
            lines.append("")
            for row in b.rows:
                cells, prev = [], None
                for c in row.cells:
                    t = c.text.strip().replace("\n", " ")
                    if t != prev:       # merged cells repeat, so drop the repeats
                        cells.append(t)
                    prev = t
                lines.append(" | ".join(cells))
            lines.append("")
    return "\n".join(lines)


def docx_chunks(path: Path, vision=False):
    doc = Document(str(path))
    chunks = [dict(c, source_type="docx") for c in chunk_markdown(docx_to_markdown(doc))]

    if vision:
        n = 0
        for rel in doc.part.rels.values():
            if "image" not in rel.reltype or n >= MAX_IMAGES:
                continue
            part = rel.target_part
            if part.content_type not in ("image/png", "image/jpeg") or len(part.blob) < MIN_BYTES:
                continue
            try:
                text = describe(pix_to_png(pymupdf.Pixmap(part.blob)))
            except Exception as e:
                print(f"    vision failed on an image: {e}")
                continue
            if text:
                chunks.append({"heading": "Embedded image", "content": text, "source_type": "docx_image"})
                n += 1
    return chunks


def ingest_file(path: Path, vision=False):
    return replace_chunks(path, docx_chunks(path, vision))