import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from qdrant_client.models import FieldCondition, Filter, MatchValue, PayloadSchemaType, PointStruct

from csharp_parser import parse_source
from embeddings import embed
from store import client, ensure_collections

REPOS_DIR = Path(__file__).resolve().parent.parent / "data" / "repos"
COLLECTION = "engineering_code"
BATCH = 16

SKIP_DIRS = {".git", ".vs", ".claude", ".idea", "bin", "obj", "packages", "dll",
             "node_modules", "TestResults"}
SKIP_SUFFIXES = (".g.cs", ".g.i.cs", ".Designer.cs", "AssemblyInfo.cs")
INDEXED_FIELDS = ["repository", "class", "method", "symbol_type",
                  "called_names", "created_types", "dependencies", "base_types"]


def iter_cs(root):
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".cs") and not f.endswith(SKIP_SUFFIXES):
                yield Path(dirpath) / f


def embed_text(r):
    name = ".".join(x for x in (r["namespace"], r["class"], r["method"]) if x)
    parts = [f"{r['symbol_type']} {name}", f"file: {r['repository']}/{r['path']}",
             r["doc"], r["content"]]
    return "\n".join(p for p in parts if p)[:4000]   # keep within the embedding context


def ensure_indexes():
    for f in INDEXED_FIELDS:
        try:
            client.create_payload_index(COLLECTION, f, PayloadSchemaType.KEYWORD)
        except Exception:
            pass


def ingest_repo(repo_dir: Path):
    repo = repo_dir.name
    client.delete(
        collection_name=COLLECTION,
        points_selector=Filter(must=[FieldCondition(key="repository", match=MatchValue(value=repo))]),
    )

    records, files = [], list(iter_cs(repo_dir))
    for f in files:
        rel = f.relative_to(repo_dir).as_posix()
        try:
            syms = parse_source(f.read_bytes())
        except Exception as e:
            print(f"  parse failed: {rel}: {e}")
            continue
        mod = datetime.fromtimestamp(f.stat().st_mtime).isoformat()
        for s in syms:
            s.update(repository=repo, path=rel, last_modified=mod)
            records.append(s)
    print(f"{repo}: {len(files)} .cs files -> {len(records)} symbols")

    for i in range(0, len(records), BATCH):
        batch = records[i:i + BATCH]
        vecs = embed([embed_text(r) for r in batch])
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                                  f"{r['repository']}/{r['path']}#{r['symbol_type']}#"
                                  f"{r['class']}#{r['signature']}#{r['start_line']}")),
                vector=v, payload=r)
            for r, v in zip(batch, vecs)
        ]
        client.upsert(collection_name=COLLECTION, points=points)
        print(f"  embedded {min(i + BATCH, len(records))}/{len(records)}", end="\r")
    print()


if __name__ == "__main__":
    ensure_collections()
    ensure_indexes()
    targets = [REPOS_DIR / a for a in sys.argv[1:]] or [d for d in REPOS_DIR.iterdir() if d.is_dir()]
    for t in targets:
        ingest_repo(t)