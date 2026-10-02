import base64
import hashlib
from pathlib import Path
import os
import pymupdf
import requests

from embeddings import OLLAMA_URL

VISION_MODEL = "qwen2.5vl:3b"
CACHE = Path(os.getenv("STATE_DIR", Path(__file__).resolve().parent.parent / "state")) / "vision_cache"
CACHE.mkdir(parents=True, exist_ok=True)

PROMPT = (
    "This image comes from an engineering document. Transcribe all readable text, labels, "
    "part numbers and values. If it is a diagram, flowchart, drawing or schematic, explain "
    "what the components are and how they connect. If it is a screenshot, describe the "
    "screen and its fields. Be factual and concise; do not guess."
)


def pix_to_png(pix):
    """Normalize colorspace and shrink big images so the vision model stays fast."""
    if pix.colorspace is None or pix.colorspace.n > 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    while max(pix.width, pix.height) > 1600:
        pix.shrink(1)
    return pix.tobytes("png")


def describe(png: bytes) -> str:
    key = hashlib.sha1(png).hexdigest()
    cached = CACHE / f"{key}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": PROMPT,
                          "images": [base64.b64encode(png).decode()]}],
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 4096},
        },
        timeout=900,
    )
    r.raise_for_status()
    text = r.json()["message"]["content"].strip()
    cached.write_text(text, encoding="utf-8")
    return text