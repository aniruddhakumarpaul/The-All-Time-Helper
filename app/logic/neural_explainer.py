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
        [ROLE] You are the Neural Interpreter for 'The All Time Helper' AI system.
        [TASK] A user dragged this message onto the system mascot: "{query_text}"
        
        The system retrieved these technical details from the Project Memory:
        {formatted_snippets}
        
        [REQUIREMENT] 
        In 2 concise sentences, explain to a human user what these technical details mean and how they relate to the message they dragged. 
        Be professional, helpful, and avoid saying 'Here is the explanation'. 
        If the snippets are irrelevant, briefly say you're looking for more specific links.
        
        [OUTPUT] 
        """
        
        # Execute call
        response = llm.call(prompt)
        
        # Clean up response (strip quotes if any)
        return response.strip().strip('"').strip("'")
        
    except Exception as e:
        print(f"Neural Explainer Error: {e}")
        return "The system retrieved these technical links, but I'm still processing the exact neural connection for you."
