import os
import threading
import time
import chromadb
import logging
from chromadb.config import Settings
from typing import List, Dict, Optional
from contextvars import ContextVar
from app.logger import logger

# Context variable to store the current user_id for multi-tenancy isolation
user_context: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
admin_auth_context: ContextVar[Optional[str]] = ContextVar("admin_key", default=None)

# Define the persistent storage path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
MEMORY_PATH = os.path.join(BASE_DIR, ".project_brain")

# Ensure the directory exists
if not os.path.exists(MEMORY_PATH):
    os.makedirs(MEMORY_PATH)

# Initialize ChromaDB Client (Persistent)
client = chromadb.PersistentClient(path=MEMORY_PATH)

# The 'neural_memory' collection stores project code, logic, and decisions
collection = client.get_or_create_collection(name="neural_memory")
_memory_unhealthy_reason: Optional[str] = None
_memory_retry_at = 0.0
_memory_lock = threading.RLock()
MEMORY_RETRY_SECONDS = max(1, int(os.getenv("MEMORY_RETRY_SECONDS", "15")))


def _record_memory_failure(exc: Exception) -> None:
    global _memory_unhealthy_reason, _memory_retry_at
    _memory_unhealthy_reason = type(exc).__name__
    _memory_retry_at = time.monotonic() + MEMORY_RETRY_SECONDS


def _clear_memory_failure() -> None:
    global _memory_unhealthy_reason, _memory_retry_at
    _memory_unhealthy_reason = None
    _memory_retry_at = 0.0


def memory_runtime_status() -> Dict:
    now = time.monotonic()
    with _memory_lock:
        if _memory_unhealthy_reason and now < _memory_retry_at:
            return {
                "healthy": False,
                "degraded": True,
                "retry_after_seconds": max(1, int(_memory_retry_at - now + 0.999)),
            }
        try:
            collection.count()
        except Exception as exc:
            _record_memory_failure(exc)
            return {
                "healthy": False,
                "degraded": True,
                "retry_after_seconds": MEMORY_RETRY_SECONDS,
            }
        _clear_memory_failure()
        return {"healthy": True, "degraded": False, "retry_after_seconds": 0}

def index_document(doc_id: str, content: str, metadata: Dict = None, user_id: str = None):
    """Adds or updates a document in the semantic memory with forced user isolation."""
    # Use provided user_id or fallback to context
    uid = user_id or user_context.get()
    if not uid:
        logger.warning("Attempted to index document without a User ID. Skipping.")
        return

    metadata = metadata or {}
    metadata.update({
        "timestamp": time.time(),
        "user_id": uid
    })
    
    with _memory_lock:
        collection.upsert(
            ids=[doc_id],
            documents=[content],
            metadatas=[metadata]
        )

def query_memory(query_text: str, n_results: int = 3, filter_dict: Dict = None, threshold: float = 0.65, user_id: str = None) -> List[Dict]:
    """
    Retrieves relevant snippets with STRICT user-level isolation and semantic thresholding.
    """
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

    with _memory_lock:
        if _memory_unhealthy_reason and time.monotonic() < _memory_retry_at:
            return []
        try:
            results = collection.query(**query_args)

            # Tool rules use a wider threshold so behavioral constraints stay grounded.
            rule_filter = {"$and": [{"user_id": uid}, {"type": "tool_rule"}]}
            rule_results = collection.query(query_texts=[query_text], n_results=3, where=rule_filter)
        except Exception as exc:
            _record_memory_failure(exc)
            logger.error(
                "[Memory] Query failed closed (%s). Retrying in %ss.",
                type(exc).__name__,
                MEMORY_RETRY_SECONDS,
            )
            return []
        _clear_memory_failure()

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

    # Add standard results (if they meet the strict 0.65 threshold)
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
    """Prune memory while enforcing ownership when a user context exists."""
    uid = user_id or user_context.get()
    try:
        with _memory_lock:
            if clear:
                logger.info("[Memory] Clearing existing collection for the active scope.")
                collection.delete(where={"user_id": uid} if uid else {})
            elif uid:
                collection.delete(ids=[doc_id], where={"user_id": uid})
            else:
                collection.delete(ids=[doc_id])
        logger.debug("[Memory] Entry pruned.")
    except Exception as exc:
        logger.debug("[Memory] Pruning failed (%s).", type(exc).__name__)


def log_insight(insight_title: str, insight_body: str, metadata_ext: Dict = None, user_id: str = None):
    """Log a project decision or architectural insight with user isolation."""
    uid = user_id or user_context.get()
    if not uid:
        logger.warning("Attempted to log insight without a User ID. Skipping.")
        return

    doc_id = f"insight_{uid}_{insight_title.lower().replace(' ', '_')}_{int(time.time() * 1000)}"
    metadata = {"type": "insight", "title": insight_title, "category": "architecture"}
    if metadata_ext:
        metadata.update(metadata_ext)
    index_document(doc_id, insight_body, metadata, user_id=uid)
    logger.debug("[Memory] Neural insight stored for the active user.")


def warmup_memory():
    try:
        with _memory_lock:
            collection.peek(limit=1)
        logger.debug("Neural Memory (RAG) warmed up successfully.")
    except Exception as exc:
        logger.debug("[Memory] Warmup skipped (%s).", type(exc).__name__)


threading.Thread(target=warmup_memory, daemon=True).start()


def prune_stale_memories(days: int = 30, user_id: str = None):
    """Remove stale non-permanent memories and enforce a bounded store size."""
    uid = user_id or user_context.get()
    max_entries = 10000
    cutoff = time.time() - (days * 86400)
    try:
        with _memory_lock:
            where_filter = (
                {"$and": [{"timestamp": {"$lt": cutoff}}, {"user_id": uid}]}
                if uid
                else {"timestamp": {"$lt": cutoff}}
            )
            stale = collection.get(where=where_filter)
            if stale and stale.get("ids"):
                stale_ids = [
                    entry_id
                    for entry_id, metadata in zip(stale["ids"], stale.get("metadatas") or [])
                    if not (metadata or {}).get("permanent")
                ]
                if stale_ids:
                    collection.delete(ids=stale_ids)
                    logger.info("[Memory] Pruned %s stale memory entries.", len(stale_ids))

            total_count = collection.count()
            if total_count > max_entries:
                snapshot = collection.get(include=["metadatas"])
                candidates = sorted(
                    (
                        (float((metadata or {}).get("timestamp", 0)), entry_id)
                        for entry_id, metadata in zip(snapshot.get("ids") or [], snapshot.get("metadatas") or [])
                        if not (metadata or {}).get("permanent")
                    ),
                    key=lambda item: item[0],
                )
                overflow_ids = [entry_id for _, entry_id in candidates[: total_count - max_entries]]
                if overflow_ids:
                    collection.delete(ids=overflow_ids)
                    logger.info("[Memory] Pruned %s overflow entries (cap: %s).", len(overflow_ids), max_entries)
    except Exception as exc:
        logger.error("[Memory] Pruning failed (%s).", type(exc).__name__)