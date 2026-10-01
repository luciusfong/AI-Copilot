import uuid
from datetime import datetime
from pathlib import Path

from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

from chunking import chunk_markdown
from embeddings import embed
from store import client, ensure_collections

DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"
COLLECTION = "engineering_docs"
BATCH = 16


def ingest_file(path: Path):
    rel = path.relative_to(DOCS_DIR).as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    chunks = chunk_markdown(text)
    if not chunks:
        return 0

    # Re-ingesting a changed file: drop its old chunks first
    client.delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="path", match=MatchValue(value=rel))]
        ),
    )

    modified = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    kind = "markdown" if path.suffix.lower() == ".md" else "text"

    for i in range(0, len(chunks), BATCH):
        batch = chunks[i : i + BATCH]
        vectors = embed([f"{rel}\n{c['heading']}\n{c['content']}" for c in batch])
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{rel}#{i + j}")),
                vector=vec,
                payload={
                    "source_type": kind,
                    "path": rel,
                    "heading": c["heading"],
                    "content": c["content"],
                    "chunk_index": i + j,
                    "last_modified": modified,
                },
            )
            for j, (c, vec) in enumerate(zip(batch, vectors))
        ]
        client.upsert(collection_name=COLLECTION, points=points)
    return len(chunks)


if __name__ == "__main__":
    ensure_collections()
    total = 0
    for p in sorted(DOCS_DIR.rglob("*")):
        if p.suffix.lower() in (".md", ".txt"):
            n = ingest_file(p)
            total += n
            print(f"{p.relative_to(DOCS_DIR)}: {n} chunks")
    print(f"done, {total} chunks")