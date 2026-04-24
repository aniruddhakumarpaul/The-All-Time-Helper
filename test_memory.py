import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.logic.memory import query_memory

def test_recall():
    query = "How does the interactive mascot tilt logic work?"
    print(f"TESTING RECALL: '{query}'")
    
    results = query_memory(query, n_results=2)
    
    if not results:
        print("FAILED: No memories found.")
        return

    for i, r in enumerate(results):
        print(f"\n[Result {i+1}] from {r['metadata'].get('file')}")
        print(f"Content snippet: {r['content'][:200]}...")

if __name__ == "__main__":
    test_recall()
