import os
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

    results = collection.query(**query_args)
    
    formatted_results = []
    if results['documents']:
        for i in range(len(results['documents'][0])):
            distance = results['distances'][0][i] if 'distances' in results else 0
            
            # Semantic Guardrail: Reject if the match is too weak
            if distance > threshold:
                logger.debug(f"DEBUG: Rejecting Weak Memory (Dist: {distance:.2f})")
                continue

            formatted_results.append({
                "content": results['documents'][0][i],
                "metadata": results['metadatas'][0][i],
                "distance": distance
            })
    return formatted_results

def delete_memory(doc_id: str, user_id: str = None, clear: bool = False):
    """Prunes a specific memory entry by ID. Enforces ownership if user_id is in context."""
    uid = user_id or user_context.get()
    try:
        # Clear existing if needed
        if clear:
            logger.info(f"[Memory] Clearing existing collection for User: {uid or 'Global'}")
            collection.delete(where={"user_id": uid} if uid else {})
        else:
            # If we have a user context, we only delete if the user owns it
            if uid:
                collection.delete(ids=[doc_id], where={"user_id": uid})
            else:
                collection.delete(ids=[doc_id])
            logger.debug(f"DEBUG: Memory {doc_id} successfully pruned.")
    except Exception as e:
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
    try:
        # A dummy query to force model loading in the background
        collection.peek(limit=1)
        print("DEBUG: Neural Memory (RAG) warmed up successfully.")
    except:
        pass

import threading
threading.Thread(target=warmup_memory, daemon=True).start()
def prune_stale_memories(days: int = 30):
    """
    Removes memories older than X days to maintain high retrieval performance.
    Skips entries with metadata 'permanent': True.
    FIX #7: Also enforces a hard cap on total collection size.
    """
    MAX_MEMORY_ENTRIES = 10000  # Hard cap to prevent unbounded growth
    
    cutoff = time.time() - (days * 86400)
    try:
        # Phase 1: Prune stale entries
        stale = collection.get(
            where={"timestamp": {"$lt": cutoff}}
        )
        
        if stale and stale['ids']:
            ids_to_delete = []
            for i in range(len(stale['ids'])):
                metadata = stale['metadatas'][i] if stale['metadatas'] else {}
                if not metadata.get('permanent'):
                    ids_to_delete.append(stale['ids'][i])
            
            if ids_to_delete:
                collection.delete(ids=ids_to_delete)
                logger.info(f"[Memory] Pruned {len(ids_to_delete)} stale memory entries.")
        
        # Phase 2: Enforce hard cap (remove oldest if over limit)
        total_count = collection.count()
        if total_count > MAX_MEMORY_ENTRIES:
            overflow = total_count - MAX_MEMORY_ENTRIES
            oldest = collection.get(limit=overflow)
            if oldest and oldest['ids']:
                collection.delete(ids=oldest['ids'])
                logger.info(f"[Memory] Pruned {len(oldest['ids'])} overflow entries (cap: {MAX_MEMORY_ENTRIES}).")
                
    except Exception as e:
        print(f"ERROR: Memory pruning failed: {str(e)}")
