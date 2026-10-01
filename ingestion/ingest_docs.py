import sys
import time
from pathlib import Path

import docx_ingest
import pdf_ingest
import text_ingest
from docs_common import DOCS_DIR
from store import ensure_collections

HANDLERS = {
    ".md": lambda p, v: text_ingest.ingest_file(p),
    ".txt": lambda p, v: text_ingest.ingest_file(p),
    ".pdf": pdf_ingest.ingest_file,
    ".docx": docx_ingest.ingest_file,
}

if __name__ == "__main__":
    args = sys.argv[1:]
    vision = "--vision" in args
    subs = [a for a in args if not a.startswith("--")]     # optional subfolder(s) under data/docs
    roots = [DOCS_DIR / s for s in subs] or [DOCS_DIR]

    ensure_collections()
    total = 0
    for root in roots:
        for p in sorted(root.rglob("*")):
            h = HANDLERS.get(p.suffix.lower())
            if not h or p.name.startswith("~$"):
                continue
            t = time.time()
            try:
                n = h(p, vision)
            except Exception as e:
                print(f"FAILED {p.relative_to(DOCS_DIR)}: {e}")
                continue
            total += n
            print(f"{p.relative_to(DOCS_DIR)}: {n} chunks ({time.time() - t:.0f}s)")
    print(f"done, {total} chunks (vision={'on' if vision else 'off'})")