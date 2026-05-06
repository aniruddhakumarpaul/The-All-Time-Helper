from duckduckgo_search import DDGS

def test_search():
    query = "recent events in politics in westbengal"
    print(f"Testing search for: {query}")
    try:
        results = DDGS().text(query, max_results=5)
        print(f"Results type: {type(results)}")
        print(f"Results: {results}")
        
        if not results:
            print("No results found.")
        else:
            for r in results:
                print(f"Title: {r['title']}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_search()
