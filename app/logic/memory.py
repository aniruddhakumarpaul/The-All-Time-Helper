import os
import chromadb
from chromadb.config import Settings
from typing import List, Dict

# Define the persistent storage path
# Using a hidden directory in the project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
MEMORY_PATH = os.path.join(BASE_DIR, ".project_brain")

# Ensure the directory exists
if not os.path.exists(MEMORY_PATH):
    os.makedirs(MEMORY_PATH)

# Initialize ChromaDB Client (Persistent)
client = chromadb.PersistentClient(path=MEMORY_PATH)

# The 'neural_memory' collection stores project code, logic, and decisions
# We use the default embedding model (Sentence Transformers / all-MiniLM-L6-v2) 
# because it's fast and runs locally.
collection = client.get_or_create_collection(name="neural_memory")

def index_document(doc_id: str, content: str, metadata: Dict = None):
    """Adds or updates a document in the semantic memory."""
    collection.upsert(
        ids=[doc_id],
        documents=[content],
        metadatas=[metadata or {}]
    )

def query_memory(query_text: str, n_results: int = 3) -> List[Dict]:
    """Retrieves relevant snippets from the memory based on semantic similarity."""
    results = collection.query(
        query_texts=[query_text],
        n_results=n_results
    )
    
    formatted_results = []
    if results['documents']:
        for i in range(len(results['documents'][0])):
            formatted_results.append({
                "content": results['documents'][0][i],
                "metadata": results['metadatas'][0][i],
                "distance": results['distances'][0][i] if 'distances' in results else None
            })
    return formatted_results

def log_insight(insight_title: str, insight_body: str):
    """Convenience function to log a project decision or architectural insight."""
    doc_id = f"insight_{insight_title.lower().replace(' ', '_')}"
    metadata = {"type": "insight", "title": insight_title}
    index_document(doc_id, insight_body, metadata)
    print(f"DEBUG: Neural Memory updated with insight: {insight_title}")
