import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.logic.memory import query_memory, user_context, index_document

def test_recall():
    uid = "test_user_admin"
    user_context.set(uid)
    
    # 1. Index a specific memory
    print("INDEXING: 'The mascot tilts based on mouse position relative to center.'")
    index_document("mascot_logic_001", "The mascot tilts based on mouse position relative to center.", {"file": "mascot.js"}, user_id=uid)
    
    # 2. Query for it (Should succeed)
    query_1 = "How does the interactive mascot tilt logic work?"
    print(f"\nQUERY 1 (Targeted): '{query_1}'")
    results_1 = query_memory(query_1, n_results=1)
    
    for i, r in enumerate(results_1):
        print(f"[SUCCESS] Found: {r['content']} (Dist: {r['distance']:.4f})")

    # 3. Query for something totally irrelevant (Should be rejected by Weak Filter)
    query_2 = "What is the capital of France?"
    print(f"\nQUERY 2 (Irrelevant): '{query_2}'")
    print("Expected: 'DEBUG: Rejecting Weak Memory' in console and 0 results.")
    results_2 = query_memory(query_2, n_results=1, threshold=0.7) # Use strict 0.7
    
    if not results_2:
        print("[SUCCESS] Weak Filter blocked the irrelevant memory.")
    else:
        print(f"[FAILURE] Weak Filter allowed: {results_2[0]['content']} (Dist: {results_2[0]['distance']:.4f})")

if __name__ == "__main__":
    test_recall()
