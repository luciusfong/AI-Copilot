import argparse
import hashlib
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

from qdrant_client.models import FieldCondition, Filter, MatchValue, PayloadSchemaType, PointStruct

import repo_ingest
from csharp_parser import parse_source
from docs_common import DOCS_DIR
from embeddings import embed
from ingest_docs import HANDLERS
from repo_ingest import BATCH, REPOS_DIR, embed_text, iter_cs
from store import client, ensure_collections

# Bump when chunking, parsing or the embedding model changes: the next sync re-indexes everything.
PIPELINE_VERSION = 1
VISION_TYPES = (".pdf", ".docx")
ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.getenv("STATE_DIR", ROOT / "state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = STATE_DIR / "index_state.db"
LOCK = STATE_DIR / "sync.lock"

# ---------- manifest ----------
def open_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""CREATE TABLE IF NOT EXISTS files (
        key TEXT PRIMARY KEY, sha1 TEXT, size INTEGER, mtime REAL,
        chunks INTEGER, vision INTEGER, version INTEGER, indexed_at TEXT)""")
    return db


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def record(db, key, path, chunks, vision):
    st = path.stat()
    db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?)",
               (key, sha1_of(path), st.st_size, st.st_mtime, chunks, int(vision),
                PIPELINE_VERSION, datetime.now().isoformat(timespec="seconds")))
    db.commit()


# ---------- scanning ----------
def scan_docs():
    for p in sorted(DOCS_DIR.rglob("*")):
        if p.is_file() and p.suffix.lower() in HANDLERS and not p.name.startswith("~$"):
            yield f"docs:{p.relative_to(DOCS_DIR).as_posix()}", p


def scan_code():
    for repo_dir in sorted(d for d in REPOS_DIR.iterdir() if d.is_dir()):
        for f in iter_cs(repo_dir):
            yield f"code:{repo_dir.name}/{f.relative_to(repo_dir).as_posix()}", f


def build_plan(db, items, prefixes, vision, full):
    rows = {r[0]: r for r in db.execute(
        "SELECT key, sha1, size, mtime, vision, version FROM files")}
    todo, touched, seen = [], [], set()
    for key, path in items:
        seen.add(key)
        st = path.stat()
        row = rows.get(key)
        reason = None
        if full:
            reason = "full"
        elif row is None:
            reason = "new"
        elif row[5] != PIPELINE_VERSION:
            reason = "pipeline"
        elif vision and not row[4] and path.suffix.lower() in VISION_TYPES:
            reason = "vision"
        elif (row[2], row[3]) != (st.st_size, st.st_mtime):
            if sha1_of(path) != row[1]:
                reason = "changed"
            else:
                touched.append((key, path))   # timestamp changed, content identical
        if reason:
            todo.append((key, path, reason))
    gone = [k for k in rows if k.startswith(prefixes) and k not in seen]
    return todo, touched, gone


# ---------- index operations ----------
def eq(key, val):
    return FieldCondition(key=key, match=MatchValue(value=val))


def delete_points(key):
    kind, rest = key.split(":", 1)
    if kind == "docs":
        client.delete(collection_name="engineering_docs",
                      points_selector=Filter(must=[eq("path", rest)]))
    else:
        repo, rel = rest.split("/", 1)
        client.delete(collection_name="engineering_code",
                      points_selector=Filter(must=[eq("repository", repo), eq("path", rel)]))


def index_code_file(key, f):
    repo, rel = key[len("code:"):].split("/", 1)
    modified = datetime.fromtimestamp(f.stat().st_mtime).isoformat()
    records = parse_source(f.read_bytes())      # parse first: a parse error leaves the old index intact
    for r in records:
        r.update(repository=repo, path=rel, last_modified=modified)
    delete_points(key)
    for i in range(0, len(records), BATCH):
        batch = records[i:i + BATCH]
        vecs = embed([embed_text(r) for r in batch])
        client.upsert(collection_name="engineering_code", points=[
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                                  f"{r['repository']}/{r['path']}#{r['symbol_type']}#"
                                  f"{r['class']}#{r['signature']}#{r['start_line']}")),
                vector=v, payload=r)
            for r, v in zip(batch, vecs)])
    return len(records)


def ensure_indexes():
    for col, fields in (("engineering_docs", ["path"]),
                        ("engineering_code", repo_ingest.INDEXED_FIELDS + ["path"])):
        for f in fields:
            try:
                client.create_payload_index(col, f, PayloadSchemaType.KEYWORD)
            except Exception:
                pass


# ---------- run ----------
def acquire_lock():
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        sys.exit(f"Another sync is running. If a crashed run left {LOCK.name}, delete it.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scope", nargs="?", default="all", choices=["all", "docs", "code"])
    ap.add_argument("--vision", action="store_true", help="describe images in PDF/DOCX")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    ap.add_argument("--full", action="store_true", help="re-index everything in scope")
    ap.add_argument("--adopt", action="store_true",
                    help="record current files as already indexed, without re-embedding")
    a = ap.parse_args()

    items, prefixes = [], []
    if a.scope in ("all", "docs"):
        items += list(scan_docs())
        prefixes.append("docs:")
    if a.scope in ("all", "code"):
        items += list(scan_code())
        prefixes.append("code:")
    prefixes = tuple(prefixes)

    acquire_lock()
    try:
        ensure_collections()
        ensure_indexes()
        db = open_db()

        if a.adopt:
            for key, path in items:
                record(db, key, path, 0, False)
            print(f"adopted {len(items)} files as up to date")
            return

        todo, touched, gone = build_plan(db, items, prefixes, a.vision, a.full)
        print(f"{len(items)} files scanned: {len(todo)} to index, {len(gone)} removed, "
              f"{len(items) - len(todo) - len(touched)} unchanged")
        if a.dry_run:
            for key, _, why in todo:
                print(f"  [{why}] {key}")
            for key in gone:
                print(f"  [removed] {key}")
            return

        for key, path in touched:
            st = path.stat()
            db.execute("UPDATE files SET size=?, mtime=? WHERE key=?", (st.st_size, st.st_mtime, key))
        for key in gone:
            delete_points(key)
            db.execute("DELETE FROM files WHERE key=?", (key,))
            print(f"removed {key}")
        db.commit()

        failed = 0
        for n, (key, path, why) in enumerate(todo, 1):
            try:
                if key.startswith("docs:"):
                    chunks = HANDLERS[path.suffix.lower()](path, a.vision)
                else:
                    chunks = index_code_file(key, path)
                record(db, key, path, chunks, a.vision)
                print(f"[{n}/{len(todo)}] {why}: {key} -> {chunks}")
            except Exception as e:      # not recorded, so the next run retries it
                failed += 1
                print(f"FAILED {key}: {e}")
        print(f"done. {len(todo) - failed} indexed, {failed} failed")
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    main()