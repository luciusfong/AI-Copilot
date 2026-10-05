import argparse
import os
from pathlib import Path

from store import COLLECTIONS, client, ensure_collections

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.getenv("STATE_DIR", ROOT / "state"))


def main():
    ap = argparse.ArgumentParser(description="Clear the Qdrant index and the sync manifest. "
                                             "Source files in data/ are never touched.")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--vision-cache", action="store_true",
                    help="also delete cached image descriptions (they are slow to regenerate)")
    a = ap.parse_args()

    print("This will delete ALL points in:", ", ".join(COLLECTIONS))
    print("and reset the sync manifest. Files in data/ are NOT touched.")
    if not a.yes and input("Type 'reset' to continue: ").strip().lower() != "reset":
        print("cancelled")
        return

    for name in COLLECTIONS:
        if client.collection_exists(name):
            client.delete_collection(name)
            print(f"deleted collection: {name}")
    ensure_collections()                       # recreate them empty

    for base in {STATE_DIR, ROOT}:             # covers both old and new state locations
        for fname in ("index_state.db", "index_state.db-journal", "sync.lock"):
            f = base / fname
            if f.exists():
                f.unlink()
                print(f"removed {f}")
        if a.vision_cache:
            cache = base / "vision_cache"
            if cache.exists():
                n = 0
                for f in cache.glob("*.txt"):
                    f.unlink()
                    n += 1
                print(f"removed {n} cached image descriptions")

    print("done. Re-index with: python sync.py")


if __name__ == "__main__":
    main()