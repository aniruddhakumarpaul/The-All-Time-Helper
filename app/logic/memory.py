import os
import sys
import time
import shutil
import threading
sys.dont_write_bytecode = True
import chromadb
from chromadb.config import Settings
from typing import Any, List, Dict, Optional
from contextvars import ContextVar
from dotenv import load_dotenv
from app.logger import logger

load_dotenv()

# Context variable to store the current user_id for multi-tenancy isolation
user_context: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
admin_auth_context: ContextVar[Optional[str]] = ContextVar("admin_key", default=None)
_memory_lock = threading.RLock()
_memory_unhealthy_reason: Optional[str] = None

# Define the persistent storage path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
PRIMARY_MEMORY_PATH = os.path.join(BASE_DIR, ".project_brain")
FALLBACK_MEMORY_PATH = os.path.join(BASE_DIR, ".project_brain_active")
MEMORY_PATH = os.getenv("PROJECT_BRAIN_PATH", PRIMARY_MEMORY_PATH)
if not os.path.isabs(MEMORY_PATH):
    MEMORY_PATH = os.path.join(BASE_DIR, MEMORY_PATH)

# Ensure the directory exists
if not os.path.exists(MEMORY_PATH):
    os.makedirs(MEMORY_PATH)

# Initialize ChromaDB Client (Persistent). Memory is useful, but the app should
# still boot if the local vector store is temporarily locked or corrupt.
def _init_chroma(path: str):
    os.makedirs(path, exist_ok=True)
    db_client = chromadb.PersistentClient(path=path)
    db_collection = db_client.get_or_create_collection(name="neural_memory")
    return db_client, db_collection

def _mark_memory_unhealthy(reason: str):
    """Disable Chroma-backed memory for this process without taking chat down."""
    global _memory_unhealthy_reason
    if not _memory_unhealthy_reason:
        _memory_unhealthy_reason = reason
        logger.error(f"[Memory] ChromaDB marked unhealthy for this process: {reason}")

def _is_memory_healthy() -> bool:
    return collection is not None and not _memory_unhealthy_reason

def _safe_collection_query(**kwargs) -> Optional[Dict[str, Any]]:
    if not _is_memory_healthy():
        return None
    try:
        with _memory_lock:
            return collection.query(**kwargs)
    except Exception as e:
        _mark_memory_unhealthy(str(e))
        logger.error(f"[Memory] Chroma query failed; returning empty memory context: {e}", exc_info=True)
        return None

try:
    client, collection = _init_chroma(MEMORY_PATH)
except Exception as e:
    if MEMORY_PATH == PRIMARY_MEMORY_PATH:
        logger.error(f"[Memory] Primary ChromaDB unavailable at {MEMORY_PATH}: {e}")
        try:
            MEMORY_PATH = FALLBACK_MEMORY_PATH
            client, collection = _init_chroma(MEMORY_PATH)
            logger.warning(f"[Memory] Using fallback ChromaDB path: {MEMORY_PATH}")
        except Exception as fallback_error:
            client = None
            collection = None
            _mark_memory_unhealthy(str(fallback_error))
            logger.error(f"[Memory] Fallback ChromaDB unavailable. Neural memory disabled for this process: {fallback_error}")
    else:
        client = None
        collection = None
        _mark_memory_unhealthy(str(e))
        logger.error(f"[Memory] ChromaDB unavailable at {MEMORY_PATH}. Neural memory disabled for this process: {e}")

def index_document(doc_id: str, content: str, metadata: Dict = None, user_id: str = None):
    """Adds or updates a document in the semantic memory with forced user isolation."""
    if not _is_memory_healthy():
        logger.warning("Attempted to index document while ChromaDB is unavailable. Skipping.")
        return False

    # Use provided user_id or fallback to context
    uid = user_id or user_context.get()
    if not uid:
        logger.warning("Attempted to index document without a User ID. Skipping.")
        return False

    metadata = metadata or {}
    metadata.update({
        "timestamp": time.time(),
        "user_id": uid
    })
    
    try:
        with _memory_lock:
            collection.upsert(
                ids=[doc_id],
                documents=[content],
                metadatas=[metadata]
            )
        return True
    except Exception as e:
        _mark_memory_unhealthy(str(e))
        logger.error(f"[Memory] Failed to index document {doc_id}: {e}", exc_info=True)
        return False

def is_memory_available() -> bool:
    return _is_memory_healthy()

def query_memory(query_text: str, n_results: int = 3, filter_dict: Dict = None, threshold: float = 0.95, user_id: str = None) -> List[Dict]:
    """
    Retrieves relevant snippets with STRICT user-level isolation and semantic thresholding.
    """
    if not _is_memory_healthy():
        logger.warning("Attempted to query memory while ChromaDB is unavailable. Returning empty.")
        return []

    uid = user_id or user_context.get()
    if not uid:
        logger.warning("Attempted to query memory without a User ID. Returning empty.")
        return []

    # OPTIMIZATION: Skip RAG for short/generic "small talk" to save latency
    stop_words = {"hi", "hello", "hey", "ok", "okay", "thanks", "thank you", "bye", "clear", "help"}
    clean_query = query_text.lower().strip().strip("?!.")
    if len(clean_query) < 5 or clean_query in stop_words:
        return []

    # ChromaDB 'where' filter implementation - ENFORCE USER ISOLATION
    where_filter = {"user_id": uid}
    if filter_dict:
        # Merge filters using $and operator if we have other filters
        if len(filter_dict) > 0:
            where_filter = {"$and": [{"user_id": uid}, filter_dict]}
    
    query_args = {
        "query_texts": [query_text],
        "n_results": n_results,
        "where": where_filter
    }

    results = _safe_collection_query(**query_args)
    if not results:
        return []
    
    # FLAW 5 FIX: PASS 2: High-Recall Tool-Rule Search
    # We always pull tool rules even with weak matches to ensure agent behavior is grounded.
    rule_filter = {"$and": [{"user_id": uid}, {"type": "tool_rule"}]}
    rule_results = _safe_collection_query(query_texts=[query_text], n_results=3, where=rule_filter) or {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    formatted_results = []
    
    # Add tool rules first (if they meet the wider 0.90 threshold)
    if rule_results['documents']:
        for i in range(len(rule_results['documents'][0])):
            distance = rule_results['distances'][0][i] if 'distances' in rule_results else 0
            if distance <= 0.90:  # Wider threshold for rules
                formatted_results.append({
                    "content": f"[SYSTEM_RULE] {rule_results['documents'][0][i]}",
                    "metadata": rule_results['metadatas'][0][i],
                    "distance": distance
                })

    # Add standard results (if they meet the semantic distance threshold — lower = more relevant)
    if results['documents']:
        for i in range(len(results['documents'][0])):
            distance = results['distances'][0][i] if 'distances' in results else 0
            content = results['documents'][0][i]
            # Deduplicate
            if any(r["content"].endswith(content) for r in formatted_results):
                continue
                
            if distance <= threshold:
                formatted_results.append({
                    "content": content,
                    "metadata": results['metadatas'][0][i],
                    "distance": distance
                })
    return formatted_results

def delete_memory(doc_id: str, user_id: str = None, clear: bool = False):
    """Prunes a specific memory entry by ID. Enforces ownership if user_id is in context."""
    if not _is_memory_healthy():
        logger.warning("Attempted to delete memory while ChromaDB is unavailable. Skipping.")
        return

    uid = user_id or user_context.get()
    try:
        # Clear existing if needed
        if clear:
            logger.info(f"[Memory] Clearing existing collection for User: {uid or 'Global'}")
            with _memory_lock:
                collection.delete(where={"user_id": uid} if uid else {})
        else:
            # If we have a user context, we only delete if the user owns it
            with _memory_lock:
                if uid:
                    collection.delete(ids=[doc_id], where={"user_id": uid})
                else:
                    collection.delete(ids=[doc_id])
            logger.debug(f"DEBUG: Memory {doc_id} successfully pruned.")
    except Exception as e:
        _mark_memory_unhealthy(str(e))
        logger.debug(f"DEBUG: Pruning failed: {e}")

def log_insight(insight_title: str, insight_body: str, metadata_ext: Dict = None, user_id: str = None):
    """Logs a project decision or architectural insight with categorization and user tagging."""
    uid = user_id or user_context.get()
    if not uid:
        print("WARNING: Attempted to log insight without a User ID. Skipping.")
        return

    doc_id = f"insight_{uid}_{insight_title.lower().replace(' ', '_')}_{int(time.time() * 1000)}"
    metadata = {"type": "insight", "title": insight_title, "category": "architecture"}
    if metadata_ext:
        metadata.update(metadata_ext)
    
    index_document(doc_id, insight_body, metadata, user_id=uid)
    print(f"DEBUG: Neural Memory updated with insight for user {uid}: {insight_title}")

# WARMUP: Trigger a background load of the embedding model to prevent first-query lag
def warmup_memory():
    if not _is_memory_healthy():
        return
    try:
        # A dummy query to force model loading in the background
        with _memory_lock:
            collection.peek(limit=1)
        print("DEBUG: Neural Memory (RAG) warmed up successfully.")
    except Exception as e:
        logger.warning(f"[Memory] Warmup skipped because ChromaDB is unavailable: {e}")

def repair_memory_store(preserve: bool = True) -> bool:
    """
    Rebuilds the active Chroma store into a fresh directory.

    Existing entries are exported first when possible. The old directory is
    archived on a best-effort basis; if Windows keeps it locked, the process
    switches to the repaired directory and leaves the old files untouched.
    """
    global client, collection, MEMORY_PATH, _memory_unhealthy_reason

    with _memory_lock:
        old_path = MEMORY_PATH
        stamp = time.strftime("%Y%m%d_%H%M%S")
        repair_path = f"{old_path}_repaired_{stamp}"
        archive_path = f"{old_path}_archived_{stamp}"
        snapshot = {"ids": [], "documents": [], "metadatas": []}

        if preserve and collection is not None:
            try:
                total = collection.count()
                if total:
                    snapshot = collection.get(limit=total, include=["documents", "metadatas"])
                    logger.info(f"[Memory] Exported {len(snapshot.get('ids', []))} entries before repair.")
            except Exception as e:
                logger.warning(f"[Memory] Could not export existing Chroma entries before repair: {e}")

        try:
            new_client, new_collection = _init_chroma(repair_path)
            ids = snapshot.get("ids") or []
            docs = snapshot.get("documents") or []
            metas = snapshot.get("metadatas") or []
            if ids and docs:
                batch_size = 100
                for start in range(0, len(ids), batch_size):
                    end = start + batch_size
                    new_collection.upsert(
                        ids=ids[start:end],
                        documents=docs[start:end],
                        metadatas=metas[start:end] if metas else None,
                    )

            archived = False
            if os.path.exists(old_path):
                try:
                    shutil.move(old_path, archive_path)
                    shutil.move(repair_path, old_path)
                    MEMORY_PATH = old_path
                    archived = True
                except Exception as archive_error:
                    logger.warning(f"[Memory] Could not archive old Chroma store; using repaired path for this process: {archive_error}")
                    MEMORY_PATH = repair_path
            else:
                MEMORY_PATH = repair_path

            if archived:
                new_client, new_collection = _init_chroma(MEMORY_PATH)

            client = new_client
            collection = new_collection
            _memory_unhealthy_reason = None
            logger.info(f"[Memory] Chroma repair complete. Active path: {MEMORY_PATH}")
            return True
        except Exception as e:
            _mark_memory_unhealthy(str(e))
            logger.error(f"[Memory] Chroma repair failed: {e}", exc_info=True)
            return False


def prune_stale_memories(days: int = 30, user_id: str = None):
    """
    Removes memories older than X days to maintain high retrieval performance.
    Skips entries with metadata 'permanent': True.
    FIX #7: Also enforces a hard cap on total collection size.
    """
    if not _is_memory_healthy():
        logger.warning("[Memory] Skipping prune because ChromaDB is unavailable.")
        return

    uid = user_id or user_context.get()
    MAX_MEMORY_ENTRIES = 10000  # Hard cap to prevent unbounded growth
    
    cutoff = time.time() - (days * 86400)
    try:
        # Phase 1: Prune stale entries
        if uid:
            where_filter = {"$and": [{"timestamp": {"$lt": cutoff}}, {"user_id": uid}]}
        else:
            where_filter = {"timestamp": {"$lt": cutoff}}
            
        with _memory_lock:
            stale = collection.get(
                where=where_filter
            )
        
        if stale and stale['ids']:
            ids_to_delete = []
            for i in range(len(stale['ids'])):
                metadata = stale['metadatas'][i] if stale['metadatas'] else {}
                if not metadata.get('permanent'):
                    ids_to_delete.append(stale['ids'][i])
            
            if ids_to_delete:
                with _memory_lock:
                    collection.delete(ids=ids_to_delete)
                logger.info(f"[Memory] Pruned {len(ids_to_delete)} stale memory entries.")
        
        # Phase 2: Enforce hard cap (remove oldest if over limit)
        with _memory_lock:
            total_count = collection.count()
        if total_count > MAX_MEMORY_ENTRIES:
            overflow = total_count - MAX_MEMORY_ENTRIES
            with _memory_lock:
                oldest = collection.get(limit=overflow)
            if oldest and oldest['ids']:
                with _memory_lock:
                    collection.delete(ids=oldest['ids'])
                logger.info(f"[Memory] Pruned {len(oldest['ids'])} overflow entries (cap: {MAX_MEMORY_ENTRIES}).")

        # Phase 3: Deduplicate entries with identical content under different IDs
        # This prevents 'confused memory' where the same snippet appears multiple times in query results
        try:
            with _memory_lock:
                all_entries = collection.get(limit=total_count or collection.count())
            if all_entries and all_entries['ids']:
                seen_hashes = {}  # hash(first 200 chars) -> (id, timestamp)
                ids_to_delete = []
                for i, doc_id in enumerate(all_entries['ids']):
                    doc = (all_entries['documents'][i] or "")[:200]
                    meta = all_entries['metadatas'][i] if all_entries['metadatas'] else {}
                    ts = meta.get('timestamp', 0)
                    doc_hash = hash(doc)
                    
                    if doc_hash in seen_hashes:
                        # Keep the newer one (higher timestamp), delete the older one
                        existing_id, existing_ts = seen_hashes[doc_hash]
                        if ts > existing_ts:
                            ids_to_delete.append(existing_id)
                            seen_hashes[doc_hash] = (doc_id, ts)
                        else:
                            ids_to_delete.append(doc_id)
                    else:
                        seen_hashes[doc_hash] = (doc_id, ts)
                
                if ids_to_delete:
                    # Delete in batches to avoid ChromaDB limits
                    batch_size = 100
                    for batch_start in range(0, len(ids_to_delete), batch_size):
                        batch = ids_to_delete[batch_start:batch_start + batch_size]
                        with _memory_lock:
                            collection.delete(ids=batch)
                    logger.info(f"[Memory] Deduplication removed {len(ids_to_delete)} duplicate entries.")
        except Exception as dedup_err:
            _mark_memory_unhealthy(str(dedup_err))
            logger.warning(f"[Memory] Deduplication phase failed: {dedup_err}")

    except Exception as e:
        _mark_memory_unhealthy(str(e))
        logger.error(f"[Memory] Memory pruning failed: {e}", exc_info=True)
