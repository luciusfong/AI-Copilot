import sys

from embeddings import embed_query
from store import client

query = " ".join(sys.argv[1:]) or "how is configuration loaded"
res = client.query_points(
    collection_name="engineering_docs",
    query=embed_query(query),
    limit=5,
)
for p in res.points:
    pl = p.payload
    print(f"\n{p.score:.3f}  {pl['path']}  [{pl['heading']}]")
    print(pl["content"][:200].replace("\n", " "))