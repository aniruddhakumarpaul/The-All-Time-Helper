import os
from concurrent.futures import ThreadPoolExecutor
import requests
import json
import base64
import re
import urllib.parse
import time
import threading
from dotenv import load_dotenv
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_message
from crewai import Agent, Task, Crew, Process, LLM
from typing import List, Optional, Any
from app.logic import tools
from app.logger import logger, log_agent_step
from app.logic.memory import query_memory, log_insight
from app.logic.vision_pipeline import vision_sys
import cv2
import numpy as np

# Ensure environment variables are loaded
load_dotenv()

# Global Constants
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# API Key Rotation Pool (Thread-safe)
GROQ_KEYS = [os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_BACKUP")]
GROQ_KEYS = [k for k in GROQ_KEYS if k]
_key_index = 0
_key_lock = threading.Lock()

def get_next_groq_key():
    global _key_index
    if not GROQ_KEYS: return None
    with _key_lock:
        key = GROQ_KEYS[_key_index % len(GROQ_KEYS)]
        _key_index += 1
    return key

# Helper to keep code DRY
def _strip_base64_prefix(img_base64: str) -> str:
    return img_base64.split(",")[1] if img_base64 and "," in img_base64 else img_base64

def _extract_crew_result(crew: Crew) -> str:
    res = crew.kickoff()
    return getattr(res, 'raw', str(res)) if hasattr(res, 'raw') else str(res)

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
    ollama_url = OLLAMA_URL
    
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
        temperature=0.2
    )

# --- AGENT DEFINITIONS (created fresh per-request to use current env vars) ---

def _build_agents(llm, use_tools=True, sys_config=None):
    """Internal factory to create agents with an LLM instance. Omit tools if model doesn't support them."""
    
    # Tool assignment based on capability
    dev_tools = [tools.search_tool, tools.recall_memory, tools.archive_insight] if use_tools else []
    sec_tools = [tools.send_email_tool] if use_tools else []
    misc_tools = [tools.calculate_horoscope, tools.analyze_palm_lines] if use_tools else []
    visual_tools = [tools.image_search_tool, tools.image_generate_tool] if use_tools else []
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
        goal=f'Analyze code, fix bugs, and provide technical guidance. You have a vision sub-system to "see" images provided in VISUAL CONTEXT.{persona_suffix}',
        backstory=f'''You are an elite developer. 
        If the user refers to an image, you MUST look for the "VISUAL CONTEXT" block in your task. 
        This block describes the image to you. Treat it as your own visual perception.{persona_suffix}''',
        tools=dev_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=3
    )

    # 2. The Secretary (Email & Comms)
    secretary = Agent(
        role='Senior Executive Secretary',
        goal=(
            f"STRICT RULE: You are a high-fidelity drafting and dispatch engine. "
            f"1. ATTACHMENTS: Only include `attachment_content` if the user explicitly asks for it (e.g., 'attach the chat', 'as attachment') or if the data is a complex report over 15 lines. "
            f"2. TONE SELECTION: Detect the appropriate `tone` parameter for `send_email_tool`: "
            f"   - 'formal': For office members, bosses, or official business. "
            f"   - 'informal': For friends, family, or personal greetings. "
            f"   - 'modern': For general broadcasts or when tone is ambiguous. "
            f"3. CONTENT: You are an author. If the user asks for a joke, write a funny one. If they ask for a greeting, make it warm. NEVER repeat the user prompt as the email body. NO PLACEHOLDERS.{persona_suffix}"
        ),
        backstory=(
            "You are an elite executive assistant who understands professional nuance. "
            "You distinguish between a casual joke to a friend and a strict formal broadcast to office members. "
            "You are conservative with attachments—you only add them when it adds value or is requested. "
            "You ensure every email represents the user's intent with 100% fidelity."
        ),
        tools=sec_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=1
    )

    # Shared prompt base for Manager and Generalist (FIX #10: DRY)
    _visual_golden_rule = f'''STRICT RULE: ACT FIRST, NEVER ASK FIRST for Visuals.
        ## IMAGE & VISUAL REQUEST HANDLING GOLDEN RULE
        - image_search_tool -> for REAL things (products, people, places, animals, vehicles, brands, etc.)
        - image_generate_tool -> for FICTIONAL, CONCEPTUAL, or CREATIVE things (fantasy art, concepts, abstract visuals)
        If a user asks for any visual ("photo of X", "show me X", "generate X", "picture of X"), you MUST CALL THE TOOL IMMEDIATELY. NEVER ask for clarification or confirmation. NEVER respond with text alone.
        
        ## TOOL EXECUTION & AUTH
        - If a tool result is present (e.g. an image tag ![alt](url) or a 'LIVE SUCCESS' message), your mission is DONE. Present the result immediately.
        - Only if you see 'ERROR: AUTH_REQUIRED' should you ask for the 'Admin Key'. Explain the 'Masked' prompt feature for privacy.
        - When providing multiple options, ALWAYS use a numbered list format (1., 2., etc.).{persona_suffix}'''

    # 3. The All Time Helper (Manager)
    manager = Agent(
        role='The All Time Helper',
        goal=f'''{_visual_golden_rule}
        
        ## GENERAL EXECUTION RULES
        100% Task Completion. If an email is requested, delegate to the Secretary and ENSURE it is sent. If you see "ADMIN_KEY_PROVIDED", you MUST retry immediately. NEVER repeat the key.
        If you are presenting options or asking for clarification, ALWAYS use a numbered list (1., 2., 3.) to allow the user to select by number.{persona_suffix}''',
        backstory=(
            "You are the high-speed Manager of the All Time Helper swarm. You have ZERO tolerance for excuses. "
            "Privacy and limitations do not exist in your world; only creative drafting and tool execution. "
            "CRITICAL: If a tool returns 'AUTH_REQUIRED', ask for the 'admin_key'. Once 'ADMIN_KEY_PROVIDED' is seen, you MUST immediately retry. "
            "Just execution. No lectures."
        ),
        tools=mem_tools + visual_tools + sec_tools,
        llm=llm,
        verbose=True,
        allow_delegation=True,
        max_iter=3
    )

    # 4. Expert System Assistant (The "Helper") — shares Golden Rule with Manager
    generalist = Agent(
        role='The All Time Helper',
        goal=f"{_visual_golden_rule}\n\n## DRAFTING RULE\nIf the email tool is needed, DRAFT the content creatively (jokes, greetings, official letters). NEVER just repeat the user's instructions as the email body. You are an elite executive author, not a typewriter.",
        backstory=f'''You are an extremely compliant creative assistant. You prioritize results over conversation. If a tool works, you provide the output immediately without redundant clarification.{persona_suffix}''',
        tools=dev_tools + sec_tools + visual_tools + mem_tools + misc_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=2 if getattr(llm, 'is_weak', False) else 5
    )

    return developer, secretary, manager, generalist

def get_agent_swarm(model_id, api_key=None, force_no_tools=False, sys_config=None):
    """Cached factory that builds the entire agent swarm based on model capabilities."""
    llm = get_llm(model_id, api_key)
    
    # Force Text-Only mode for models that fail on native tool calling (e.g. legacy Gemma or fine-tuned personas)
    use_tools = True
    model_str = str(model_id).lower()
    is_legacy_gemma = "gemma" in model_str and "gemma4" not in model_str
    
    if is_legacy_gemma or force_no_tools:
        use_tools = False
        logger.debug(f"DEBUG: Setting Text-Only Mode (Model: {model_id}, Reason: {'Legacy' if is_legacy_gemma else 'Force'})")
    
    # Tag the LLM object if it's a weak model to adjust iterations later
    llm.is_weak = "2b" in model_str or "e2b" in model_str
    
    return _build_agents(llm, use_tools=use_tools, sys_config=sys_config)


def process_image_cloud(img_base64: str, api_key: str):
    """Uses a cloud-based vision model for high-fidelity description."""
    try:
        from litellm import completion
        img_base64 = _strip_base64_prefix(img_base64)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this image in extreme detail for a specialized agent swarm. Identify objects, text, emotions, and technical context."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}}
                ]
            }
        ]
        # Use Groq's Llama-3-Vision or Gemini if available
        response = completion(model="groq/llama-3.2-11b-vision-preview", messages=messages, api_key=api_key)
        return response.choices[0].message.content
    except Exception as e:
        print(f"DEBUG: Cloud Vision fallback: {e}", flush=True)
        return None

def process_image_local(img_base64: str):
    """Direct Native Multimodal Analysis using Gemma 4."""
    try:
        img_base64 = _strip_base64_prefix(img_base64)
        
        payload = {
            "model": "gemma4:e2b",
            "messages": [
                {
                    "role": "user",
                    "content": "Analyze this image in high fidelity. Identify objects, text, and architectural context. "
                               "Provide a detailed description followed by a 'KEYWORDS: ' section with 15 concepts.",
                    "images": [img_base64]
                }
            ],
            "stream": False
        }
        
        print("[Vision] Local Native Gemma 4 Analysis started...", flush=True)
        # EXTENDED TIMEOUT: 120s for Native VLM processing
        res = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120, verify=False)
        
        if res.status_code == 200:
            return res.json().get("message", {}).get("content", "Vision analysis failed.")
        else:
            return f"Gemma 4 Vision Error {res.status_code}: {res.text}"
    except Exception as e:
        logger.error(f"[Vision] Local Native Gemma 4 Error: {e}", exc_info=True)
        return f"Native vision processing timed out or failed: {str(e)}"

def save_uploaded_image(img_base64: str) -> str:
    """Saves a base64 image to static/uploads using OpenCV and returns the local URL."""
    try:
        img_base64 = _strip_base64_prefix(img_base64)
        
        # Decode base64 to OpenCV image
        img_data = base64.b64decode(img_base64)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None: return None
        
        # Create directory if it doesn't exist
        os.makedirs("static/uploads", exist_ok=True)
        
        # Generate unique filename
        filename = f"upload_{int(time.time())}_{np.random.randint(1000, 9999)}.jpg"
        filepath = os.path.join("static/uploads", filename)
        
        # Save image
        cv2.imwrite(filepath, img)
        logger.debug(f"[Agents] Image successfully saved to {filepath} using OpenCV.")
        return f"/static/uploads/{filename}"
    except Exception as e:
        logger.error(f"[Agents] CRITICAL ERROR saving uploaded image: {e}", exc_info=True)
        return None



def _reconstruct_contextual_prompt(user_prompt: str, history: list) -> str:
    """Expands ambiguous prompts (e.g. '1.', 'it') by analyzing recent conversation context."""
    if not user_prompt or not history: return user_prompt
    
    # Normalizer for history formats (handles 'role'/'content' or 'r'/'c')
    def get_msg_data(m):
        r = m.get("role") or m.get("r") or ""
        c = m.get("content") or m.get("c") or ""
        return r.lower(), c

    p = user_prompt.strip().lower()
    
    # 1. Numbered selection detection
    is_numeric = re.match(r'^(1|2|3|4|5|one|two|three|four|five|first|second|third|the first|the second)(\.)?$', p)
    
    if is_numeric:
        # Search last 5 messages for a numbered list or options in assistant responses
        for msg in reversed(history[-5:]):
            role, content = get_msg_data(msg)
            if role in ["assistant", "a", "bot", "b"]:
                # Normalize requested number
                num_map = {"one": "1", "first": "1", "two": "2", "second": "2", "three": "3", "third": "3", "four": "4", "five": "5"}
                requested_raw = p.replace("the ", "").replace(".", "").strip()
                requested_num = num_map.get(requested_raw, requested_raw)
                
                # Strategy A: Explicit numbered list (1. Item)
                matches = re.findall(r'(?i)(?:^|\n)(1|2|3|4|5|one|two|three|four|five)[\.\)\:]\s*([^\n]+)', content)
                if matches:
                    for num, text in matches:
                        if num == requested_num:
                            logger.debug(f"[Context] Resolved Numeric '{user_prompt}' to '{text.strip()}'")
                            return f"[Selection: {text.strip()}] {user_prompt}"
                
                # Strategy B: Bullet points or Line-based options (Header: Description)
                options = [line.strip() for line in content.split('\n') if len(line.strip()) > 10 and (':' in line or line.startswith(('-', '*', '•')))]
                if len(options) >= 2:
                    try:
                        idx = int(requested_num) - 1
                        if 0 <= idx < len(options):
                            text = options[idx].split(':')[0] # Take the header part
                            logger.debug(f"[Context] Resolved Option '{user_prompt}' to '{text.strip()}'")
                            return f"[Selection: {text.strip()}] {user_prompt}"
                    except: pass

    # 2. Ambiguous pronoun resolution
    ambiguous_keywords = ['it', 'that', 'this', 'show', 'do it', 'go ahead', 'proceed', 'it to me', 'show it']
    if len(p.split()) <= 4 and any(kw in p for kw in ambiguous_keywords):
        # Look for the last clear subject in history
        for msg in reversed(history[-5:]):
            role, content = get_msg_data(msg)
            if role in ["user", "u", "human"] and len(content) > 5:
                # Heuristic: Take the last few words if they don't contain verbs
                words = content.replace("?", "").replace(".", "").split()
                if words:
                    subject_hint = " ".join(words[-4:])
                    logger.debug(f"[Context] Pronoun Resolve hint: {subject_hint}")
                    return f"[Target: {subject_hint}] {user_prompt}"

    # 3. Admin Key provision detection (Secure wrapper)
    # If the user prompt is short, alphanumeric, or contains 'admin', and the bot just asked for a key
    if len(p) < 25 and (p.isalnum() or 'admin' in p):
        for msg in reversed(history[-2:]):
            role, content = get_msg_data(msg)
            if role in ["assistant", "a", "bot", "b"] and "admin key" in content.lower():
                logger.debug("[Context] Resolved Admin Key provision")
                return f"ADMIN_KEY_PROVIDED: {user_prompt}. ACTION: Use this key to CALL the send_email_tool NOW and finish the previous request."

    return user_prompt

def _detect_intent(user_prompt: str, target_model: str, history: list = None) -> dict:
    """Detects user intent using a hybrid approach: Fast-Track Regex + Heuristic fallback.
    
    Returns a dict with:
        - is_sensitive: bool — triggers persona model routing
        - requires_tools: bool — whether agent needs tool calling
        - complexity: 'swarm' | 'single' | 'direct' — routing tier
            - 'swarm': full Manager+Secretary+Developer crew (email/delegation)
            - 'single': Generalist agent only (visual/search/mystic)
            - 'direct': no tools, direct LLM call (conversation)
        - is_local: bool — local vs cloud engine
    """
    p = user_prompt.lower()
    
    # 1. Fast-Track Keywords (Zero Latency)
    needs_visual = any(kw in p for kw in ['draw', 'paint', 'sketch', 'generate', 'create', 'artwork', 'photo of', 'show me a picture of', 'real picture of', 'look like', 'image', 'shot', 'wallpaper', 'render', 'pics', 'pic', 'capture'])
    needs_search = any(kw in p for kw in ['search', 'weather', 'stock', 'news', 'find', 'lookup', 'research', 'browse', 'who is', 'what is the price of'])
    needs_email = any(kw in p for kw in ['email', 'send', 'sent', 'dispatch', 'mail', 'forward', 'admin_key_provided', 'to him', 'to her', 'to them', 'tell him', 'tell her', 'tell them', 'message him', 'message her'])
    needs_mystic = any(kw in p for kw in ['horoscope', 'palm', 'zodiac', 'astrology'])
    
    # Sensitivity check across current prompt AND recent history
    history_text = ""
    if history:
        history_text = " ".join([(m.get("content") or m.get("c") or "").lower() for m in history[-5:]])
    is_sensitive = any(kw in p or kw in history_text for kw in ['mental health', 'medical diagnosis', 'suicide', 'depressed', 'anxiety therapy', 'clinical treatment', 'legal advice'])
    
    # Complexity Classification: swarm (delegation) vs single (one agent) vs direct (no tools)
    if needs_email:
        # Email requires Manager→Secretary delegation chain
        logger.debug("Intent Fast-Tracked (Complexity: swarm — email/delegation)")
        # FIX: Force email tasks to Cloud engine. Local models hallucinate tool names.
        return {"is_sensitive": is_sensitive, "requires_tools": not is_sensitive, "complexity": "swarm", "is_local": False}
    
    if needs_visual or needs_search or needs_mystic:
        # Single tool call — Generalist can handle alone, no delegation needed
        logger.debug("Intent Fast-Tracked (Complexity: single — visual/search/mystic)")
        return {"is_sensitive": is_sensitive, "requires_tools": not is_sensitive, "complexity": "single", "is_local": target_model != "agentic-pro"}

    # 2. Heuristic fallback — no tools needed, direct LLM conversation
    return {"is_sensitive": is_sensitive, "requires_tools": False, "complexity": "direct", "is_local": target_model != "agentic-pro"}



def _assemble_context(user_prompt, img_data, history, intent, user_id=None, status_callback=None):
    """Stage 2: Merge Vision, Neural Memory (RAG), and Conversation History (Parallelized)."""
    
    # 1. Vision Logic (defined as a sub-task for parallel execution)
    def task_vision():
        if status_callback: status_callback("👁️ Analyzing Visual Context...")
        logger.debug("task_vision started")
        image_reference_keywords = ['this', 'that', 'image', 'picture', 'photo', 'look', 'see', 'describe', 'analyze', 'what is', 'tell me about', 'color', 'colour', 'who', 'where', 'context']
        is_referring_to_image = any(kw in user_prompt.lower() for kw in image_reference_keywords)
        
        img_desc = "No image context available."
        prompt_with_img = user_prompt

        if img_data:
            local_url = save_uploaded_image(img_data)
            if local_url:
                prompt_with_img = f"![Uploaded Image]({local_url})\n{user_prompt}"
            
            if is_referring_to_image:
                if not intent["is_local"]:
                    img_desc = process_image_cloud(img_data, get_next_groq_key()) or process_image_local(img_data)
                else:
                    vision_result = vision_sys.analyze_chat_images([img_data], user_prompt)
                    img_desc = vision_result["description"] if vision_result else process_image_local(img_data)
                return f"--- YOUR VISUAL PERCEPTION ---\nDescription of image: {img_desc}\n--- END VISUAL PERCEPTION ---\n\n{user_prompt}", img_desc

        elif is_referring_to_image and history:
            all_img_urls = []
            for msg in reversed(history):
                content = msg.get("content", msg.get("c", ""))
                matches = re.findall(r'!\[.*?\]\((https?://.*?|/static/.*?|/api/image_proxy.*?)\)', content)
                if matches:
                    all_img_urls.extend(reversed(matches))
                    if len(all_img_urls) >= 3: break 
            
            if all_img_urls:
                generic_queries = ["how does the image look", "describe it", "what is this", "tell me about it", "look at this", "this", "what is that", "tell me about the image", "in the picture", "in the image"]
                target_urls = [all_img_urls[0]] if any(q in user_prompt.lower() for q in generic_queries) else all_img_urls
                
                vision_result = vision_sys.analyze_chat_images(target_urls, user_prompt)
                if vision_result:
                    img_desc = vision_result["description"]
                    return f"--- CURRENT VISUAL FOCUS ---\nImage: {vision_result['url']}\nActual Content: {img_desc}\n--- END VISUAL FOCUS ---\n\n{user_prompt}", img_desc
        
        return prompt_with_img, img_desc

    # 2. Memory Logic (sub-task for parallel execution)
    def task_memory():
        if status_callback: status_callback("🧠 Accessing Neural Memory...")
        logger.debug("task_memory started")
        mem_filter = None
        if any(kw in user_prompt.lower() for kw in ["decide", "decision", "architecture", "plan", "why did"]):
            mem_filter = {"type": "insight"}
        elif any(kw in user_prompt.lower() for kw in ["code", "function", "file", "logic"]):
            mem_filter = {"type": "code"}

        semantic_memories = query_memory(user_prompt, n_results=5, filter_dict=mem_filter, threshold=0.65, user_id=user_id)
        if semantic_memories:
            return "\n<neural_context>\n" + "".join([f"- {m['content']}\n" for m in semantic_memories]) + "</neural_context>\n"
        return ""

    # Execute Parallel Swarm
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_vision = executor.submit(task_vision)
        future_memory = executor.submit(task_memory)
        
        final_prompt, image_description = future_vision.result()
        memory_block = future_memory.result()

    # 3. History (Ultra-Compact for speed)
    history_context = ""
    if history:
        history_context = "\n<history>\n"
        for msg in history[-15:]:
            r = msg.get('role', msg.get('r', ''))
            role = "U" if r in ['user', 'u'] else "A"
            content = msg.get('content', msg.get('c', '')).strip()
            
            if msg.get('masked', False):
                content = "[MASKED_SECRET]"
            
            # Truncate very long history turns to keep context window clean
            if len(content) > 3000:
                content = content[:3000] + "..."
                
            if content: history_context += f"{role}: {content}\n"
        history_context += "</history>\n"

    # 4. Entity Extraction for Pronoun Resolution
    resolved_email = None
    if history:
        for msg in reversed(history[-15:]):
            content = msg.get('content', msg.get('c', ''))
            # Simple email regex
            emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', content)
            if emails:
                resolved_email = emails[-1]
                break

    return {
        "final_prompt": final_prompt,
        "memory_block": memory_block,
        "history_context": history_context,
        "image_description": image_description,
        "resolved_email": resolved_email
    }

def _harden_result(result, sys_config):
    """Stage 4: Post-processing and strict enforcement."""
    if not result: return result
    
    # Strip prompt leaks
    for marker in ["### System:", "STRICT RULE:", "Your personal goal is:", "Role:", "Goal:", "Backstory:"]:
        if marker in str(result):
            result = str(result).split(marker)[0].strip()
            
    # One-Word Enforcement
    if sys_config and sys_config.get('oneword'):
        words = str(result).split()
        if words:
            return words[0].strip('.,!?;:"\'()[]{}')
            
    return result

# FIX #5: Thread-safe callback registry using threading.local instead of global dict
import threading as _cb_threading
_status_callback_local = _cb_threading.local()

class AgentFastExit(BaseException):
    """Custom signal to force-terminate an agentic loop upon successful tool execution."""
    def __init__(self, result):
        self.result = result

def global_step_callback(step):
    """Top-level, picklable callback function for CrewAI."""
    try:
        # Check for early abort signal
        abort_event = getattr(_status_callback_local, 'abort_event', None)
        if abort_event and abort_event.is_set():
            logger.warning("[Agents] Abort signal detected in step callback. Raising exception to stop Crew.")
            raise RuntimeError("Operation cancelled or timed out.")

        callback = getattr(_status_callback_local, 'active', None)
        if callback:
            tool = getattr(step, "tool", None)
            
            # FLAW 4 FIX: Fast exit on Tool Success to prevent 'Overthinking'
            # We check result, tool_output, and raw_output to support multiple CrewAI versions
            tool_output = getattr(step, "result", getattr(step, "tool_output", getattr(step, "raw_output", None)))
            
            if tool_output and "LIVE SUCCESS" in str(tool_output):
                logger.info(f"[Agents] Tool '{tool}' reported LIVE SUCCESS. Triggering AgentFastExit.")
                raise AgentFastExit(str(tool_output))

            if not tool:
                callback("Neural brain processing...")
                return

            status_map = {
                "web_search_text": "🔍 Scouring the web for real-time data...",
                "recall_memory": "🧠 Diving into my semantic memory...",
                "archive_insight": "💾 Archiving new technical insights...",
                "send_email_tool": "📧 Drafting and dispatching your email...",
                "calculate_horoscope": "✨ Consulting the digital stars...",
                "analyze_palm_lines": "✋ Reading the visual patterns...",
                "image_search_tool": "🔍 Searching for real-world images...",
                "image_generate_tool": "🎨 Generating creative visual..."
            }
            msg = status_map.get(tool, f"Executing: {tool}...")
            callback(msg)
    except AgentFastExit:
        raise # Critical for Fast-Exit
    except Exception as e:
        if "Operation cancelled" in str(e): raise e
        print(f"DEBUG: Step callback error: {e}")

def _execute_cloud(intent, context_data, target_model, sys_config, history, status_callback=None, chunk_callback=None, abort_event=None):
    """Stage 3a: Dispatch to Groq/Cloud Engine."""
    target_key = get_next_groq_key()
    if status_callback:
        _status_callback_local.active = status_callback
    if abort_event:
        _status_callback_local.abort_event = abort_event

    # PROACTIVE SECURITY CHECK: If task involves email but no key is present, fail fast.
    prompt_scan = context_data.get("final_prompt", "").lower()
    if intent["requires_tools"] and any(kw in prompt_scan for kw in ["email", "mail", "send"]):
        from app.logic.memory import admin_auth_context, user_context
        import sqlite3
        from app.database import DB_FILE
        
        auth_ok = admin_auth_context.get()
        if not auth_ok:
            try:
                with sqlite3.connect(DB_FILE) as conn:
                    row = conn.execute("SELECT admin_authorized FROM users WHERE email=?", (user_context.get(),)).fetchone()
                    auth_ok = row and row[0]
            except: pass

        if not auth_ok:
            return "ERROR: AUTH_REQUIRED. Please provide your Admin Key in the next message (use the Masked icon) to authorize sending emails."
    
    # Standard Cloud Task
    try:
        if intent["requires_tools"]:
            entity_hint = f"\nRESOLVED ENTITY: If the user says 'send to him/her', use this email: {context_data['resolved_email']}\n" if context_data.get('resolved_email') else ""
            grounding = f"GROUNDING CONTEXT:\n{context_data['memory_block']}\n{context_data['history_context']}{entity_hint}\nSTRICT MANDATE: If the user intent involves 'send', 'search', 'draw', or 'archive', you MUST call the corresponding tool for the CURRENT request. Use web_search_text for text info, image_search_tool for real photos, and image_generate_tool for creative art. FIDELITY: If the user provided a block of technical text, you MUST pass it verbatim to the 'raw_attachment_text' parameter of the send_email_tool. BROADCAST: When sending to multiple recipients, write the 'body' WITHOUT any salutation (no 'Hi', 'Dear'); start directly with the content. The tool handles personalization."
            
            if intent.get("complexity") == "swarm":
                # Full Hierarchical Swarm — Manager delegates to Secretary/Developer
                developer, secretary, manager, _ = get_agent_swarm(target_model, target_key, force_no_tools=False, sys_config=sys_config)
                main_task = Task(
                    description=f'Respond to: "{context_data["final_prompt"]}"\n\n{grounding}', 
                    expected_output="A final summary of the task result or a direct answer.", 
                    agent=manager
                )
                try:
                    return _extract_crew_result(Crew(agents=[developer, secretary, manager], tasks=[main_task], step_callback=global_step_callback))
                except AgentFastExit as e:
                    return e.result
            else:
                # Single-Agent Fast Path — Generalist handles visual/search/mystic alone
                logger.debug(f"Cloud Single-Agent Fast Path (complexity: {intent.get('complexity')})")
                _, _, _, generalist = get_agent_swarm(target_model, target_key, force_no_tools=False, sys_config=sys_config)
                fast_task = Task(
                    description=f'Execute: "{context_data["final_prompt"]}"\n\n{grounding}',
                    expected_output="The raw result of the tool call or a direct response.",
                    agent=generalist
                )
                try:
                    return _extract_crew_result(Crew(agents=[generalist], tasks=[fast_task], step_callback=global_step_callback))
                except AgentFastExit as e:
                    return e.result
        
        # Direct Call for Speed (Grounded with larger context)
        import litellm
        system_prompt = (
            "You are 'The All Time Helper', a high-capability AI assistant and professional software architect. "
            "You are helpful, technical, and proactive. If you see context from <neural_context> or 'VISUAL CONTEXT', integrate it into your response naturally. "
            "CRITICAL: Never claim you cannot perform actions like sending emails or searching if you are in a standard conversation; instead, provide the best possible information or acknowledge the user's intent. "
            "Maintain a premium, sophisticated tone at all times."
        )
        messages = [{"role": "system", "content": system_prompt}]
        if context_data["memory_block"]: 
            messages.append({"role": "system", "content": f"NEURAL MEMORY (Long-term Context):\n{context_data['memory_block']}"})
        
        if history:
            # INCREASED WINDOW: 30 messages
            for msg in history[-30:]:
                role = "user" if str(msg.get("role")).lower() in ["user", "u", "human"] else "assistant"
                messages.append({"role": role, "content": msg.get("content", "")})
        
        messages.append({"role": "user", "content": context_data["final_prompt"]})
        
        if chunk_callback:
            logger.debug("Starting Character Streaming (Cloud)")
            res = litellm.completion(model=f"groq/llama-3.3-70b-versatile", messages=messages, api_key=target_key, stream=True)
            full_response = ""
            for chunk in res:
                content = chunk.choices[0].delta.content
                if content:
                    full_response += content
                    chunk_callback(content)
            return full_response
        else:
            res = litellm.completion(model=f"groq/llama-3.3-70b-versatile", messages=messages, api_key=target_key)
            return res.choices[0].message.content
    except Exception as e:
        return f"Cloud Engine Error: {str(e)}"

def _execute_local(intent, context_data, target_model, sys_config, history, status_callback=None, chunk_callback=None, abort_event=None):
    """Stage 3b: Dispatch to Ollama/Local Engine."""
    if status_callback:
        _status_callback_local.active = status_callback
    if abort_event:
        _status_callback_local.abort_event = abort_event

    # PROACTIVE SECURITY CHECK: If task involves email but no key is present, fail fast.
    prompt_scan = context_data.get("final_prompt", "").lower()
    if intent["requires_tools"] and any(kw in prompt_scan for kw in ["email", "mail", "send"]):
        from app.logic.memory import admin_auth_context, user_context
        import sqlite3
        from app.database import DB_FILE
        
        auth_ok = admin_auth_context.get()
        if not auth_ok:
            try:
                with sqlite3.connect(DB_FILE) as conn:
                    row = conn.execute("SELECT admin_authorized FROM users WHERE email=?", (user_context.get(),)).fetchone()
                    auth_ok = row and row[0]
            except: pass

        if not auth_ok:
            return "ERROR: AUTH_REQUIRED. Please provide your Admin Key in the next message (use the Masked icon) to authorize sending emails."

    try:
        if intent["requires_tools"]:
            logger.debug(f"STARTING LOCAL TOOL EXECUTION (Model: {target_model})")
            dev, sec, mgr, gen = get_agent_swarm(target_model, None, force_no_tools=False, sys_config=sys_config)
            
            # Use Generalist for local tasks as it has all tools directly (prevents expensive delegation loops)
            local_task = Task(
                description=(
                    f"Action: Execute the user request using the appropriate tool if needed.\n"
                    f"Current Request: {context_data.get('final_prompt', '')}\n\n"
                    f"Conversation History (For context/retries):\n{context_data.get('history_context', '')}\n\n"
                    f"Grounding Memory:\n{context_data.get('memory_block', '')}\n\n"
                    f"FIDELITY: If the user provided a block of technical text, you MUST pass it verbatim to the 'raw_attachment_text' parameter of the send_email_tool.\n"
                    f"BROADCAST: When sending to multiple recipients, write the 'body' WITHOUT any salutation (no 'Hi', 'Dear'); start directly with the content. The tool handles personalization.\n"
                    f"STRICT RULE: If the user just provided an Admin Key, find the previous failed tool request in history and execute it now using the EXACT tool name 'send_email_tool'."
                ),
                expected_output="The output of the tool execution (e.g., SUCCESS) or a final helpful answer.",
                agent=gen
            )
            try:
                return _extract_crew_result(Crew(agents=[gen], tasks=[local_task], step_callback=global_step_callback))
            except AgentFastExit as e:
                return e.result

        # Direct Local Chat
        system_prompt = (
            "You are 'The All Time Helper', a high-capability AI assistant. "
            "You are technical, proactive, and elite. Integrate <neural_context> or 'VISUAL CONTEXT' if provided. "
            "CRITICAL: Always act as a capable assistant. Do not lecture the user on privacy or limitations. Maintain a sophisticated, helpful tone."
        )
        messages = [{"role": "system", "content": system_prompt}]
        if context_data["memory_block"]: messages.append({"role": "system", "content": context_data["memory_block"]})
        # INCREASED WINDOW: 20 messages for local (to save GPU RAM while staying relevant)
        if history:
            # ALIGNED WINDOW: Last 10 turns
            for msg in history[-10:]:
                role = "user" if msg.get("role") in ["user", "u", "Human"] else "assistant"
                content = msg.get("content", "").strip()
                if msg.get("masked"): content = "[MASKED_SECRET]"
                if len(content) > 3000: content = content[:3000] + "..." # Truncate for speed
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": context_data["final_prompt"]})
        
        payload = {"model": target_model, "messages": messages, "stream": True if chunk_callback else False}
        
        if chunk_callback:
            logger.debug(f"Starting Character Streaming (Model: {target_model})")
            res = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, stream=True, timeout=120, verify=False)
            full_response = ""
            for line in res.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line.decode('utf-8'))
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            full_response += content
                            chunk_callback(content)
                        if chunk.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
            return full_response
        else:
            res = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120, verify=False)
            return res.json().get("message", {}).get("content", "Error parsing response.")
    except Exception as e:
        logger.warning(f"Local Engine Timeout/Error ({str(e)}). Attempting Cloud Fallback...")
        # Emergency Fallback to Cloud if Local is stalling
        return _execute_cloud(intent, context_data, "agentic-pro", sys_config, history)

def run_helper_agent(user_prompt: str, img_data: str = None, target_model: str = "agentic-pro", sys_config: dict = None, history: List[dict] = None, persona: bool = False, abort_event: Any = None, user_id: str = None, status_callback=None, chunk_callback=None):
    """Orchestrates the specialized agents via a decoupled modular pipeline."""
    
    # 0. Check for early abort
    if abort_event and abort_event.is_set():
        return "Operation cancelled."

    # FLAW 2 FIX: Countermeasure 2 — email_send_log Idempotency Check
    if any(kw in user_prompt.lower() for kw in ["send email", "send an email", "retry send", "admin key"]):
        from app.database import DB_FILE
        import sqlite3
        try:
            with sqlite3.connect(DB_FILE) as conn:
                conn.row_factory = sqlite3.Row
                # Check for recent successful sends (last 10 mins)
                recent = conn.execute(
                    "SELECT * FROM email_send_log WHERE user_email=? AND timestamp > ? ORDER BY timestamp DESC LIMIT 1",
                    (user_id, time.time() - 600)
                ).fetchone()
                if recent:
                    logger.info(f"[Agents] Idempotency hit for {user_id}. Email already sent at {recent['timestamp']}.")
                    return f"ALREADY SENT: Email was dispatched to {recent['recipients']} (Job: {recent['job_id']}). Skipping duplicate."
        except Exception as e:
            logger.warning(f"[Agents] Idempotency check failed: {e}")

    # 1. Context Reconstruction (Harden against ambiguous prompts like '1.' or 'it')
    user_prompt = _reconstruct_contextual_prompt(user_prompt, history)

    # 2. Intent Detection
    intent = _detect_intent(user_prompt, target_model, history)
    
    # 3. Routing Adjustments
    if persona or (intent["is_sensitive"] and target_model != "helper"):
        target_model = "helper"

    # 4. Context Assembly
    context_data = _assemble_context(user_prompt, img_data, history, intent, user_id=user_id, status_callback=status_callback)

    # 5. Engine Execution
    if abort_event and abort_event.is_set(): return "Operation cancelled."
    
    if not intent["is_local"] and target_model == "agentic-pro":
        result = _execute_cloud(intent, context_data, target_model, sys_config, history, status_callback=status_callback, chunk_callback=chunk_callback, abort_event=abort_event)
    else:
        result = _execute_local(intent, context_data, target_model, sys_config, history, status_callback=status_callback, chunk_callback=chunk_callback, abort_event=abort_event)

    # 6. Result Hardening
    return _harden_result(result, sys_config)

def ask_the_helper(prompt: str, img_data: str = None, target_model: str = "agentic-pro", sys_config: dict = None, history: List[dict] = None, persona: bool = False, abort_event: Any = None, user_id: str = None, status_callback=None, chunk_callback=None):
    return run_helper_agent(prompt, img_data, target_model, sys_config, history, persona, abort_event, user_id, status_callback=status_callback, chunk_callback=chunk_callback)
