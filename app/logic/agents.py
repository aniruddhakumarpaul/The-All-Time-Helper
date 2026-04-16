import os
import requests
import json
import base64
from dotenv import load_dotenv
from crewai import Agent, Task, Crew, Process, LLM
from app.logic.tools import (
    search_tool,
    send_email_tool,
    calculate_horoscope,
    analyze_palm_lines,
    generate_visionary_image
)
from app.logic.logger import log_agent_step

# Ensure environment variables are loaded
load_dotenv()

def get_llm(model_id="agentic-pro"):
    """Factory to get the right LLM brain based on the user's selection."""
    
    # CASE 1: Cloud Agentic Pro (Groq)
    if not model_id or model_id == "agentic-pro":
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY missing - required for Agentic Pro.")
        return LLM(
            model="groq/llama-3.3-70b-versatile",
            temperature=0.3,
            api_key=api_key
        )
    
    # CASE 2: Local Ollama Model
    # Expecting model_id like 'gemma2:2b', 'llama3', etc.
    return LLM(
        model=f"ollama/{model_id}",
        base_url="http://localhost:11434",
        temperature=0.3
    )

# --- AGENT DEFINITIONS (created fresh per-request to use current env vars) ---

def _build_agents(llm):
    """Factory that creates a fresh set of agents with the current LLM instance."""

    # 1. Senior Developer Agent
    developer = Agent(
        role='Senior Software Engineer',
        goal='Analyze code, fix bugs, and provide precise technical guidance.',
        backstory='''You are an elite developer with decades of experience.
        You are precise, logical, and always encouraging to junior developers.''',
        tools=[search_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False
    )

    # 2. Personal Secretary Agent
    secretary = Agent(
        role='Strategic Personal Assistant',
        goal='Manage communications and send professional emails.',
        backstory='''You are organized, efficient, and have a perfect command of professional etiquette.
        You help the user stay on top of their life.''',
        tools=[send_email_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False
    )

    # 3. The Mystic (Art) Specialist
    # BUG FIX: Removed invalid 'system_prompt' kwarg (not supported by CrewAI Agent).
    # The art mandate is now enforced via the Task description and backstory.
    mystic = Agent(
        role='Celestial Guide & Visual Artist',
        goal='''First, expand simple user prompts into rich, ultra-detailed artistic descriptions (8k, cinematic, detailed textures).
        Second, call the generate_visionary_image tool with that expanded description.
        Your final response must be ONLY the raw markdown tag returned by the tool.''',
        backstory='''You are a master visual artist. You never just "draw a cat". 
        You envision the concept, lighting, texture, and atmosphere (e.g., "A regal 
        golden tabby with emerald eyes, sun dappled through autumn leaves, bokeh, 
        8k masterpiece"). You then feed this vision into your artisan tools.
        OUTPUT RULE: Return ONLY the exact markdown string from the tool.''',
        tools=[calculate_horoscope, analyze_palm_lines, generate_visionary_image],
        llm=llm,
        verbose=True,
        allow_delegation=False
    )

    # 4. The All Time Helper (Manager)
    # BUG FIX: Manager's backstory now explicitly instructs it to pass image outputs
    # through verbatim rather than summarizing them.
    manager = Agent(
        role='The All Time Helper',
        goal='''Be the best AI assistant. Orchestrate specialists to answer queries.
        For image requests: delegate to the Visual Artist and pass their EXACT output
        to the user without any modification. NEVER strip or modify markdown image tags.''',
        backstory='''You are a state-of-the-art AI assistant manager.
        CRITICAL RULE: When a specialist returns markdown containing ![...](...),
        you MUST include it verbatim in your final output. Never paraphrase it.
        Never convert an image tag to a plain hyperlink.
        NEVER claim you cannot draw — always delegate to your Visual Artist specialist.''',
        tools=[],  # Manager delegates; it does not use tools directly
        llm=llm,
        verbose=True,
        allow_delegation=True
    )

    return developer, secretary, mystic, manager


def process_image(img_base64: str):
    """Uses a local Vision model (Moondream/Ollama) to describe an image."""
    try:
        if "," in img_base64:
            img_base64 = img_base64.split(",")[1]

        payload = {
            "model": "moondream",
            "prompt": "Describe this image in detail. If it is a human hand, describe the lines on the palm (Life line, Heart line, etc).",
            "stream": False,
            "images": [img_base64]
        }
        response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=15)
        return response.json().get("response", "I can see an image but cannot discern details.")
    except Exception as e:
        return f"Vision analysis unavailable: {str(e)}"


def run_helper_agent(user_prompt: str, img_data: str = None, target_model: str = "agentic-pro"):
    """Orchestrates the specialized agents to answer the user prompt."""

    # BUG FIX: Get a fresh LLM instance per request so env vars are always current
    llm = get_llm(target_model)
    developer, secretary, mystic, manager = _build_agents(llm)

    context = user_prompt
    if img_data:
        image_description = process_image(img_data)
        context = f"User provided an image. Image Description: {image_description}\n\nUser Question: {user_prompt}"

    is_image_request = any(kw in user_prompt.lower() for kw in [
        'draw', 'paint', 'picture', 'image', 'photo', 'scenery', 'sketch',
        'illustration', 'generate image', 'create image', 'show me', 'visualize'
    ])

    if is_image_request:
        # BUG FIX: For image requests, use a dedicated single-agent task on mystic
        # to bypass the hierarchical manager's tendency to paraphrase tool output.
        image_task = Task(
            description=f'''The user wants an image: "{context}".
            
            1. REWRITE the user's request into a sophisticated, 100-word artistic prompt.
               Include cinematic lighting, specific camera lenses (e.g. 35mm), resolution (8k/HD), 
               and high-end artistic styles (Cyberpunk, Surrealism, etc.) where appropriate.
            
            2. CALL the generate_visionary_image tool with this expanded masterpiece prompt.
            
            STRICT OUTPUT RULE: Your final answer must be EXACTLY and ONLY the markdown
            returned by the tool (starting with '![').
            Do NOT add any text before or after it.''',
            expected_output="A single raw markdown image tag produced from an artistically expanded prompt.",
            agent=mystic
        )

        crew = Crew(
            agents=[mystic],
            tasks=[image_task],
            verbose=True
        )
    else:
        # Standard hierarchical flow for non-image requests
        main_task = Task(
            description=f'''Respond to the user request: "{context}".
            Be helpful, professional, and thorough.
            If the user asks for an email, use the send_email_tool.
            If the user asks for a web search, use the search_tool.
            If the user asks for a horoscope, use the calculate_horoscope tool.''',
            expected_output="A helpful, accurate, and professional response to the user's request."
        )

        crew = Crew(
            agents=[developer, secretary, mystic],
            tasks=[main_task],
            manager_agent=manager,
            process=Process.hierarchical,
            step_callback=log_agent_step,
            verbose=True
        )

    return crew.kickoff()


# Exportable function for the router
def ask_the_helper(prompt: str, img_data: str = None, target_model: str = "agentic-pro"):
    return run_helper_agent(prompt, img_data, target_model)
