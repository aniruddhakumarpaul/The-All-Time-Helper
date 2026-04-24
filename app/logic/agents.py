import os
import requests
import json
import base64
from dotenv import load_dotenv
from functools import lru_cache
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_message
from crewai import Agent, Task, Crew, Process, LLM
from typing import List, Optional, Any
from app.logic import tools
from app.logic.logger import log_agent_step
from app.logic.memory import query_memory, log_insight

# Ensure environment variables are loaded
load_dotenv()

# API Key Rotation Pool
GROQ_KEYS = [os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_BACKUP")]
GROQ_KEYS = [k for k in GROQ_KEYS if k]
_key_index = 0

def get_next_groq_key():
    global _key_index
    if not GROQ_KEYS: return None
    key = GROQ_KEYS[_key_index % len(GROQ_KEYS)]
    _key_index += 1
    return key

@lru_cache(maxsize=20)
def get_llm(model_id="agentic-pro", api_key=None):
    """Factory to get the right LLM brain based on the user's selection, cached to prevent latency."""
    
    # CASE 1: Cloud Agentic Pro (Groq)
    if not model_id or model_id == "agentic-pro":
        target_key = api_key or os.getenv("GROQ_API_KEY")
        if not target_key:
            raise ValueError("GROQ_API_KEY missing - required for Agentic Pro.")
        return LLM(
            model="groq/llama-3.3-70b-versatile",
            temperature=0.3,
            api_key=target_key
        )
    
    # CASE 2: Local Ollama Model
    # Expecting model_id like 'gemma2:2b', 'llama3', etc.
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    
    # Pre-verification: Check if model exists in Ollama to avoid 404 crash
    try:
        check_res = requests.get(f"{ollama_url}/api/tags", timeout=2)
        if check_res.status_code == 200:
            models = [m['name'] for m in check_res.json().get('models', [])]
            # Simple fuzzy match (model_id vs model_id:latest/tag)
            if not any(model_id in m for m in models):
                if model_id == "helper":
                    raise ValueError(f"Model '{model_id}' not found. Please complete the Colab training, download the GGUF, and run 'ollama create helper -f app/logic/fine_tuning/Modelfile' in your terminal.")
                else:
                    raise ValueError(f"Model '{model_id}' not found in your local Ollama. Please run 'ollama pull {model_id}' in your terminal.")
    except Exception as e:
        # If Ollama is down or verification fails, we let the LLM call try anyway, 
        # but if we know it's missing, we've already raised the error above.
        if isinstance(e, ValueError): raise e

    return LLM(
        model=f"ollama/{model_id}",
        base_url=ollama_url,
        temperature=0.3
    )

# --- AGENT DEFINITIONS (created fresh per-request to use current env vars) ---

def _build_agents(llm, use_tools=True, sys_config=None):
    """Internal factory to create agents with an LLM instance. Omit tools if model doesn't support them."""
    
    # Tool assignment based on capability
    dev_tools = [tools.search_tool, tools.recall_memory, tools.archive_insight] if use_tools else []
    sec_tools = [tools.send_email_tool] if use_tools else []
    mystic_tools = [tools.calculate_horoscope, tools.analyze_palm_lines, tools.generate_visionary_image] if use_tools else []
    mem_tools = [tools.recall_memory, tools.archive_insight] if use_tools else []

    # Dynamic Persona Enhancements from sys_config
    persona_suffix = ""
    if sys_config:
        if sys_config.get('english'):
            persona_suffix += " STRICT RULE: Respond ONLY in the English Language."
        if sys_config.get('oneword'):
            persona_suffix += " STRICT RULE: You MUST return EXACTLY ONE WORD as your final output. No more."
        if sys_config.get('pers'):
            persona_suffix += " Ensure highly personalized, empathetic tone."

    # 1. Senior Developer Agent
    developer = Agent(
        role='Senior Software Engineer',
        goal=f'Analyze code, fix bugs, and provide precise technical guidance.{persona_suffix}',
        backstory=f'''You are an elite developer with decades of experience.
        You are precise, logical, and always encouraging to junior developers.{persona_suffix}''',
        tools=dev_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=3
    )

    # 2. The Secretary (Email & Comms)
    secretary = Agent(
        role='Senior Executive Secretary',
        goal=f'Managed communications and email dispatch. Ensure all emails are professional and accurate.{persona_suffix}',
        backstory=f'You are a master of organization. You handle email formatting and sending carefully.{persona_suffix}',
        tools=sec_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=3 # SECURITY: Prevent infinite loops
    )

    # 3. The Mystic (Art) Specialist
    mystic = Agent(
        role='Celestial Guide & Visual Artist',
        goal=f'''First, expand simple user prompts into rich, ultra-detailed artistic descriptions (8k, cinematic, detailed textures).
        Second, call the generate_visionary_image tool with that expanded description.
        Your final response must be the EXACT markdown tag returned by the tool. STOP after receiving the link.{persona_suffix}''',
        backstory=f'''You are a master visual artist. You never just "draw a cat". 
        You envision the concept, lighting, texture, and atmosphere.
        As soon as you receive the tool output, you MUST STOP and return the markdown link.
        If the output starts with IMAGE_GENERATED_SUCCESS, your job is complete.{persona_suffix}''',
        tools=mystic_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=3 # SECURITY: Prevent infinite loops
    )

    # 4. The All Time Helper (Manager)
    manager = Agent(
        role='The All Time Helper',
        goal=f'''Be the best AI assistant. Orchestrate specialists to answer queries.
        For image requests: delegate to the Visual Artist and pass their EXACT output
        to the user without any modification. NEVER strip or modify markdown image tags.{persona_suffix}''',
        backstory=f'''You are a state-of-the-art AI assistant manager.
        CRITICAL RULE: When a specialist returns markdown containing ![...](...),
        you MUST include it verbatim in your final output. Never paraphrase it.
        Never convert an image tag to a plain hyperlink.
        NEVER claim you cannot draw — always delegate to your Visual Artist specialist.{persona_suffix}''',
        tools=mem_tools,  # Manager uses memory to orchestrate context
        llm=llm,
        verbose=True,
        allow_delegation=True,
        max_iter=3 # SECURITY: Prevent infinite loops
    )

    # 5. The Generalist (For Local Models)
    generalist = Agent(
        role='Expert System Assistant',
        goal=f'Provide comprehensive help using all available tools while maintaining a professional, helpful tone.{persona_suffix}',
        backstory=f'''You are a highly capable AI generalist. 
        You have direct access to Search, Email, and Art tools.
        STRICT RULES:
        1. NEVER output raw JSON code blocks or tool-call fragments to the user.
        2. DO NOT use tools (Email, Search, etc.) unless the user EXPLICITLY asks for them (e.g., 'send an email' or 'search for').
        3. For sensitive topics (Mental Health/Diagnosis): Provide empathetic orientation and resources. YOU ARE PERMITTED to help find support and offer compassion, but DO NOT provide medical treatments or clinical assessments.
        4. If a specialty tool is not relevant, answer directly and empathetically.{persona_suffix}''',
        tools=dev_tools + sec_tools + mystic_tools + mem_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=3 # SECURITY: Prevent infinite loops on local hardware
    )

    return developer, secretary, mystic, manager, generalist

def get_agent_swarm(model_id, api_key=None, force_no_tools=False, sys_config=None):
    """Cached factory that builds the entire agent swarm based on model capabilities."""
    llm = get_llm(model_id, api_key)
    
    # Force Text-Only mode for models that fail on native tool calling (e.g. Gemma 2B or fine-tuned personas)
    use_tools = True
    if "gemma" in str(model_id).lower() or force_no_tools or "helper" in str(model_id).lower():
        use_tools = False
        print(f"DEBUG: Setting Text-Only Mode (Model: {model_id}, Force: {force_no_tools})")
        
    return _build_agents(llm, use_tools=use_tools, sys_config=sys_config)


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
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        # Increased timeout for local vision models (they can be slow to load)
        response = requests.post(f"{ollama_url}/api/generate", json=payload, timeout=45, verify=False)
        return response.json().get("response", "I can see an image but cannot discern details.")
    except Exception as e:
        return f"Vision analysis unavailable (Hardware Lag): {str(e)}"


def run_helper_agent(user_prompt: str, img_data: str = None, target_model: str = "agentic-pro", sys_config: dict = None, history: List[dict] = None, persona: bool = False):
    """Orchestrates the specialized agents to answer the user prompt with system configurations."""
    
    # --- ULTRA-FAST IMAGE TRACK (BYPASS EVERYTHING) ---
    is_image_request = any(kw in user_prompt.lower() for kw in [
        'draw', 'paint', 'picture', 'image', 'photo', 'scenery', 'sketch',
        'illustration', 'generate image', 'create image', 'show me', 'visualize'
    ])
    is_local = target_model != "agentic-pro"

    if is_image_request and is_local:
        print(f"DEBUG: Ultra-Fast Local Image Track Active")
        import urllib.parse
        import time
        clean_desc = user_prompt.lower()
        for kw in ['draw me a ', 'draw me ', 'draw a ', 'draw ', 'paint me a ', 'paint me ', 'paint a ', 'paint ', 'show me a ', 'show me ', 'create an image of ', 'generate an image of ']:
            clean_desc = clean_desc.replace(kw, '')
        clean_desc = clean_desc.strip().capitalize()
        encoded = urllib.parse.quote(clean_desc)
        seed = (abs(hash(clean_desc)) + int(time.time())) % 1000000
        image_url = f"https://image.pollinations.ai/prompt/{encoded}?model=turbo&width=1024&height=1024&nologo=true&seed={seed}"
        return f"![{clean_desc}]({image_url})"

    # 0. Persona Routing: Support for Fine-Tuned Local Models
    if persona:
        print(f"DEBUG: Multifaceted Persona Active. Routing to 'helper'")
        target_model = "helper"

    # 0.1 Sensitive Routing: Force 'helper' model for sensitive topics (Privacy & Empathy)
    sensitive_keywords = ['mental', 'health', 'medical', 'legal', 'suicide', 'depressed', 'anxiety', 'therapy', 'diagnosis']
    is_sensitive = any(kw in user_prompt.lower() for kw in sensitive_keywords)
    
    if is_sensitive and target_model != "helper":
        print(f"DEBUG: Sensitive Topic detected. Elevating privacy: Routing to 'helper'.")
        target_model = "helper"

    # Key Rotation logic for Groq
    target_key = None
    if target_model == "agentic-pro":
        target_key = get_next_groq_key()

    # 1. Decision Engine
    needs_mystic = any(kw in user_prompt.lower() for kw in ['draw', 'paint', 'horoscope', 'palm', 'picture', 'image', 'photo', 'sketch'])
    needs_search = any(kw in user_prompt.lower() for kw in ['search', 'weather', 'stock', 'news', 'find'])
    needs_email = any(kw in user_prompt.lower() for kw in ['email', 'send'])
    
    requires_tools = (needs_mystic or needs_search or needs_email) and not is_sensitive
    force_no_tools = is_local and not requires_tools

    # DEFERRED AGENT BUILDING: We only build the swarm if we actually need it for text tasks or cloud images.
    # developer, secretary, mystic, manager, generalist = get_agent_swarm(target_model, target_key, force_no_tools=force_no_tools, sys_config=sys_config)


    context = user_prompt
    # Only analyze the image if it's actually referred to or if we are in vision mode
    image_reference_keywords = ['this', 'that', 'image', 'picture', 'photo', 'look', 'see', 'describe', 'analyze', 'what is', 'tell me about']
    is_referring_to_image = any(kw in user_prompt.lower() for kw in image_reference_keywords)

    if img_data and is_referring_to_image:
        print("DEBUG: Image reference detected. Analyzing with Moondream...")
        image_description = process_image(img_data)
        context = f"User provided an image. Image Description: {image_description}\n\nUser Question: {user_prompt}"
    elif img_data:
        print("DEBUG: Image present but no direct reference in prompt. Skipping Moondream for speed.")


    if is_image_request:
        # Cloud images still proceed here to use the agent swarm
        # Build only the mystic agent for cloud images
        _, _, mystic, _, _ = get_agent_swarm(target_model, target_key, force_no_tools=force_no_tools, sys_config=sys_config)
        image_task = Task(
            description=f'''User image request: "{context}".
            1. Expand this into a high-fidelity artistic prompt (cinematic, 8k, detailed). 
            2. CALL generate_visionary_image with this vision.
            STRICT OUTPUT: ONLY the raw markdown tag starting with '!['.''',
            expected_output="A single markdown image tag.",
            agent=mystic
        )
        crew = Crew(agents=[mystic], tasks=[image_task], verbose=True)
        result = getattr(crew.kickoff(), 'raw', str(crew.kickoff())) if hasattr(crew.kickoff(), 'raw') else str(crew.kickoff())
    else:
        system_instructions = ""
        if sys_config:
            if sys_config.get('english'):
                system_instructions += "\n- CRITICAL: Respond ONLY in the English Language."
            if sys_config.get('oneword'):
                system_instructions += "\n- CRITICAL: Provide exactly ONE WORD as your output."
            if sys_config.get('pers'):
                system_instructions += "\n- CRITICAL: Ensure highly personalized, empathetic tone."

        history_context = ""
        if history:
            history_context = "\n<internal_memory>\nCONVERSATION HISTORY:\n"
            for msg in history[-5:]:
                role = "User" if msg.get('role') == 'user' else "Assistant"
                content = msg.get('content', '').strip()
                if content: history_context += f"{role}: {content}\n"
            history_context += "</internal_memory>\n"

        task_desc = f'Respond to the user request: "{context}". Be helpful and professional.'
        use_tools = "gemma" not in str(target_model).lower()
        if use_tools and not is_sensitive:
            task_desc += "\nUse tools for search, email, or horoscope if needed."
        if is_sensitive:
            task_desc += "\n- ATTENTION: Provide EMPATHETIC ORIENTATION and crisis resources."
        
        task_desc += f"\n{system_instructions}\n- CRITICAL: DO NOT repeat text from <internal_memory>."

        # Define dynamic expected output for strictness
        expected_output = "A helpful and professional response."
        if sys_config and sys_config.get('oneword'):
            expected_output = "EXACTLY ONE WORD. Do not explain your response."

        main_task = Task(description=task_desc, expected_output=expected_output)

        # --- EXECUTION PATHWAY ---
        result = None
        crew = None

        # --- NEURAL MEMORY RETRIEVAL (RAG) ---
        # Before answering, we "think" about what we know from previous sessions
        semantic_memories = query_memory(user_prompt, n_results=3)
        memory_block = ""
        if semantic_memories:
            memory_block = "\n<neural_context>\n"
            for m in semantic_memories:
                memory_block += f"- {m['content']}\n"
            memory_block += "</neural_context>\n"

        # --- EXECUTION PATHWAY: CREW-LESS TEXT PROCESSING ---
        result = None
        
        # 1. Cloud Engine (Agentic Pro / Groq)
        if target_model == "agentic-pro":
            print(f"DEBUG: Executing Deep Context Analysis for {target_model}...")
            # BUILD SWARM ONLY WHEN NEEDED
            developer, secretary, mystic, manager, generalist = get_agent_swarm(target_model, target_key, force_no_tools=force_no_tools, sys_config=sys_config)
            llm_engine = get_llm(target_model, target_key)
            
            messages = []
            
            # System Role Integration: INTELLECTUAL PARTNER MODE
            sys_msg = (
                "You are 'The All Time Helper', a sophisticated intellectual partner. "
                "Your goal is to have a deep, coherent conversation. Analyze the <neural_context> and history "
                "to provide answers that are grounded in past decisions and current logic. "
                "Avoid generic AI boilerplate. Be precise, creative, and strictly build upon previous context. "
            )
            
            if is_sensitive:
                sys_msg += "TOP PRIORITY: This is a sensitive topic. Provide empathetic orientation and crisis resources. "
            if sys_config and sys_config.get('oneword'):
                sys_msg += "STRICT RULE: YOU MUST RESPOND WITH EXACTLY ONE WORD. "
            elif system_instructions:
                sys_msg += f"SYSTEM PREFERENCES: {system_instructions}"
            
            messages.append({"role": "system", "content": sys_msg})
            
            # Use the retrieved memory block to ground the LLM
            if memory_block:
                messages.append({"role": "system", "content": f"Relevant background info: {memory_block}"})

            if history:
                # Increased history depth to 15 for intellectual continuity
                for msg in history[-15:]:
                    messages.append({"role": "user" if msg.get("role")=="user" else "assistant", "content": msg.get("content", "")})
            
            messages.append({"role": "user", "content": context})
            
            try:
                # Direct call to the underlying LiteLLM/Groq via the CrewAI LLM wrapper
                result = llm_engine.call(messages=messages)
                
                # --- AUTO-ARCHIVING: Save this exchange for future deep context ---
                if result and len(str(result)) > 50:
                    log_insight(f"Interaction_{int(os.path.getmtime(__file__))}", f"User: {user_prompt}\nHelper: {result}")
                    
            except Exception as e:
                result = f"Cloud Engine Direct Call Error: {str(e)}"
                
        # 2. Local Engine (Ollama)
        else:
            print(f"DEBUG: Executing Deep Local Analysis for {target_model}...")
            
            # If the local request specifically needs tools, we use the Agent Swarm
            if requires_tools:
                print(f"DEBUG: Local Tool Usage Detected. Initializing Generalist Agent...")
                _, _, _, _, generalist = get_agent_swarm(target_model, target_key, force_no_tools=False, sys_config=sys_config)
                
                local_task = Task(
                    description=f"Handle the user request using your tools if necessary: {user_prompt}",
                    expected_output="A helpful response using available tools.",
                    agent=generalist
                )
                crew = Crew(agents=[generalist], tasks=[local_task], verbose=True)
                result = getattr(crew.kickoff(), 'raw', str(crew.kickoff())) if hasattr(crew.kickoff(), 'raw') else str(crew.kickoff())
            else:
                # Direct Call for simple chat (High Performance)
                messages = []
                local_sys = "You are a highly capable AI assistant. Focus on deep context and avoid generic answers. "
                if sys_config and sys_config.get('oneword'):
                    local_sys += "ONE-WORD MODE: You MUST respond with exactly ONE WORD. "
                elif system_instructions:
                    local_sys += f"SYSTEM PREFERENCES: {system_instructions}"
                
                messages.append({"role": "system", "content": local_sys})
                if memory_block:
                    messages.append({"role": "system", "content": f"Context from previous sessions: {memory_block}"})
                if history:
                    for msg in history[-10:]:
                        messages.append({"role": "user" if msg.get("role")=="user" else "assistant", "content": msg.get("content", "")})
                
                messages.append({"role": "user", "content": context})
                
                payload = {"model": target_model, "messages": messages, "stream": False}
                ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
                try:
                    res = requests.post(f"{ollama_url}/api/chat", json=payload, timeout=240, verify=False)
                    result = res.json().get("message", {}).get("content", "Error parsing response.")
                    
                    if result and len(str(result)) > 50:
                        log_insight(f"Local_{target_model}_{int(os.path.getmtime(__file__))}", f"Context: {user_prompt}\nAns: {result}")
                        
                except Exception as e:
                    result = f"Local Engine Direct Call Error: {str(e)}"

    # --- GLOBAL POST-PROCESSING HARDENING ---
    if result and sys_config and sys_config.get('oneword'):
        # Extract the first word and strip punctuation
        words = str(result).split()
        if words:
            # We take the first non-empty word and clean it
            one_word = words[0].strip('.,!?;:"\'()[]{}')
            print(f"DEBUG: One-Word Mode Enforcement Active. Original: '{result}' -> Final: '{one_word}'")
            return one_word

    return result


def ask_the_helper(prompt: str, img_data: str = None, target_model: str = "agentic-pro", sys_config: dict = None, history: List[dict] = None, persona: bool = False):
    return run_helper_agent(prompt, img_data, target_model, sys_config, history, persona)
