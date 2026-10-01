import uuid
from datetime import datetime
from pathlib import Path

from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

from embeddings import embed
from store import client

DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"
COLLECTION = "engineering_docs"
BATCH = 16


def rel_path(path: Path) -> str:
    return path.relative_to(DOCS_DIR).as_posix()


def replace_chunks(path: Path, chunks):
    """Delete the file's old chunks, then embed and store the new ones.
    chunk = {content, heading, page?, source_type?}"""
    rel = rel_path(path)
    client.delete(
        collection_name=COLLECTION,
        points_selector=Filter(must=[FieldCondition(key="path", match=MatchValue(value=rel))]),
    )
    if not chunks:
        return 0
    modified = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        vecs = embed([f"{rel}\n{c['heading']}\n{c['content']}"[:3500] for c in batch])
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{rel}#{i + j}")),
                vector=v,
                payload={
                    "source_type": c.get("source_type", "text"),
                    "path": rel,
                    "heading": c["heading"],
                    "content": c["content"],
                    "page": c.get("page"),
                    "chunk_index": i + j,
                    "last_modified": modified,
                },
            )
            for j, (c, v) in enumerate(zip(batch, vecs))
        ]
        client.upsert(collection_name=COLLECTION, points=points)
    return len(chunks)