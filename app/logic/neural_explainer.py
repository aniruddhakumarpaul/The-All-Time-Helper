import os
import litellm

def explain_neural_context(query_text, snippets):
    """
    Uses a high-speed LLM to explain the relevance of technical snippets 
    retrieved from the neural memory in relation to a user's dragged message.
    """
    try:
        formatted_snippets = ""
        for i, s in enumerate(snippets, 1):
            clean_s = s[:500] + "..." if len(s) > 500 else s
            formatted_snippets += f"\nSnippet {i}:\n{clean_s}\n"

        prompt = f"""[ROLE] Neural Interpreter
[CONTEXT] User query: "{query_text}"
[DATA] {formatted_snippets}

[TASK] In 2 extremely concise sentences, explain how these technical snippets relate to the user's query."""

        response = litellm.completion(
            model="groq/llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            api_key=os.getenv("GROQ_API_KEY"),
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Neural Explainer Error: {e}")
        return "The system retrieved these technical links, but I'm still processing the exact neural connection for you."
