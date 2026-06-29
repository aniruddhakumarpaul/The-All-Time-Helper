from duckduckgo_search import DDGS

def test_search():
    query = "recent events in politics in westbengal"
    print(f"Testing search for: {query}")
    try:
        with DDGS() as ddgs:
            print("Trying .text()...")
            results = [r for r in ddgs.text(query, max_results=5)]
            print(f"Text Results: {len(results)}")
            
            print("Trying .news()...")
            news_results = [r for r in ddgs.news(query, max_results=5)]
            print(f"News Results: {len(news_results)}")
            
            if not results and not news_results:
                print("No results found in either.")
            else:
                for r in results + news_results:
                    print(f"Found: {r.get('title', 'N/A')}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_search()
