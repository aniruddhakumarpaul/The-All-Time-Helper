"""
One-time ChromaDB deduplication script.
Scans the neural_memory collection for entries with identical content (first 200 chars)
and removes the older duplicate, keeping the most recent version.

Usage: python scripts/deduplicate_memory.py
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import chromadb

MEMORY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".project_brain")

def deduplicate():
    print(f"[Dedup] Opening ChromaDB at: {MEMORY_PATH}")
    client = chromadb.PersistentClient(path=MEMORY_PATH)
    collection = client.get_or_create_collection("neural_memory")
    
    total_before = collection.count()
    print(f"[Dedup] Total entries before: {total_before}")
    
    if total_before == 0:
        print("[Dedup] Nothing to deduplicate.")
        return

    # Fetch all entries
    all_data = collection.get(limit=total_before)
    
    seen_hashes = {}  # hash(first 200 chars) -> (id, timestamp)
    ids_to_delete = []
    
    for i, doc_id in enumerate(all_data["ids"]):
        doc = (all_data["documents"][i] or "")[:200]
        meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
        ts = meta.get("timestamp", 0)
        doc_hash = hash(doc)
        
        if doc_hash in seen_hashes:
            existing_id, existing_ts = seen_hashes[doc_hash]
            if ts > existing_ts:
                # Current is newer, delete the old one
                ids_to_delete.append(existing_id)
                seen_hashes[doc_hash] = (doc_id, ts)
                print(f"  DUPE (keeping newer): {doc_id[:60]} replaces {existing_id[:60]}")
            else:
                # Current is older, delete it
                ids_to_delete.append(doc_id)
                print(f"  DUPE (keeping older): {existing_id[:60]} kept, removing {doc_id[:60]}")
        else:
            seen_hashes[doc_hash] = (doc_id, ts)
    
    if not ids_to_delete:
        print("[Dedup] No duplicates found. Memory is clean.")
        return
    
    print(f"\n[Dedup] Found {len(ids_to_delete)} duplicates to remove.")
    
    # Delete in batches
    batch_size = 100
    for batch_start in range(0, len(ids_to_delete), batch_size):
        batch = ids_to_delete[batch_start:batch_start + batch_size]
        collection.delete(ids=batch)
        print(f"  Deleted batch {batch_start // batch_size + 1}: {len(batch)} entries")
    
    total_after = collection.count()
    print(f"\n[Dedup] Done. Entries: {total_before} -> {total_after} (removed {total_before - total_after})")


if __name__ == "__main__":
    deduplicate()
