import os
import chromadb
import sys

# Force UTF-8 output for Windows console
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Define the persistent storage path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_PATH = os.path.join(BASE_DIR, ".project_brain")

# Initialize ChromaDB Client (Persistent)
client = chromadb.PersistentClient(path=MEMORY_PATH)

# Get the collection
try:
    collection = client.get_collection(name="neural_memory")
    # Retrieve all documents
    all_data = collection.get()

    print("--- NEURAL MEMORY DUMP ---")
    if not all_data['ids']:
        print("Memory is empty.")
    else:
        for i in range(len(all_data['ids'])):
            print(f"ID: {all_data['ids'][i]}")
            print(f"Metadata: {all_data['metadatas'][i]}")
            content = all_data['documents'][i]
            # Print first 200 chars if too long, and handle encoding
            print(f"Content: {content[:500]}...")
            print("-" * 30)
except Exception as e:
    print(f"Error accessing memory: {e}")
