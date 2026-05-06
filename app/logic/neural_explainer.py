import os
from app.logic.agents import get_llm

def explain_neural_context(query_text, snippets):
    """
    Uses a high-speed LLM to explain the relevance of technical snippets 
    retrieved from the neural memory in relation to a user's dragged message.
    """
    try:
        # Use Agentic Pro (Groq 70B) for high-quality, fast summarization
        llm = get_llm("agentic-pro")
        
        # Format snippets for the prompt
        formatted_snippets = ""
        for i, s in enumerate(snippets, 1):
            # Cap each snippet to avoid context bloat
            clean_s = s[:500] + "..." if len(s) > 500 else s
            formatted_snippets += f"\nSnippet {i}:\n{clean_s}\n"

        prompt = f"""
        [ROLE] Neural Interpreter
        [CONTEXT] User query: "{query_text}"
        [DATA] {formatted_snippets}
        
        [TASK] In 2 extremely concise sentences, explain how these technical snippets relate to the user's query. 
        Be professional and direct. If snippets are irrelevant, say "I found some technical links, but they seem loosely related to your specific request."
        
        [OUTPUT]
        """
        
        # Execute call
        response = llm.call(prompt)
        
        return response.strip().strip('"').strip("'")
        
    except Exception as e:
        print(f"Neural Explainer Error: {e}")
        return "The system retrieved these technical links, but I'm still processing the exact neural connection for you."
