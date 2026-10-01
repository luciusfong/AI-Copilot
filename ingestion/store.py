from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from embeddings import VECTOR_SIZE

client = QdrantClient(host="localhost", port=6333)

COLLECTIONS = ("engineering_code", "engineering_docs")


def ensure_collections():
    for name in COLLECTIONS:
        if not client.collection_exists(name):
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            print(f"created collection: {name}")


if __name__ == "__main__":
    ensure_collections()
    print([c.name for c in client.get_collections().collections])