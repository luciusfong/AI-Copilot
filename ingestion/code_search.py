import sys

from qdrant_client.models import FieldCondition, Filter, MatchValue

from embeddings import embed_query
from store import client

COL = "engineering_code"


def eq(key, val):
    return FieldCondition(key=key, match=MatchValue(value=val))


def scroll(flt, limit=200):
    pts, _ = client.scroll(collection_name=COL, scroll_filter=flt, limit=limit, with_payload=True)
    return sorted((p.payload for p in pts), key=lambda p: (p["path"], p["start_line"]))


def show(p, extra=""):
    print(f"{p['symbol_type']:<11} {p['class']}.{p['method'] or ''}  ({p['path']}:{p['start_line']}) {extra}")


cmd, arg = (sys.argv[1] if len(sys.argv) > 1 else ""), " ".join(sys.argv[2:])

if cmd == "usages":      # who calls a method name?
    for p in scroll(Filter(must=[eq("called_names", arg)])):
        show(p, "calls: " + ", ".join(c for c in p["calls"] if arg in c)[:100])
elif cmd == "uses":      # which classes depend on / create / inherit a type?
    for p in scroll(Filter(should=[eq("dependencies", arg), eq("created_types", arg), eq("base_types", arg)])):
        show(p)
elif cmd == "class":     # a class and all its members
    for p in scroll(Filter(must=[eq("class", arg)])):
        show(p, "| " + p["signature"][:90])
elif cmd == "search":    # semantic search
    res = client.query_points(collection_name=COL, query=embed_query(arg), limit=6, with_payload=True)
    for r in res.points:
        show(r.payload, f"score={r.score:.3f}")
else:
    print("usage: code_search.py [usages|uses|class|search] <text>")