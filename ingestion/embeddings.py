import requests
import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"
VECTOR_SIZE = 768


def embed(texts, prefix="search_document: "):
    """Embed a list of strings. nomic-embed-text expects a task prefix."""
    r = requests.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": [prefix + t for t in texts]},
        timeout=300,
    )
    r.raise_for_status()
    return r.json()["embeddings"]


def embed_query(text):
    return embed([text], prefix="search_query: ")[0]