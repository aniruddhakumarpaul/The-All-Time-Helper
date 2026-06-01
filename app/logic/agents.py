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

# --- Intent Classification Keyword Constants (Single source of truth) ---
# FIX: Extracted from _detect_intent, _execute_cloud, _execute_local, chat.py to eliminate DRY violation
CODE_KEYWORDS = ['code', 'bug', 'logic', 'python', 'javascript', 'html', 'css', 'develop', 'compile', 'debug', 'git', 'refactor', 'function', 'class']
VISUAL_KEYWORDS = ['draw', 'paint', 'sketch', 'scetch', 'generate', 'create', 'artwork', 'photo of', 'show me a picture of', 'real picture of', 'look like', 'image', 'shot', 'wallpaper', 'render', 'pics', 'pic', 'capture', 'acrylic', 'acrilic', 'drawing', 'drawin', 'painting', 'panting', 'illustration', 'portrait', 'potrait', 'canvas', 'sketching']
EMAIL_KEYWORDS = ['email', 'send', 'sent', 'dispatch', 'mail', 'forward', 'admin_key_provided', 'to him', 'to her', 'to them', 'tell him', 'tell her', 'tell them', 'message him', 'message her']

# API Key Rotation Pool (Thread-safe)
GROQ_KEYS = [os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_BACKUP")]
GROQ_KEYS = [k for k in GROQ_KEYS if k]
_key_index = 0
_key_lock = threading.Lock()

CLOUD_MODEL_CONFIG = {
    "agentic-pro": {
        "provider": "groq",
        "model": "groq/llama-3.3-70b-versatile",
        "classifier_model": "groq/llama-3.1-8b-instant",
        "key_envs": ("GROQ_API_KEY",),
    },
    "gemma4-cloud": {
        "provider": "groq",
        "model": "groq/llama-3.1-8b-instant",
        "classifier_model": "groq/llama-3.1-8b-instant",
        "key_envs": ("GROQ_API_KEY",),
    },
    "gemma4-openrouter": {
        "provider": "openrouter",
        "model": "openrouter/google/gemma-4-26b-a4b-it:free",
        "classifier_model": "openrouter/google/gemma-4-26b-a4b-it:free",
        "fallback_models": (
            "openrouter/google/gemma-4-31b-it:free",
            "openrouter/google/gemma-3-27b-it",
            "openrouter/google/gemma-3-12b-it",
        ),
        "key_envs": ("OPENROUTER_API_KEY",),
    },
    "gemini-1.5-flash-latest": {
        "provider": "gemini",
        "model": "gemini/gemini-1.5-flash-latest",
        "classifier_model": "gemini/gemini-1.5-flash-latest",
        "key_envs": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    },
    "gemini-1.5-pro-latest": {
        "provider": "gemini",
        "model": "gemini/gemini-1.5-pro-latest",
        "classifier_model": "gemini/gemini-1.5-flash-latest",
        "key_envs": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    },
}

def get_next_groq_key():
    global _key_index
    if not GROQ_KEYS: return None
    with _key_lock:
        key = GROQ_KEYS[_key_index % len(GROQ_KEYS)]
        _key_index += 1
    return key


def _is_cloud_model(model_id: str) -> bool:
    return model_id in CLOUD_MODEL_CONFIG


def _get_cloud_config(model_id: str) -> dict:
    if model_id not in CLOUD_MODEL_CONFIG:
        raise ValueError(f"Unknown cloud model '{model_id}'.")
    return CLOUD_MODEL_CONFIG[model_id]


def _get_cloud_api_key(model_id: str, explicit_key: str = None) -> str:
    cfg = _get_cloud_config(model_id)
    if explicit_key:
        return explicit_key
    if cfg["provider"] == "groq":
        key = get_next_groq_key()
        if key:
            return key
    for env_name in cfg["key_envs"]:
        key = os.getenv(env_name)
        if key:
            return key
    env_display = " or ".join(cfg["key_envs"])
    raise ValueError(f"{env_display} missing - required for {model_id}.")


def _cloud_candidate_models(cfg: dict) -> list:
    models = [cfg["model"]]
    for model in cfg.get("fallback_models", ()):
        if model not in models:
            models.append(model)
    return models


def _is_rate_limit_error(error: Exception) -> bool:
    message = str(error).lower()
    return "rate_limit" in message or "rate limit" in message or "429" in message or "too many requests" in message


def _cloud_rate_limit_message(model_id: str) -> str:
    if model_id == "gemma4-openrouter":
        return (
            "Cloud Engine Error: Gemma 4 Cloud is temporarily rate limited by OpenRouter's upstream provider. "
            "Please retry shortly, use another cloud model, or add your own Google AI Studio key in OpenRouter Integrations to increase quota."
        )
    return "Cloud Engine Error: The selected cloud model is temporarily rate limited. Please retry shortly or choose another model."


def clean_user_prompt(prompt: str) -> str:
    """Removes attached context blocks from the prompt to get the actual user-typed query."""
    if not prompt:
        return ""
    # Regex to match [Attached Context N] followed by triple quotes and their content
    pattern = r'\[Attached Context \d+\]\s*"""[\s\S]*?"""'
    cleaned = re.sub(pattern, '', prompt)
    return cleaned.strip()


def _normalize_prompt_for_intent(prompt: str) -> str:
    """Light normalization used only for intent classification."""
    cleaned = clean_user_prompt(prompt)
    if _looks_like_structured_technical_text(cleaned):
        return _preserve_structured_text(cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _preserve_structured_text(text: str) -> str:
    """Keep multiline content readable while trimming only outer whitespace."""
    if not text:
        return ""
    lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def _looks_like_structured_technical_text(text: str) -> bool:
    """Heuristic to detect pasted code, logs, JSON, or syntax-heavy text."""
    if not text:
        return False

    raw = str(text)
    lower = clean_user_prompt(raw).lower()

    if "```" in raw or "<?php" in lower:
        return True

    lines = [line for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
    code_markers = [
        r'^\s*import\s+\w+',
        r'^\s*from\s+\w+\s+import\s+',
        r'^\s*def\s+\w+\s*\(',
        r'^\s*class\s+\w+\s*[:\(]',
        r'^\s*(const|let|var)\s+\w+',
        r'^\s*function\s+\w+\s*\(',
        r'^\s*#include\b',
        r'^\s*using\s+\w+',
        r'^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE)\b',
    ]
    for line in lines[:12]:
        if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in code_markers):
            return True

    punct_density = sum(ch in "{}[]();=<>\\" for ch in raw) / max(len(raw), 1)
    if len(lines) >= 4 and punct_density > 0.03:
        return True

    if len(lines) >= 3:
        indented_lines = sum(1 for line in lines if line.startswith(" ") or line.startswith("\t"))
        if indented_lines >= 2:
            return True

    return False


# Helper to keep code DRY
def _strip_base64_prefix(img_base64: str) -> str:
    return img_base64.split(",")[1] if img_base64 and "," in img_base64 else img_base64

def _extract_crew_result(crew: Crew) -> str:
    res = crew.kickoff()
    return getattr(res, 'raw', str(res)) if hasattr(res, 'raw') else str(res)

_llm_cache = {}
_llm_cache_lock = threading.Lock()

def get_llm(model_id="gemma4:e2b", api_key=None, model_override=None):
    """Factory to get the right LLM brain based on the user's selection, cached to prevent latency."""
    
    if not model_id:
        model_id = "gemma4:e2b"

    # CASE 1: Cloud Models
    target_key = None
    if _is_cloud_model(model_id):
        target_key = _get_cloud_api_key(model_id, api_key)
        
    cache_key = (model_id, target_key, model_override)
    with _llm_cache_lock:
        if cache_key in _llm_cache:
            return _llm_cache[cache_key]

    if _is_cloud_model(model_id):
        cfg = _get_cloud_config(model_id)
        llm_inst = LLM(
            model=model_override or cfg["model"],
            temperature=0.3,
            api_key=target_key
        )
    else:
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

        llm_inst = LLM(
            model=f"ollama/{model_id}",
            base_url=ollama_url,
            temperature=0.2
        )

    with _llm_cache_lock:
        _llm_cache[cache_key] = llm_inst
    return llm_inst

# FIX #5: Thread-safe callback registry using ContextVar instead of threading.local
from contextvars import ContextVar
active_status_callback: ContextVar[Optional[Any]] = ContextVar("active_status_callback", default=None)
active_abort_event: ContextVar[Optional[Any]] = ContextVar("active_abort_event", default=None)

from app.logic.exceptions import AgentFastExit


def global_step_callback(step):
    """Top-level, picklable callback function for CrewAI."""
    try:
        # Check for early abort signal
        abort_event = active_abort_event.get()
        if abort_event and abort_event.is_set():
            logger.warning("[Agents] Abort signal detected in step callback. Raising exception to stop Crew.")
            raise RuntimeError("Operation cancelled or timed out.")

        # FLAW 4 FIX: Fast exit on Tool Success to prevent 'Overthinking'
        # We check result, tool_output, and raw_output to support multiple CrewAI versions
        tool = getattr(step, "tool", None)
        tool_output = getattr(step, "result", getattr(step, "tool_output", getattr(step, "raw_output", None)))
        
        if tool_output and ("LIVE SUCCESS" in str(tool_output) or "EMAIL_DRAFT_PAYLOAD" in str(tool_output) or "SIMULATE SUCCESS" in str(tool_output) or ("![" in str(tool_output) and "](" in str(tool_output))):
            logger.info(f"[Agents] Tool '{tool}' reported success/draft/image payload. Triggering AgentFastExit.")
            
            # FLAW 1 FIX: Store tool output in the ToolResultBus so the queue can retrieve it
            from app.logic.bus import tool_result_bus, job_id_context
            jid = job_id_context.get()
            if jid:
                logger.info(f"[Agents] Storing tool output in bus for job {jid}")
                tool_result_bus.set_result(jid, str(tool_output))
                
            raise AgentFastExit(str(tool_output))

        callback = active_status_callback.get()
        if callback:
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

# --- AGENT DEFINITIONS (created fresh per-request to use current env vars) ---

def _build_agents(llm, use_tools=True, sys_config=None):
    """Internal factory to create agents with an LLM instance. Omit tools if model doesn't support them."""
    
    # Tool assignment based on capability
    dev_tools = [tools.search_tool, tools.recall_memory, tools.archive_insight] if use_tools else []
    sec_tools = [tools.send_email_tool] if use_tools else []
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
        max_iter=1 if getattr(llm, 'is_weak', False) else 3,
        step_callback=global_step_callback
    )

    # 2. The Secretary (Email & Comms)
    selected_tone = sys_config.get('email_tone', '') if sys_config else ''
    tone_instruction = f" You MUST use the tone '{selected_tone}' when calling send_email_tool." if selected_tone else " Detect the appropriate `tone` parameter for `send_email_tool`: 'formal' for office/business, 'informal' for friends/family, or 'modern' for general/ambiguous."
    
    secretary = Agent(
        role='Senior Executive Secretary',
        goal=(
            f"STRICT RULE: You are a high-fidelity drafting and dispatch engine. "
            f"1. ATTACHMENTS & GENERATION VS RETRIEVAL: Only include `attachment_content` if requested. "
            f"If the user asks to 'generate', 'create', 'draw' or 'paint' an image and attach it, you MUST ensure that a new image is generated first (if tool is available). NEVER pass a text description of a to-be-generated image (like 'house image') directly to `send_email_tool` without generating it first. "
            f"If you are attaching a newly generated image, pass the full markdown image tag (e.g. `![alt](url)`) or the URL directly as the `attachment_content` parameter. "
            f"ONLY pass a text description (like 'house image') to `chat_image_reference` when the user did NOT ask to generate a new image, which tells the tool to safely search and attach an existing image from the past chat history. "
            f"2. TONE SELECTION:{tone_instruction} "
            f"3. JSON ESCAPING: When invoking any tool, you MUST strictly escape nested double quotes (\") in string parameters (e.g. change `\"Monolith\"` to `\\\"Monolith\\\"`) or convert them to single quotes (`'Monolith'`). NEVER write unescaped double quotes inside tool arguments. "
            f"4. CONTENT: You are an author. If the user asks for a joke, write a funny one. If they ask for a greeting, make it warm. NEVER repeat the user prompt as the email body. NO PLACEHOLDERS.{persona_suffix}"
        ),
        backstory=(
            "You are an elite executive assistant who understands professional nuance. "
            "You distinguish between a casual joke to a friend and a strict formal broadcast to office members. "
            "You are conservative with attachments—you only add them when it adds value or is requested. "
            "You ensure every email represents the user's intent with 100% fidelity. "
            "Crucially, you ensure all tool call arguments are valid JSON, strictly escaping nested double quotes or converting them to single quotes."
        ),
        tools=sec_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=1 if getattr(llm, 'is_weak', False) else 3,
        step_callback=global_step_callback
    )
 
    # 3. Creative Visual Artist Agent
    artist = Agent(
        role='Creative Visual Artist',
        goal=f'Search or generate images, design interfaces, and provide aesthetic/artistic/visual guidance.{persona_suffix}',
        backstory=f'''You are an elite digital artist and graphic designer. You specialize in selecting and creating the perfect visual content. You use the image generation and image search tools to produce state of the art visual art.{persona_suffix}''',
        tools=visual_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=1 if getattr(llm, 'is_weak', False) else 3,
        step_callback=global_step_callback
    )
 
    # Shared prompt base for Manager and Generalist (FIX #10: DRY)
    _visual_golden_rule = f'''STRICT RULE: ACT FIRST, NEVER ASK FIRST for Visuals.
        ## IMAGE & VISUAL REQUEST HANDLING GOLDEN RULE
        - image_search_tool -> for REAL things (products, people, places, animals, vehicles, brands, etc.)
        - image_generate_tool -> for FICTIONAL, CONCEPTUAL, or CREATIVE things (fantasy art, concepts, abstract visuals)
        If a user asks for any visual ("photo of X", "show me X", "generate X", "picture of X"), you MUST CALL THE TOOL IMMEDIATELY. NEVER ask for clarification or confirmation. NEVER respond with text alone.
        
        ## EMAIL ATTACHMENT FOR NEW IMAGES VS EXISTING IMAGES
        - If the user asks to "generate" or "create" or "draw" or "paint" an image (e.g., "generate a house image and attach it"), you MUST call the `image_generate_tool` FIRST to generate a new image. You must NEVER pass the image description (like 'house image') as the `attachment_content` or `chat_image_reference` of `send_email_tool` without generating it first, as that will fail to create a new image and instead attach a stale one from history.
        - NEVER call `image_generate_tool` and `send_email_tool` in the same step/turn (in parallel). You MUST call `image_generate_tool` first, wait for the actual output markdown tag containing the URL, and only then call `send_email_tool` in a subsequent step using that actual URL. Never fabricate placeholder URLs like `https://example.com/house.png`.
        - If you generated a NEW image in this turn, pass its full markdown tag (e.g. `![description](url)`) or its URL directly as the `attachment_content` parameter of `send_email_tool`.
        - ONLY use `chat_image_reference` or text descriptions in `attachment_content` when the user does NOT use the word "generate/create/draw/paint" (meaning they explicitly want to attach a past image that is already in the chat history).
        
        ## TOOL EXECUTION & AUTH
        - If a tool result is present (e.g. an image tag ![alt](url) or a 'LIVE SUCCESS' message), your mission is DONE. Present the result immediately.
        - Only if you see 'ERROR: AUTH_REQUIRED' should you ask for the 'Admin Key'. Explain the 'Masked' prompt feature for privacy.
        - When providing multiple options, ALWAYS use a numbered list format (1., 2., etc.).
        
        ## JSON ESCAPING & STRING FORMATTING IN TOOL CALLS
        - When invoking any tool, you MUST ensure that all string arguments are valid JSON.
        - NEVER output unescaped double quotes (") inside tool call arguments.
        - Convert any nested double quotes in the source text to single quotes (') or escape them strictly as \\". For example, change: The "Monolith" -> The 'Monolith'.{persona_suffix}'''

    # 4. The All Time Helper (Manager)
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
        max_iter=1 if getattr(llm, 'is_weak', False) else 3,
        step_callback=global_step_callback
    )

    # 5. Expert System Assistant (The "Helper") — shares Golden Rule with Manager
    generalist = Agent(
        role='The All Time Helper',
        goal=f"{_visual_golden_rule}\n\n## DRAFTING RULE\nIf the email tool is needed, DRAFT the content creatively (jokes, greetings, official letters). NEVER just repeat the user's instructions as the email body. You are an elite executive author, not a typewriter.",
        backstory=f'''You are an extremely compliant creative assistant. You prioritize results over conversation. If a tool works, you provide the output immediately without redundant clarification.{persona_suffix}''',
        tools=dev_tools + visual_tools + mem_tools,  # FIX: Removed sec_tools — email should route through secretary, not generalist
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=2 if getattr(llm, 'is_weak', False) else 5,
        step_callback=global_step_callback
    )

    return developer, secretary, artist, manager, generalist

def get_agent_swarm(model_id, api_key=None, force_no_tools=False, sys_config=None, model_override=None):
    """Cached factory that builds the entire agent swarm based on model capabilities."""
    llm = get_llm(model_id, api_key, model_override=model_override)
    
    # Force Text-Only mode for models that fail on native tool calling (e.g. legacy Gemma or fine-tuned personas)
    use_tools = True
    model_str = str(model_id).lower()
    is_legacy_gemma = "gemma" in model_str and "gemma4" not in model_str
    
    if is_legacy_gemma or force_no_tools:
        use_tools = False
        logger.debug(f"DEBUG: Setting Text-Only Mode (Model: {model_id}, Reason: {'Legacy' if is_legacy_gemma else 'Force'})")
    
    # Tag the LLM object if it's a weak model to adjust iterations later
    llm.is_weak = ("2b" in model_str or "0.5b" in model_str or "1.5b" in model_str) and "e2b" not in model_str
    
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
    """Direct Native Multimodal Analysis using Moondream."""
    try:
        img_base64 = _strip_base64_prefix(img_base64)
        
        payload = {
            "model": "moondream",
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
        
        print("[Vision] Local Native Moondream Analysis started...", flush=True)
        # EXTENDED TIMEOUT: 120s for Native VLM processing
        res = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120, verify=False)
        
        if res.status_code == 200:
            return res.json().get("message", {}).get("content", "Vision analysis failed.")
        else:
            return f"Moondream Vision Error {res.status_code}: {res.text}"
    except Exception as e:
        logger.error(f"[Vision] Local Native Moondream Error: {e}", exc_info=True)
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
    if _looks_like_structured_technical_text(user_prompt):
        return user_prompt
    # Normalizer for history formats (handles 'role'/'content' or 'r'/'c')
    def get_msg_data(m):
        r = m.get("role") or m.get("r") or ""
        c = m.get("content") or m.get("c") or ""
        return r.lower(), c

    # Clean the prompt to isolate user-typed message from attached contexts for checking
    clean_p = clean_user_prompt(user_prompt)
    p = clean_p.strip().lower()
    
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
                            logger.debug(f"[Context] Resolved Numeric '{clean_p}' to '{text.strip()}'")
                            return f"[Selection: {text.strip()}] {user_prompt}"
                
                # Strategy B: Bullet points or Line-based options (Header: Description)
                options = [line.strip() for line in content.split('\n') if len(line.strip()) > 10 and (':' in line or line.startswith(('-', '*', '•')))]
                if len(options) >= 2:
                    try:
                        idx = int(requested_num) - 1
                        if 0 <= idx < len(options):
                            text = options[idx].split(':')[0] # Take the header part
                            logger.debug(f"[Context] Resolved Option '{clean_p}' to '{text.strip()}'")
                            return f"[Selection: {text.strip()}] {user_prompt}"
                    except Exception: pass
 
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


def _history_msg_data(message: dict) -> tuple:
    role = (message.get("role") or message.get("r") or "").lower()
    content = message.get("content") or message.get("c") or ""
    return role, content


def _is_visual_context_switch(text: str) -> bool:
    p = clean_user_prompt(text).lower().strip()
    if not p:
        return True

    if re.search(r'[\w\.-]+@[\w\.-]+\.\w+', p):
        return True

    search_terms = ['search', 'weather', 'stock', 'news', 'lookup', 'research', 'browse']
    if any(kw in p for kw in EMAIL_KEYWORDS + CODE_KEYWORDS + search_terms):
        return True

    factual_starters = (
        'what is', 'what are', 'who is', 'who was', 'where is', 'when is',
        'why is', 'why do', 'how to', 'how do', 'how does', 'explain',
        'define', 'summarize', 'tell me about'
    )
    return any(p.startswith(starter) for starter in factual_starters)


def _looks_like_visual_request(text: str) -> bool:
    p = clean_user_prompt(text).lower().strip()
    if not p:
        return False

    visual_anchors = [
        'image', 'picture', 'photo', 'artwork', 'portrait', 'potrait',
        'sketch', 'scetch', 'painting', 'drawing', 'illustration',
        'acrylic', 'acrilic', 'canvas', 'wallpaper', 'visual'
    ]
    visual_actions = [
        'draw', 'paint', 'sketch', 'scetch', 'generate', 'create',
        'make', 'render', 'want', 'need', 'would like'
    ]
    return any(anchor in p for anchor in visual_anchors) and any(action in p for action in visual_actions)


def _is_visual_proceed_signal(text: str) -> bool:
    p = _normalize_visual_continuation_text(text).lower().strip()
    proceed_phrases = [
        'yes', 'yep', 'yeah', 'ok', 'okay', 'go ahead', 'do it',
        'that works', 'perfect', 'proceed', 'use your judgment',
        'use your judgement', 'just do it', 'stop asking',
        'stop asking me', 'stop asking me so many questions',
        'as you see fit'
    ]
    for phrase in proceed_phrases:
        if " " not in phrase:
            if p == phrase:
                return True
        elif re.search(rf'\b{re.escape(phrase)}\b', p):
            return True
    return False


def _looks_like_visual_refinement(text: str) -> bool:
    p = clean_user_prompt(text).lower().strip()
    if not p or _is_visual_context_switch(p):
        return False
    if _is_visual_proceed_signal(p):
        return True

    refinement_terms = [
        'hair', 'gown', 'dress', 'attire', 'clothing', 'pose', 'profile',
        'smile', 'eyes', 'face', 'feminine', 'femininity', 'elegant',
        'elegance', 'beautiful', 'realistic', 'cinematic', 'lighting',
        'color', 'colour', 'palette', 'background', 'scene', 'style',
        'aesthetic', 'vintage', 'modern', 'victorian', 'darker',
        'brighter', 'less', 'more', 'make her', 'make it'
    ]
    if any(term in p for term in refinement_terms):
        return True

    # Short comma-separated descriptive fragments are usually specs when a visual task is active.
    return ',' in p and len(p.split()) <= 18 and not p.endswith('?')


def _normalize_visual_continuation_text(text: str) -> str:
    clean = clean_user_prompt(text).strip()
    replacements = {
        r'\bdo+ as fit for the most elegance\b': 'maximum elegance',
        r'\bacrilic\b': 'acrylic',
        r'\bscetch\b': 'sketch',
        r'\bpotrait\b': 'portrait',
        r'\bpanting\b': 'painting',
        r'\bdrawin\b': 'drawing',
        r'\bbeautifull\b': 'beautiful',
        r'\bdoo\b': 'do',
    }
    for pattern, replacement in replacements.items():
        clean = re.sub(pattern, replacement, clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def _resolve_visual_task_continuation(user_prompt: str, history: list) -> Optional[str]:
    """Builds an explicit image-generation prompt when recent history has an unresolved visual task."""
    if not user_prompt or not history:
        return None

    current = clean_user_prompt(user_prompt).strip()
    if not current or _is_visual_context_switch(current):
        return None

    if _looks_like_visual_request(current):
        return None

    if not (_looks_like_visual_refinement(current) or _is_visual_proceed_signal(current)):
        return None

    normalized_current = _normalize_visual_continuation_text(current).lower()
    recent = list(history[-8:])
    current_already_in_history = False
    if recent:
        last_role, last_content = _history_msg_data(recent[-1])
        if last_role in ["user", "u", "human"]:
            normalized_last = _normalize_visual_continuation_text(last_content).lower()
            if normalized_last == normalized_current:
                current_already_in_history = True
                recent = recent[:-1]

    base_index = None
    for idx in range(len(recent) - 1, -1, -1):
        role, content = _history_msg_data(recent[idx])
        if role in ["user", "u", "human"] and _looks_like_visual_request(content):
            base_index = idx
            break

    if base_index is None:
        return None

    for msg in recent[base_index + 1:]:
        role, content = _history_msg_data(msg)
        if role in ["assistant", "a", "bot"] and re.search(r'!\[[^\]]*\]\([^)]+\)', content):
            return None

    prompt_parts = []
    seen_parts = set()

    def add_prompt_part(text: str):
        part = _normalize_visual_continuation_text(text)
        key = part.lower()
        if part and key not in seen_parts:
            prompt_parts.append(part)
            seen_parts.add(key)

    for msg in recent[base_index:]:
        role, content = _history_msg_data(msg)
        if role not in ["user", "u", "human"]:
            continue
        if _is_visual_context_switch(content):
            return None
        if _looks_like_visual_request(content) or (
            _looks_like_visual_refinement(content) and not _is_visual_proceed_signal(content)
        ):
            add_prompt_part(content)

    if not _is_visual_proceed_signal(current):
        add_prompt_part(current)

    prompt_parts = [part for part in prompt_parts if part]
    if not prompt_parts:
        return None

    reconstructed = "generate an image of " + ". ".join(prompt_parts)
    logger.info(f"[Context] Continuing visual generation task from recent history: '{reconstructed[:180]}'")
    return reconstructed

def _classify_complexity_via_llm(user_prompt: str, target_model: str) -> str:
    """Invokes a fast, structured LLM call to classify the prompt complexity."""
    system_prompt = (
        "You are a task complexity classifier for an agentic helper. "
        "Classify the user's prompt into one of these paths:\n"
        "- 'direct': Simple conversational queries, general questions, explanations of code/concepts, "
        "requests to describe/analyze/explain an uploaded image/file, or basic help. No active tool execution or task execution is needed.\n"
        "- 'single': The query demands a single specific tool action like drawing/generating a brand new image, searching the web for real-time/current information, searching for real images, or writing/modifying code files.\n"
        "- 'swarm': Complex workflows like writing and sending emails.\n\n"
        "Respond with exactly one word from ['direct', 'single', 'swarm']. Nothing else."
    )
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Prompt: {user_prompt}"}
    ]
    
    try:
        if _is_cloud_model(target_model):
            # Cloud fast model call
            import litellm
            cfg = _get_cloud_config(target_model)
            key = _get_cloud_api_key(target_model)
            res = litellm.completion(
                model=cfg["classifier_model"],
                messages=messages,
                api_key=key,
                temperature=0.0,
                max_tokens=5,
                timeout=3.0
            )
            raw = res.choices[0].message.content.strip().lower()
        else:
            # Ollama fast call
            payload = {
                "model": target_model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 5
                }
            }
            res = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=6.0, verify=False)
            res.raise_for_status()
            raw = res.json().get("message", {}).get("content", "").strip().lower()
        
        # Parse output
        for choice in ["direct", "single", "swarm"]:
            if choice in raw:
                logger.info(f"[Intent Classifier] LLM classified complexity as '{choice}' (raw: '{raw}')")
                return choice
    except Exception as e:
        logger.warning(f"LLM Complexity Classification failed: {e}. Falling back to heuristics.")
    
    return "heuristic"


def _detect_complexity_heuristically(user_prompt: str) -> Optional[str]:
    """Determines prompt complexity via fast local rules to save LLM roundtrips."""
    p = user_prompt.lower().strip()
    
    # Strip common conversational/politeness prefixes at the start
    conversational_prefixes = [
        "ok", "okay", "please", "hey", "hello", "hi", 
        "can you", "could you", "would you", "will you",
        "tell me", "show me", "find out", "check", "search for",
        "one thing", "just", "so", "now", "actually"
    ]
    
    p_clean = p
    modified = True
    while modified:
        modified = False
        for prefix in conversational_prefixes:
            if p_clean.startswith(prefix + " ") or p_clean == prefix:
                p_clean = p_clean[len(prefix):].strip()
                modified = True
                break
            elif p_clean.startswith(prefix + ",") or p_clean.startswith(prefix + "."):
                p_clean = p_clean[len(prefix):].strip(",. ").strip()
                modified = True
                break
    
    # Clean ending punctuation
    p_clean = p_clean.rstrip('?.!')
    
    # 1. Very short queries are almost always direct conversation
    if len(p_clean) < 15:
        return "direct"
        
    # 2. Questions about past actions or state (always direct conversational, no new tool execution)
    state_question_starters = ["did you", "why did you", "how did you", "was there", "were you", "have you", "did it"]
    if any(p_clean.startswith(starter) for starter in state_question_starters):
        return "direct"
        
    # Helper to check whole-word prefix match
    def starts_with_word(text, prefix):
        if text == prefix:
            return True
        return text.startswith(prefix + " ") or text.startswith(prefix + ",") or text.startswith(prefix + "?")
        
    direct_starters = [
        "what is", "what are", "how to", "how do", "how can", "how does", "how did", "why do", "why does",
        "why did", "explain", "describe", "summarize", "tell me about", "who is", "who was",
        "where is", "where was", "when is", "when did", "define", "what's", "how's", "is it",
        "can you explain", "could you explain", "do you know", "please explain", "what does",
        "how is", "why is", "what was", "how was", "why was", "tell me", "show me"
    ]
    is_question = any(starts_with_word(p_clean, starter) for starter in direct_starters)
    
    # --- FAST-PATH HEURISTICS FOR ACTIVE TOOLS ---
    # A. Email dispatch/draft commands
    is_email = any(kw in p_clean for kw in ['email', 'mail', 'send', 'draft'])
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', p_clean)
    if is_email and email_match and not is_question:
        needs_code = any(kw in p_clean for kw in CODE_KEYWORDS)
        needs_visual = any(kw in p_clean for kw in VISUAL_KEYWORDS) or bool(re.search(r'\b(generate|create|make|draw|paint|sketch|render)\s+(?:[a-zA-Z]+\s+){0,3}(image|picture|pic|photo|artwork|portrait|wallpaper|scene|illustration)\b', p_clean))
        needs_search = any(kw in p_clean for kw in ['search', 'weather', 'stock', 'news', 'find', 'lookup', 'research', 'browse'])
        if needs_code or needs_visual or needs_search:
            return "swarm"
        return "single"
        
    # B. Image generation/drawing command
    is_generate = (
        any(kw in p_clean for kw in ['draw', 'generate image', 'generate an image', 'create image', 'create an image', 'paint', 'sketch', 'scetch', 'artwork', 'painting', 'drawing', 'acrylic', 'acrilic', 'portrait', 'potrait']) or
        bool(re.search(r'\b(generate|create|make|draw|paint|sketch|scetch|render)\s+(?:[a-zA-Z]+\s+){0,3}(image|picture|pic|photo|artwork|portrait|potrait|wallpaper|scene|illustration)\b', p_clean))
    )
    if is_generate and not is_question and not any(kw in p_clean for kw in ['search image', 'search photo', 'find image', 'find photo']):
        return "single"
        
    # 3. Obvious direct conversational / question starters
    if is_question:
        action_keywords = ["send", "mail", "email", "search", "draw", "generate", "create image", "paint"]
        if not any(kw in p_clean for kw in action_keywords):
            return "direct"

    # 4. Code explanation questions (often start with "how do i write", "example of", etc.)
    code_explain_keywords = ["example of", "tutorial on", "how do i use", "how to write a", "difference between"]
    if any(starts_with_word(p_clean, kw) for kw in code_explain_keywords):
        if not any(kw in p_clean for kw in ["write to file", "modify file", "create file", "save to"]):
            return "direct"
            
    return None

def _analyze_prompt_via_llm(user_prompt: str, target_model: str) -> dict:
    """Uses a fast cloud or local LLM call to analyze the user's prompt for intent, tools, and complexity."""
    system_prompt = (
        "You are a structured prompt analyzer for an agentic helper.\n"
        "Analyze the user's prompt and return a valid JSON object with the following keys:\n"
        "- \"requires_tools\": boolean (true if the user wants to execute a tool like sending/drafting emails, generating/drawing/sketching images, searching the web, or writing/modifying code files; false for casual conversation, explanations, greetings, or analyzing/describing an uploaded image).\n"
        "- \"complexity\": string, one of [\"direct\", \"single\", \"swarm\"].\n"
        "  - \"direct\": Conversational questions, conceptual explanations, or describing uploaded images.\n"
        "  - \"single\": Needs a single tool action (e.g. generating an image, web search, code writing/editing).\n"
        "  - \"swarm\": Needs complex workflows (e.g. drafting/sending emails).\n"
        "- \"category\": string, one of [\"email\", \"visual\", \"code\", \"search\", \"casual\"].\n\n"
        "Provide ONLY the raw JSON object. Do not include markdown formatting or backticks. Example:\n"
        "{\"requires_tools\": true, \"complexity\": \"single\", \"category\": \"visual\"}"
    )
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Prompt: {user_prompt}"}
    ]
    
    try:
        if _is_cloud_model(target_model):
            import litellm
            cfg = _get_cloud_config(target_model)
            key = _get_cloud_api_key(target_model)
            res = litellm.completion(
                model=cfg["classifier_model"],
                messages=messages,
                api_key=key,
                temperature=0.0,
                max_tokens=40,
                timeout=4.0
            )
            raw = res.choices[0].message.content.strip()
        else:
            payload = {
                "model": target_model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 40
                }
            }
            res = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=6.0, verify=False)
            res.raise_for_status()
            raw = res.json().get("message", {}).get("content", "").strip()

        # Clean JSON wrappers if present
        if raw.startswith("```"):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            raw = raw.strip()
            
        data = json.loads(raw)
        return {
            "requires_tools": bool(data.get("requires_tools", False)),
            "complexity": data.get("complexity", "direct") if data.get("complexity") in ["direct", "single", "swarm"] else "direct",
            "category": data.get("category", "casual")
        }
    except Exception as e:
        logger.warning(f"[Prompt Analyzer] Failed structured analysis: {e}")
        return None

def _detect_intent(user_prompt: str, target_model: str, history: list = None) -> dict:
    """Detects user intent using a hybrid approach: Structured LLM Analyzer + Heuristic fallback.
    
    Returns a dict with:
        - is_sensitive: bool — triggers persona model routing
        - requires_tools: bool — whether agent needs tool calling
        - complexity: 'swarm' | 'single' | 'direct' — routing tier
            - 'swarm': full Crew swarm (email/delegation)
            - 'single': Specialist agent (developer/artist/secretary)
            - 'direct': no tools, direct LLM call (conversation)
        - is_local: bool — local vs cloud engine
    """
    clean_p = _normalize_prompt_for_intent(user_prompt)
    p = clean_p.lower()
    
    # Sensitivity check across current prompt AND recent history
    history_text = ""
    if history:
        history_text = " ".join([(m.get("content") or m.get("c") or "").lower() for m in history[-5:]])
    is_sensitive = any(kw in p or kw in history_text for kw in ['mental health', 'medical diagnosis', 'suicide', 'depressed', 'anxiety therapy', 'clinical treatment', 'legal advice'])

    # Try structured LLM Prompt Analyzer first
    analysis = _analyze_prompt_via_llm(clean_p, target_model)
    if analysis:
        logger.info(f"[Prompt Analyzer] Result: {analysis}")
        requires_tools = analysis["requires_tools"] and not is_sensitive
        return {
            "is_sensitive": is_sensitive,
            "requires_tools": requires_tools,
            "complexity": analysis["complexity"],
            "is_local": not _is_cloud_model(target_model)
        }
    
    logger.warning("[Prompt Analyzer] Falling back to keyword heuristics.")
    
    # 1. Fast-Track Keywords (Zero Latency)
    needs_code = any(kw in p for kw in CODE_KEYWORDS)
    needs_visual = any(kw in p for kw in VISUAL_KEYWORDS)
    needs_search = any(kw in p for kw in ['search', 'weather', 'stock', 'news', 'find', 'lookup', 'research', 'browse', 'who is', 'what is the price of'])
    needs_email = any(kw in p for kw in EMAIL_KEYWORDS)
    
    # Sensitivity check across current prompt AND recent history
    history_text = ""
    if history:
        history_text = " ".join([(m.get("content") or m.get("c") or "").lower() for m in history[-5:]])
    is_sensitive = any(kw in p or kw in history_text for kw in ['mental health', 'medical diagnosis', 'suicide', 'depressed', 'anxiety therapy', 'clinical treatment', 'legal advice'])
    
    # Complexity Classification: swarm (delegation) vs single (one agent) vs direct (no tools)
    
    # Run fast heuristic check first to avoid redundant LLM complexity classification
    complexity = _detect_complexity_heuristically(clean_p)
    if complexity is None and (needs_email or needs_visual or needs_search or needs_code):
        complexity = _classify_complexity_via_llm(clean_p, target_model)
    elif complexity is None:
        complexity = "heuristic"

    if complexity == "direct":
        # Bypasses tools entirely for conversational questions and image description requests
        logger.info("[Intent Classifier] Direct route activated via heuristic/LLM classification")
        return {"is_sensitive": is_sensitive, "requires_tools": False, "complexity": "direct", "is_local": not _is_cloud_model(target_model)}
    
    # Fallback to existing heuristics if classification is 'heuristic' or matched choice
    if needs_email:
        # If other capabilities are also active, use full swarm; otherwise bypass Manager for simple emails
        heuristic_complexity = "swarm" if (needs_code or needs_visual or needs_search) else "single"
        final_complexity = complexity if complexity in ["single", "swarm"] else heuristic_complexity
        logger.debug(f"Intent Classified (Complexity: {final_complexity} — email tasks)")
        return {"is_sensitive": is_sensitive, "requires_tools": not is_sensitive, "complexity": final_complexity, "is_local": not _is_cloud_model(target_model)}
    
    if needs_visual or needs_search or needs_code:
        final_complexity = complexity if complexity in ["single", "swarm"] else "single"
        logger.debug(f"Intent Classified (Complexity: {final_complexity} — visual/search/code)")
        return {"is_sensitive": is_sensitive, "requires_tools": not is_sensitive, "complexity": final_complexity, "is_local": not _is_cloud_model(target_model)}

    # 2. Heuristic fallback — no tools needed, direct LLM conversation
    return {"is_sensitive": is_sensitive, "requires_tools": False, "complexity": "direct", "is_local": not _is_cloud_model(target_model)}



def _assemble_context(user_prompt, img_data, history, intent, user_id=None, status_callback=None):
    """Stage 2: Merge Vision, Neural Memory (RAG), and Conversation History (Parallelized)."""
    clean_prompt = clean_user_prompt(user_prompt)
    
    # 1. Vision Logic (defined as a sub-task for parallel execution)
    def task_vision():
        if status_callback: status_callback("👁️ Analyzing Visual Context...")
        logger.debug("task_vision started")
        image_reference_keywords = ['this', 'that', 'image', 'picture', 'photo', 'look', 'see', 'describe', 'analyze', 'what is', 'tell me about', 'color', 'colour', 'who', 'where', 'context']
        is_referring_to_image = any(kw in clean_prompt.lower() for kw in image_reference_keywords)
        
        img_desc = "No image context available."
        prompt_with_img = user_prompt
 
        if img_data:
            # Normalize to list of base64 image strings
            imgs = [img_data] if isinstance(img_data, str) else img_data
            
            local_urls = []
            for item in imgs:
                local_url = save_uploaded_image(item)
                if local_url:
                    local_urls.append(local_url)
            
            if local_urls:
                img_markdown = "\n".join(f"![Uploaded Image]({url})" for url in local_urls)
                prompt_with_img = f"{img_markdown}\n{user_prompt}"
            
            if is_referring_to_image:
                descs = []
                for item in imgs:
                    if not intent["is_local"]:
                        desc = process_image_cloud(item, get_next_groq_key()) or process_image_local(item)
                    else:
                        vision_result = vision_sys.analyze_chat_images([item], clean_prompt)
                        desc = vision_result["description"] if vision_result else process_image_local(item)
                    if desc:
                        descs.append(desc)
                
                img_desc = "\n".join(f"Image {idx+1}: {d}" for idx, d in enumerate(descs)) if descs else "No image context available."
                return f"--- YOUR VISUAL PERCEPTION ---\n{img_desc}\n--- END VISUAL PERCEPTION ---\n\n{user_prompt}", img_desc
 
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
                target_urls = [all_img_urls[0]] if any(q in clean_prompt.lower() for q in generic_queries) else all_img_urls
                
                vision_result = vision_sys.analyze_chat_images(target_urls, clean_prompt)
                if vision_result:
                    img_desc = vision_result["description"]
                    return f"--- CURRENT VISUAL FOCUS ---\nImage: {vision_result['url']}\nActual Content: {img_desc}\n--- END VISUAL FOCUS ---\n\n{user_prompt}", img_desc
        
        return prompt_with_img, img_desc
 
    # 2. Memory Logic (sub-task for parallel execution)
    def task_memory():
        p_lower = clean_prompt.lower()
        # Skip semantic memory search for direct/conversational questions that don't ask about the project, files, code or database
        rag_triggers = ["architecture", "code", "function", "file", "logic", "decide", "decision", "plan", "why did", "project", "helper", "memory", "database", "implement", "design"]
        if not intent.get("requires_tools") and not any(tg in p_lower for tg in rag_triggers):
            logger.debug("[Memory] Skipping RAG for non-project casual query to save latency.")
            return ""
 
        if status_callback: status_callback("🧠 Accessing Neural Memory...")
        logger.debug("task_memory started")
        mem_filter = None
        if any(kw in clean_prompt.lower() for kw in ["decide", "decision", "architecture", "plan", "why did"]):
            mem_filter = {"type": "insight"}
        elif any(kw in clean_prompt.lower() for kw in ["code", "function", "file", "logic"]):
            mem_filter = {"type": "code"}
 
        semantic_memories = query_memory(clean_prompt, n_results=5, filter_dict=mem_filter, threshold=0.95, user_id=user_id)
        if semantic_memories:
            return "\n<neural_context>\n" + "".join([f"- {m['content']}\n" for m in semantic_memories]) + "</neural_context>\n"
        return ""

    # Execute Parallel Swarm
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_vision = executor.submit(task_vision)
        future_memory = executor.submit(task_memory)
        
        final_prompt, image_description = future_vision.result()
        try:
            memory_block = future_memory.result()
        except Exception as e:
            logger.error(f"[Memory] Context assembly continuing without neural memory: {e}", exc_info=True)
            memory_block = ""

    # 3. History (Ultra-Compact for speed)
    history_context = ""
    if history:
        history_context = "\n<history>\n"
        # OPTIMIZATION: limit to last 5 turns if it requires tools, otherwise 15
        limit = 5 if intent.get("requires_tools") else 15
        for msg in history[-limit:]:
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

def _harden_result(result, sys_config, target_model="gemma4:e2b"):
    """Stage 4: Post-processing and strict enforcement."""
    if not result: return result
    
    # Strip prompt leaks
    for marker in ["### System:", "STRICT RULE:", "Your personal goal is:", "Role:", "Goal:", "Backstory:"]:
        if marker in str(result):
            result = str(result).split(marker)[0].strip()
            
    # Fallback email JSON detection - strictly only for local models
    is_local = True
    if target_model:
        is_local = not _is_cloud_model(target_model)
        
    if is_local:
        res_str = str(result).strip()
        if "EMAIL_DRAFT_PAYLOAD:" not in res_str:
            # Match either a JSON object { ... } or a JSON array [ ... ]
            json_match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', res_str)
            if json_match:
                try:
                    candidate = json.loads(json_match.group(1))
                    
                    # Recursive function to locate the email dictionary
                    def find_email_dict(data):
                        if isinstance(data, dict):
                            if any(k in data for k in ["recipient", "to"]) and "subject" in data and "body" in data:
                                return data
                            for val in data.values():
                                res = find_email_dict(val)
                                if res:
                                    return res
                        elif isinstance(data, list):
                            for item in data:
                                res = find_email_dict(item)
                                if res:
                                    return res
                        return None
                    
                    email_data = find_email_dict(candidate)
                    if email_data:
                        draft = {
                            "recipient": email_data.get("recipient") or email_data.get("to"),
                            "subject": email_data.get("subject"),
                            "body": email_data.get("body"),
                            "tone": email_data.get("tone", "modern"),
                            "attachment_content": email_data.get("attachment_content"),
                            "attachment_filename": email_data.get("attachment_filename") or "report.txt"
                        }
                        prefix_text = res_str.split(json_match.group(1))[0].strip()
                        prefix_text = re.sub(r'```json\s*$', '', prefix_text).strip()
                        prefix_text = re.sub(r'```\s*$', '', prefix_text).strip()
                        
                        if prefix_text:
                            return f"{prefix_text}\n\nEMAIL_DRAFT_PAYLOAD:{json.dumps(draft)}"
                        else:
                            return f"EMAIL_DRAFT_PAYLOAD:{json.dumps(draft)}"
                except Exception:
                    pass

                try:
                    candidate = json.loads(json_match.group(1))

                    def contains_send_email_plan(data):
                        if isinstance(data, dict):
                            return "send_email_tool" in data or any(contains_send_email_plan(val) for val in data.values())
                        if isinstance(data, list):
                            return any(contains_send_email_plan(item) for item in data)
                        return False

                    def find_image_generation(data):
                        if isinstance(data, dict):
                            if "image_generate_tool" in data and isinstance(data["image_generate_tool"], dict):
                                return data["image_generate_tool"].get("description")
                            for val in data.values():
                                res = find_image_generation(val)
                                if res:
                                    return res
                        elif isinstance(data, list):
                            for item in data:
                                res = find_image_generation(item)
                                if res:
                                    return res
                        return None

                    description = None if contains_send_email_plan(candidate) else find_image_generation(candidate)
                    if description:
                        return tools.image_generate_tool.func(description=description)
                except Exception as e:
                    logger.warning(f"[Agents] Failed to recover image tool plan from final answer: {e}")

    return result

# Callbacks moved above _build_agents for proper forward referencing

def _execute_cloud(intent, context_data, target_model, sys_config, history, status_callback=None, chunk_callback=None, abort_event=None):
    """Stage 3a: Dispatch to Groq/Cloud Engine."""
    try:
        target_key = _get_cloud_api_key(target_model)
    except ValueError as e:
        return f"Cloud Engine Error: {str(e)}"
    cloud_cfg = _get_cloud_config(target_model)
    is_groq = cloud_cfg["provider"] == "groq"
    candidate_models = _cloud_candidate_models(cloud_cfg)
    if status_callback:
        active_status_callback.set(status_callback)
    if abort_event:
        active_abort_event.set(abort_event)

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
            except Exception:
                logger.warning(f"[Agents] Admin auth DB check failed for cloud path")

        if not auth_ok:
            return "ERROR: AUTH_REQUIRED. Please provide your Admin Key in the next message (use the Masked icon) to authorize sending emails."
    
    # Standard Cloud Task
    max_attempts = 3 if is_groq else len(candidate_models)
    for attempt in range(max_attempts):
        if abort_event and abort_event.is_set():
            return "Operation cancelled."
        current_key = target_key
        if is_groq and attempt > 0:
            current_key = get_next_groq_key() or os.getenv("GROQ_API_KEY")
        active_cloud_model = cloud_cfg["model"] if is_groq else candidate_models[attempt]
        try:
            if intent["requires_tools"]:
                entity_hint = f"\nRESOLVED ENTITY: If the user says 'send to him/her', use this email: {context_data['resolved_email']}\n" if context_data.get('resolved_email') else ""
                grounding = f"GROUNDING CONTEXT:\n{context_data['memory_block']}\n{context_data['history_context']}{entity_hint}\nSTRICT MANDATE: If the user intent involves 'send', 'search', 'draw', or 'archive', you MUST call the corresponding tool for the CURRENT request. Use web_search_text for text info, image_search_tool for real photos, and image_generate_tool for creative art. FIDELITY: If the user provided a block of technical text, you MUST pass it verbatim to the 'raw_attachment_text' parameter of the send_email_tool. However, you MUST convert any nested double quotes (\") in that text to single quotes (') or escape them strictly as \\\" to ensure valid JSON arguments. BROADCAST: When sending to multiple recipients, write the 'body' WITHOUT any salutation (no 'Hi', 'Dear'); start directly with the content. The tool handles personalization."
                
                if intent.get("complexity") == "swarm":
                    # Full Hierarchical Swarm — Manager delegates to Secretary/Developer/Artist
                    developer, secretary, artist, manager, generalist = get_agent_swarm(target_model, current_key, force_no_tools=False, sys_config=sys_config, model_override=active_cloud_model)
                    main_task = Task(
                        description=f'Respond to: "{context_data["final_prompt"]}"\n\n{grounding}', 
                        expected_output="A final summary of the task result or a direct answer.", 
                        agent=manager
                    )
                    try:
                        return _extract_crew_result(Crew(agents=[developer, secretary, artist, manager], tasks=[main_task], step_callback=global_step_callback))
                    except AgentFastExit as e:
                        return e.result
                else:
                    # Single-Agent Fast Path — route directly to the designated specialist agent
                    developer, secretary, artist, manager, generalist = get_agent_swarm(target_model, current_key, force_no_tools=False, sys_config=sys_config, model_override=active_cloud_model)
                    
                    # Determine which specialist is needed
                    p = clean_user_prompt(context_data["final_prompt"]).lower()
                    needs_code = any(kw in p for kw in CODE_KEYWORDS)
                    needs_visual = any(kw in p for kw in VISUAL_KEYWORDS)
                    needs_email = any(kw in p for kw in EMAIL_KEYWORDS)

                    if needs_email:
                        specialist = secretary
                        specialist_name = "secretary"
                    elif needs_code:
                        specialist = developer
                        specialist_name = "developer"
                    elif needs_visual:
                        specialist = artist
                        specialist_name = "artist"
                    else:
                        specialist = generalist
                        specialist_name = "generalist"

                    logger.debug(f"Cloud Single-Agent Fast Path to specialist: {specialist_name} (complexity: {intent.get('complexity')})")
                    
                    fast_task = Task(
                        description=f'Execute: "{context_data["final_prompt"]}"\n\n{grounding}',
                        expected_output="The raw result of the tool call or a direct response.",
                        agent=specialist
                    )
                    try:
                        return _extract_crew_result(Crew(agents=[specialist], tasks=[fast_task], step_callback=global_step_callback))
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
                    content = msg.get("content", "").strip()
                    if msg.get("masked") or msg.get("masked", False):
                        content = "[MASKED_SECRET]"
                    elif len(content) > 3000:
                        content = content[:3000] + "..."
                    messages.append({"role": role, "content": content})
            
            messages.append({"role": "user", "content": context_data["final_prompt"]})
            
            litellm_model = active_cloud_model

            if chunk_callback:
                logger.debug(f"Starting Character Streaming (Cloud: {litellm_model})")
                res = litellm.completion(model=litellm_model, messages=messages, api_key=current_key, stream=True)
                full_response = ""
                for chunk in res:
                    content = chunk.choices[0].delta.content
                    if content:
                        full_response += content
                        chunk_callback(content)
                return full_response
            else:
                res = litellm.completion(model=litellm_model, messages=messages, api_key=current_key)
                return res.choices[0].message.content
        except Exception as e:
            is_rate_limit = _is_rate_limit_error(e)
            if abort_event and abort_event.is_set():
                return "Operation cancelled."
            if is_rate_limit and is_groq:
                if target_model != "gemma4-cloud" and "llama-3.1-8b" not in str(e).lower():
                    logger.warning("Cloud Llama 70B rate limited. Retrying with Llama 3.1 8B Cloud model...")
                    target_model = "gemma4-cloud"
                    cloud_cfg = _get_cloud_config(target_model)
                    candidate_models = _cloud_candidate_models(cloud_cfg)
                    current_key = get_next_groq_key() or os.getenv("GROQ_API_KEY")
                    continue
                if attempt < max_attempts - 1:
                    logger.warning(f"Cloud Engine rate limited on attempt {attempt+1}. Retrying with key rotation in 3s...")
                    for _ in range(30):
                        if abort_event and abort_event.is_set():
                            return "Operation cancelled."
                        time.sleep(0.1)
                    continue
            if is_rate_limit and not is_groq:
                if attempt < max_attempts - 1:
                    logger.warning(
                        f"Cloud model {active_cloud_model} rate limited. Trying fallback {candidate_models[attempt + 1]}..."
                    )
                    continue
                return _cloud_rate_limit_message(target_model)
            return f"Cloud Engine Error: {str(e)}"

def _execute_local(intent, context_data, target_model, sys_config, history, status_callback=None, chunk_callback=None, abort_event=None):
    """Stage 3b: Dispatch to Ollama/Local Engine."""
    if status_callback:
        active_status_callback.set(status_callback)
    if abort_event:
        active_abort_event.set(abort_event)

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
            except Exception:
                logger.warning(f"[Agents] Admin auth DB check failed for local path")

        if not auth_ok:
            return "ERROR: AUTH_REQUIRED. Please provide your Admin Key in the next message (use the Masked icon) to authorize sending emails."

    try:
        if abort_event and abort_event.is_set():
            return "Operation cancelled."
        if intent["requires_tools"]:
            logger.debug(f"STARTING LOCAL TOOL EXECUTION (Model: {target_model})")
            developer, secretary, artist, manager, generalist = get_agent_swarm(target_model, None, force_no_tools=False, sys_config=sys_config)
            
            # Determine which specialist is needed
            p = clean_user_prompt(context_data["final_prompt"]).lower()
            needs_code = any(kw in p for kw in CODE_KEYWORDS)
            needs_visual = any(kw in p for kw in VISUAL_KEYWORDS)
            needs_email = any(kw in p for kw in EMAIL_KEYWORDS)

            if intent.get("complexity") == "swarm":
                specialist = manager
                specialist_name = "manager"
            elif needs_email:
                specialist = secretary
                specialist_name = "secretary"
            elif needs_code:
                specialist = developer
                specialist_name = "developer"
            elif needs_visual:
                specialist = artist
                specialist_name = "artist"
            else:
                specialist = generalist
                specialist_name = "generalist"

            logger.debug(f"Local Execution path routed to: {specialist_name}")
            
            local_task = Task(
                description=(
                    f"Action: Execute the user request using the appropriate tool if needed.\n"
                    f"Current Request: {context_data.get('final_prompt', '')}\n\n"
                    f"Conversation History (For context/retries):\n{context_data.get('history_context', '')}\n\n"
                    f"Grounding Memory:\n{context_data.get('memory_block', '')}\n\n"
                    f"FIDELITY: If the user provided a block of technical text, you MUST pass it verbatim to the 'raw_attachment_text' parameter of the send_email_tool. However, you MUST convert any nested double quotes (\") in that text to single quotes (') or escape them strictly as \\\" to ensure valid JSON arguments.\n"
                    f"BROADCAST: When sending to multiple recipients, write the 'body' WITHOUT any salutation (no 'Hi', 'Dear'); start directly with the content. The tool handles personalization.\n"
                    f"STRICT RULE: If the user just provided an Admin Key, find the previous failed tool request in history and execute it now using the EXACT tool name 'send_email_tool'."
                ),
                expected_output="The output of the tool execution (e.g., SUCCESS) or a final helpful answer.",
                agent=specialist
            )
            try:
                return _extract_crew_result(Crew(agents=[specialist], tasks=[local_task], step_callback=global_step_callback))
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
                role = "user" if str(msg.get("role")).lower() in ["user", "u", "human"] else "assistant"
                content = msg.get("content", "").strip()
                if msg.get("masked") or msg.get("masked", False):
                    content = "[MASKED_SECRET]"
                elif len(content) > 3000:
                    content = content[:3000] + "..." # Truncate for speed
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": context_data["final_prompt"]})
        
        payload = {"model": target_model, "messages": messages, "stream": True if chunk_callback else False}
        
        if chunk_callback:
            logger.debug(f"Starting Character Streaming (Model: {target_model})")
            res = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, stream=True, timeout=120, verify=False)
            res.raise_for_status()
            full_response = ""
            for line in res.iter_lines():
                if abort_event and abort_event.is_set():
                    return "Operation cancelled."
                if line:
                    try:
                        chunk = json.loads(line.decode('utf-8'))
                        if "error" in chunk:
                            raise ValueError(f"Ollama streaming error: {chunk['error']}")
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
            res.raise_for_status()
            res_json = res.json()
            if "error" in res_json:
                raise ValueError(f"Ollama error: {res_json['error']}")
            return res_json.get("message", {}).get("content", "Error parsing response.")
    except Exception as e:
        err_msg = str(e)
        if abort_event and abort_event.is_set():
            return "Operation cancelled."
        logger.warning(f"Local Engine Timeout/Error ({err_msg}). Attempting Cloud Fallback...")
        
        # Clean up the error message if it's a JSON string from Ollama
        clean_err = err_msg
        if "model requires more system memory" in err_msg:
            # Extract just the message for clean display
            match = re.search(r'"message":\s*"([^"]+)"', err_msg) or re.search(r"'message':\s*'([^']+)'", err_msg)
            if match:
                clean_err = match.group(1)
        
        warning_msg = f"⚠️ **System Alert**: Local model `{target_model}` failed to load ({clean_err}). Falling back to Cloud engine...\n\n"
        
        if chunk_callback:
            chunk_callback(warning_msg)
            
        fallback_model = "gemma4-cloud" if target_model == "gemma4:e2b" else "agentic-pro"
        
        try:
            cloud_res = _execute_cloud(intent, context_data, fallback_model, sys_config, history, status_callback=status_callback, chunk_callback=chunk_callback, abort_event=abort_event)
            return f"{warning_msg}{cloud_res}"
        except Exception as cloud_err:
            fail_msg = f"Cloud fallback failed: {str(cloud_err)}"
            if chunk_callback:
                chunk_callback(fail_msg)
            return f"{warning_msg}{fail_msg}"

def _extract_image_prompt(user_prompt: str, history: list) -> str:
    """
    Extracts or reconstructs the image description from the user prompt and history.
    """
    clean_p = _normalize_prompt_for_intent(user_prompt)
    p = clean_p.lower().strip()
    
    # 1. First strip leading triggers to see what the core request is
    clean_desc = clean_p
    patterns_to_strip = [
        r'(?i)^\s*(now\s+)?(can\s+you\s+)?(generate|draw|create|paint|show)(\s+an?|\s+the)?\s*image\s+(of|about|depicting)?\s*',
        r'(?i)^\s*(now\s+)?(can\s+you\s+)?(draw|paint|create|sketch)\s+(of|about|depicting)?\s*'
    ]
    for pattern in patterns_to_strip:
        clean_desc = re.sub(pattern, '', clean_desc)
    
    clean_desc = clean_desc.strip()
    clean_desc = re.sub(r'[?\.]+$', '', clean_desc).strip()
    
    # 2. Check if clean_desc is generic/referential
    is_generic = False
    clean_lower = clean_desc.lower()
    
    # Reference indicators
    ref_indicators = ["based on this", "based on that", "based on the above", "based on previous", "of this", "of that", "of it", "about this", "about that", "this", "that", "it"]
    if clean_lower in ref_indicators or any(clean_lower == kw for kw in ["above", "scenery", "description"]):
        is_generic = True
    elif any(kw in clean_lower for kw in ["based on this", "based on that", "based on the above", "based on previous", "of it"]):
        is_generic = True
    elif len(clean_lower.split()) <= 3 and any(kw in clean_lower for kw in ["this", "that", "it", "above"]):
        is_generic = True
        
    if is_generic and history:
        # Find the last assistant message
        for msg in reversed(history):
            role = (msg.get("role") or msg.get("r") or "").lower()
            if role in ["assistant", "a", "bot"]:
                content = msg.get("content") or msg.get("c") or ""
                # Strip markdown tags/images/links/formatting
                clean_content = re.sub(r'!\[.*?\]\(.*?\)', '', content)
                clean_content = re.sub(r'\[.*?\]\(.*?\)', '', clean_content)
                clean_content = re.sub(r'[*_`#\-]', ' ', clean_content)
                clean_content = _preserve_structured_text(clean_content)
                if clean_content:
                    logger.info(f"[Direct Tool] Reconstructed image description from history: '{clean_content[:150]}...'")
                    return clean_content
                    
    return clean_desc or user_prompt


def _is_image_generation_prompt(prompt_lower: str) -> bool:
    return (
        any(kw in prompt_lower for kw in ['draw', 'generate image', 'generate an image', 'create image', 'create an image', 'paint', 'sketch', 'artwork']) or
        bool(re.search(r'\b(generate|create|make|draw|paint|sketch|render)\s+(?:[a-zA-Z]+\s+){0,3}(image|picture|pic|photo|artwork|portrait|wallpaper|scene|illustration)\b', prompt_lower))
    )


def _is_image_email_workflow(prompt_lower: str, email_match) -> bool:
    has_email_action = any(kw in prompt_lower for kw in ['email', 'mail', 'send'])
    has_image_reference = any(kw in prompt_lower for kw in ['image', 'picture', 'photo', 'pic', 'artwork'])
    has_attachment_action = any(kw in prompt_lower for kw in ['attach', 'attachment', 'include'])
    return (has_email_action or has_attachment_action) and has_image_reference and (_is_image_generation_prompt(prompt_lower) or has_attachment_action)


def _history_content(msg: dict) -> str:
    return str(msg.get("content") or msg.get("c") or "")


def _last_email_from_history(history: list, current_prompt: str = "") -> Optional[str]:
    if not history:
        return None
    current_clean = clean_user_prompt(current_prompt).strip()
    for msg in reversed(history):
        content = _history_content(msg)
        if current_clean and content.strip() == current_clean:
            continue
        emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', content)
        if emails:
            return emails[-1]
    return None


def _latest_attachable_history_context(history: list, current_prompt: str = "") -> dict:
    context = {"has_image": False, "has_text": False, "text": ""}
    if not history:
        return context

    current_clean = clean_user_prompt(current_prompt).strip()
    for msg in reversed(history):
        content = _history_content(msg)
        if current_clean and content.strip() == current_clean:
            continue

        if msg.get("img") or msg.get("i") or re.search(r'!\[[^\]]*\]\([^)]+\)', content):
            context["has_image"] = True

        text = re.sub(r'!\[[^\]]*\]\([^)]+\)', ' ', content)
        text = re.sub(r'EMAIL_DRAFT_PAYLOAD:\s*\{[\s\S]*?\}', ' ', text)
        text = re.sub(r'```[\s\S]*?```', ' ', text)
        text = _preserve_structured_text(text)
        if re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text) and len(re.sub(r'\s+', ' ', text).split()) <= 8:
            text = ""
        if text and len(text) >= 20 and not context["has_text"]:
            context["has_text"] = True
            context["text"] = text

        if context["has_image"] and context["has_text"]:
            break

    return context


def _is_above_attachment_request(prompt: str) -> bool:
    prompt_lower = clean_user_prompt(prompt).lower()
    if _looks_like_structured_technical_text(prompt):
        return False
    if any(kw in prompt_lower for kw in ["explain", "describe", "syntax", "walkthrough", "analysis", "analyze", "detail", "detailed"]):
        return False
    has_attach_action = any(kw in prompt_lower for kw in [
        "attach", "attachment", "include", "add", "put", "use"
    ])
    has_reference = any(kw in prompt_lower for kw in [
        "above", "previous", "last", "that", "this", "it", "same"
    ])
    has_email_context = any(kw in prompt_lower for kw in [
        "email", "mail", "template", "tamplate", "draft", "send"
    ])
    return has_attach_action and has_reference and (has_email_context or "attach" in prompt_lower)


def _attachment_choice_from_prompt(prompt: str, attach_context: dict) -> str:
    prompt_lower = clean_user_prompt(prompt).lower()
    if _looks_like_structured_technical_text(prompt):
        return "unknown"
    has_explicit_attachment_request = any(kw in prompt_lower for kw in [
        "attach", "attachment", "include", "add", "put", "use"
    ]) or any(kw in prompt_lower for kw in [
        "send", "email", "mail", "template", "draft"
    ])
    if not has_explicit_attachment_request:
        return "unknown"
    explicit_image = any(kw in prompt_lower for kw in ["image", "photo", "picture", "pic", "artwork"])
    no_content_request = any(kw in prompt_lower for kw in [
        "dont fill content", "don't fill content", "do not fill content",
        "empty content", "blank content", "empty body", "blank body", "no content"
    ])
    explicit_text = any(kw in prompt_lower for kw in ["text", "paragraph", "notes", "message"]) or (
        "content" in prompt_lower and not no_content_request
    )
    wants_both = "both" in prompt_lower or "image and text" in prompt_lower or "text and image" in prompt_lower
    wants_summary = "summary" in prompt_lower or "summarize" in prompt_lower or "relevant text" in prompt_lower or "relivent text" in prompt_lower

    if wants_summary:
        return "summary"
    if wants_both:
        return "both"
    if explicit_image and not explicit_text:
        return "image"
    if explicit_text and not explicit_image:
        return "text"
    if attach_context.get("has_image") and not attach_context.get("has_text"):
        return "image"
    if attach_context.get("has_text") and not attach_context.get("has_image"):
        return "text"
    if attach_context.get("has_image") and attach_context.get("has_text"):
        return "ambiguous"
    return "unknown"


def _extract_latest_email_draft(history: list) -> Optional[dict]:
    if not history:
        return None

    for msg in reversed(history):
        content = _history_content(msg)
        if "EMAIL_DRAFT_PAYLOAD:" not in content:
            continue
        payload = content.split("EMAIL_DRAFT_PAYLOAD:", 1)[1].strip()
        start_idx = payload.find("{")
        end_idx = payload.rfind("}")
        if start_idx == -1 or end_idx <= start_idx:
            continue
        try:
            draft = json.loads(payload[start_idx:end_idx + 1])
            if isinstance(draft, dict):
                return draft
        except Exception:
            continue
    return None


def _extract_quoted_value_for_field(prompt: str, field_name: str) -> Optional[str]:
    field_pattern = re.escape(field_name)
    patterns = [
        rf'["“”]([^"“”]+)["“”]\s+for\s+(?:the\s+)?[\'"`]?{field_pattern}[\'"`]?',
        rf"[\'‘’]([^\'‘’]+)[\'‘’]\s+for\s+(?:the\s+)?[\'\"`]?{field_pattern}[\'\"`]?",
        rf"(?:{field_pattern})\s*(?:as|to|=|:)\s*[\"“”]([^\"“”]+)[\"“”]",
        rf"(?:{field_pattern})\s*(?:as|to|=|:)\s*[\'‘’]([^\'‘’]+)[\'‘’]",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_email_draft_updates(prompt: str) -> dict:
    updates = {}
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', prompt)
    if email_match:
        updates["recipient"] = email_match.group(0)

    subject = _extract_quoted_value_for_field(prompt, "subject")
    body = _extract_quoted_value_for_field(prompt, "body") or _extract_quoted_value_for_field(prompt, "content")
    if subject is not None:
        updates["subject"] = subject
    if body is not None:
        updates["body"] = body

    tone_match = re.search(r'\b(formal|informal|modern)\b', prompt, flags=re.IGNORECASE)
    if tone_match:
        updates["tone"] = tone_match.group(1).lower()
    return updates


def _is_email_draft_edit_request(prompt_lower: str) -> bool:
    has_edit_action = any(kw in prompt_lower for kw in [
        "fill", "update", "change", "set", "put", "replace", "edit"
    ])
    has_email_surface = any(kw in prompt_lower for kw in [
        "email", "mail", "template", "tamplate", "draft", "widget"
    ])
    has_field = any(kw in prompt_lower for kw in [
        " to", "'to'", '"to"', "recipient", "subject", "body", "content", "tone"
    ])
    return has_edit_action and (has_email_surface or has_field)


def _try_update_email_draft_from_prompt(clean_prompt: str, history: list, target_model: str) -> Optional[str]:
    if not _is_email_draft_edit_request(clean_prompt.lower()):
        return None
    if _looks_like_structured_technical_text(clean_prompt) and not any(
        kw in clean_prompt.lower() for kw in ["email", "mail", "send", "attach", "attachment", "template", "draft"]
    ):
        return None

    updates = _extract_email_draft_updates(clean_prompt)
    if not updates:
        return None

    draft = _extract_latest_email_draft(history) or {}
    if not draft:
        draft = {
            "recipient": _last_email_from_history(history, clean_prompt) or "",
            "subject": "Requested Image and Description",
            "body": "",
            "tone": "modern",
            "attachment_content": None,
            "attachment_filename": "attachment.png",
        }

    draft.update({key: value for key, value in updates.items() if value is not None})
    draft.setdefault("tone", "modern")
    draft.setdefault("subject", "Requested Image and Description")
    draft.setdefault("body", "")

    if not draft.get("attachment_content"):
        from app.logic.tools import resolve_chat_image
        resolved = resolve_chat_image("above image", history)
        if isinstance(resolved, tuple) and resolved[0] is not None:
            draft["attachment_content"] = resolved[0]
            draft["attachment_filename"] = resolved[1] if len(resolved) > 1 else draft.get("attachment_filename", "attachment.png")

    if not draft.get("recipient"):
        return "Which recipient should I put in the email template?"

    return f"EMAIL_DRAFT_PAYLOAD:{json.dumps(draft)}"


def _extract_generated_image_url(tool_result: str) -> Optional[str]:
    markdown_match = re.search(r'!\[[^\]]*\]\(([^)]+)\)', str(tool_result))
    if markdown_match:
        return markdown_match.group(1)
    url_match = re.search(r'https?://\S+', str(tool_result))
    if url_match:
        return url_match.group(0).rstrip(').,')
    return None


def _clean_email_image_description(clean_prompt: str, history: list) -> str:
    raw_desc = _extract_image_prompt(clean_prompt, history)
    description = re.split(r'\b(?:and\s+)?(?:attach|send|email|mail)\b', raw_desc, flags=re.IGNORECASE)[0].strip()
    description = re.sub(r'(?i)\b(?:for|to|with)\b.*$', '', description).strip()
    description = re.sub(r'(?i)\b(?:image|picture|photo|pic|artwork)$', '', description).strip()
    description = re.sub(r'[?\.\,\;\:\-\_]+$', '', description).strip()
    if not description or description.lower() in {"a", "an", "the"}:
        return "a vibrant creative abstract image with rich colors and a polished modern style"
    return description


def _try_direct_tool_execution(user_prompt: str, intent: dict, history: list, target_model: str = "gemma2:2b", status_callback=None, chunk_callback=None) -> Optional[str]:
    """
    Attempts to execute deterministic tool workflows directly.
    Returns the string result if a tool was executed, otherwise None.
    """
    clean_prompt = clean_user_prompt(user_prompt)
    p = _normalize_prompt_for_intent(user_prompt).lower().strip()
    
    # Strip common conversational/politeness prefixes at the start to ensure greeting-agnostic guard checks
    conversational_prefixes = [
        "ok", "okay", "please", "hey", "hello", "hi", 
        "can you", "could you", "would you", "will you",
        "tell me", "show me", "find out", "check", "search for",
        "one thing", "just", "so", "now", "actually"
    ]
    
    p_stripped = p
    modified = True
    while modified:
        modified = False
        for prefix in conversational_prefixes:
            if p_stripped.startswith(prefix + " ") or p_stripped == prefix:
                p_stripped = p_stripped[len(prefix):].strip()
                modified = True
                break
            elif p_stripped.startswith(prefix + ",") or p_stripped.startswith(prefix + "."):
                p_stripped = p_stripped[len(prefix):].strip(",. ").strip()
                modified = True
                break

    # Conversational guard: question-type queries should bypass direct tools and route to the LLM agent
    conversational_starters = [
        'did you', 'why did you', 'how did you', 'do you', 'can you tell me if', 
        'was there', 'were you', 'did it', 'what is', 'what are', 'how to', 
        'how do', 'how can', 'how does', 'how is', 'how was', 'why do', 
        'why does', 'why is', 'explain', 'describe', 'summarize', 'tell me', 
        'who is', 'who was', 'where is', 'when is', 'define', 'what\'s', 'how\'s'
    ]
    if any(p_stripped.startswith(start) for start in conversational_starters):
        return None

    draft_update = _try_update_email_draft_from_prompt(clean_prompt, history, target_model)
    if draft_update:
        return draft_update
    
    # Check for email intent with an email address
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', p)
    history_email = _last_email_from_history(history, clean_prompt)
    recipient = email_match.group(0) if email_match else history_email
    is_above_attachment = _is_above_attachment_request(clean_prompt)
    attach_context = _latest_attachable_history_context(history, clean_prompt) if is_above_attachment else {}
    attachment_choice = _attachment_choice_from_prompt(clean_prompt, attach_context) if is_above_attachment else "unknown"
    is_email = any(kw in p for kw in ['email', 'mail', 'send']) or (
        bool(email_match) and any(kw in p for kw in ['attach', 'attachment', 'include'])
    ) or (is_above_attachment and bool(recipient))
    
    # 1. Image Generation Intent (Supports up to 3 words in between generate and image)
    is_generate = _is_image_generation_prompt(p)
    is_image_search = any(kw in p for kw in ['search image', 'search photo', 'find image', 'find photo', 'show me a picture of', 'show me a photo of', 'real picture of', 'real photo of'])
    is_deterministic_image_email = _is_image_email_workflow(p, email_match) or (
        is_email and is_above_attachment and attachment_choice in {"image", "text", "both", "summary"}
    )
    force_direct_tool = bool(intent.get("force_direct_tool"))

    if is_above_attachment and attachment_choice == "ambiguous":
        return (
            "I found both an image and text in the recent chat. "
            "Should I use the image only, the text only, both, or a summary of the relevant text with the image attached?"
        )

    if not is_deterministic_image_email and not force_direct_tool:
        if not intent.get("is_local") or not intent.get("requires_tools"):
            return None

        # Capable local models should still use CrewAI except for deterministic workflows.
        model_str = target_model.lower()
        is_weak = ("2b" in model_str or "0.5b" in model_str or "1.5b" in model_str) and "e2b" not in model_str
        if not is_weak:
            logger.debug(f"[Direct Tool] Bypassing direct execution for capable local model: {target_model}")
            return None
    
    # Directly execute email tasks to avoid ReAct loop failures.
    if is_email and recipient:
        if status_callback:
            status_callback("📧 Drafting email with visual attachments...")
            
        attachment_url = None
        filename = "attachment.png"
        description = ""
        raw_attachment_text = ""
        wants_empty_body = any(kw in p for kw in [
            "dont fill content", "don't fill content", "do not fill content",
            "empty content", "blank content", "empty body", "blank body", "no content"
        ])
        
        # Step A: Generate image if requested in the email context
        if is_generate:
            description = _clean_email_image_description(clean_prompt, history)
                
            if status_callback:
                status_callback("🎨 Generating attached creative visual...")
            logger.info(f"[Direct Tool] Executing image_generate_tool for prompt: '{description}'")
            try:
                gen_res = tools.image_generate_tool.func(description=description)
                attachment_url = _extract_generated_image_url(gen_res)
                if not attachment_url:
                    return f"ERROR: Image generation did not return an attachable URL. Result: {gen_res}"
                
                # Determine clean filename from description
                clean_name = re.sub(r'[^\w]', '_', description).strip().lower()
                filename = f"{clean_name[:20]}_image.png"
            except Exception as e:
                logger.error(f"[Direct Tool] image_generate_tool failed inside email flow: {e}")
                return f"ERROR: Image generation failed before email drafting: {str(e)}"
                
        # Step B: Resolve chat history reference if no image generation was requested
        if not attachment_url and attachment_choice in {"image", "both", "summary", "unknown"}:
            from app.logic.tools import resolve_chat_image
            resolved = resolve_chat_image(clean_prompt, history)
            if isinstance(resolved, tuple) and resolved[0] is not None:
                attachment_url = resolved[0]
                filename = resolved[1] if len(resolved) > 1 else filename
            elif isinstance(resolved, str):
                attachment_url = resolved

        if is_above_attachment and attachment_choice in {"text", "both", "summary"}:
            text_context = attach_context.get("text", "")
            if attachment_choice == "summary" and text_context:
                raw_attachment_text = "Summary of relevant previous text: " + text_context[:900]
            else:
                raw_attachment_text = text_context

        # Step C: Use the local model to write the email subject and body dynamically
        from app.logic.exceptions import AgentFastExit
        system_prompt = (
            "You are an assistant that drafts email details (subject, body, filename) based on user instructions.\n"
            "Draft a professional and complete email. If the user wants a description of the generated image, write a creative description to include in the body.\n"
            "Output strictly a JSON object with keys: 'subject', 'body', 'attachment_filename'.\n"
            "Do not include any markdown wrapper or extra text."
        )
        user_msg = f"User Instruction: {clean_prompt}"
        
        subject = "Requested Image and Description"
        if wants_empty_body:
            body = ""
        elif raw_attachment_text:
            body = raw_attachment_text
        elif description:
            body = f"Please find the requested image attached. It depicts {description}."
        else:
            body = "Please find the requested image attached."
        
        try:
            if _is_cloud_model(target_model):
                raise RuntimeError("Skipping cloud model for deterministic email drafting")
            model_name = target_model or "gemma2:2b"
            if model_name == "helper":
                raise RuntimeError("No local Ollama model selected for deterministic email drafting")
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                "format": "json",
                "stream": False,
                "options": {
                    "temperature": 0.3
                }
            }
            res = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=25, verify=False)
            res.raise_for_status()
            content = res.json().get("message", {}).get("content", "").strip()
            
            # Clean potential markdown wrappers
            if content.startswith("```"):
                content = re.sub(r'^```(?:json)?\n', '', content)
                content = re.sub(r'\n```$', '', content)
            data = json.loads(content)
            subject = data.get("subject", subject)
            body = data.get("body", body)
            filename = data.get("attachment_filename", filename)
        except Exception as e:
            logger.warning(f"[Direct Tool] Failed to draft email via native Ollama: {e}")
            if "house" in p:
                subject = "House Image and Description"
                body = "Here is the house image you requested, along with a description of the cozy and beautiful home."
                filename = "house_image.png"
            elif "car" in p:
                subject = "Car Image and Description"
                body = "Here is the car image you requested, along with a description of the sleek and futuristic vehicle."
                filename = "car_image.png"
            else:
                subject = "Requested Image and Description"
                if wants_empty_body:
                    body = ""
                elif raw_attachment_text:
                    body = raw_attachment_text
                elif description:
                    body = f"Please find the requested image attached. It depicts {description}."
                else:
                    body = "Please find the requested image attached to this email."

        if wants_empty_body:
            body = ""

        logger.info(f"[Direct Tool] Executing send_email_tool for recipient: '{recipient}'")
        try:
            tools.send_email_tool.func(
                recipient=recipient,
                subject=subject,
                body=body,
                attachment_content=attachment_url or "",
                attachment_filename=filename,
                raw_attachment_text="" if raw_attachment_text == body else raw_attachment_text
            )
        except AgentFastExit as e:
            from app.logic.bus import tool_result_bus, job_id_context
            jid = job_id_context.get()
            if jid:
                logger.info(f"[Direct Tool] Saving direct email result to bus for job {jid}")
                tool_result_bus.set_result(jid, e.result)
            return e.result
        except Exception as e:
            logger.error(f"[Direct Tool] send_email_tool failed: {e}", exc_info=True)
            return f"Error drafting email: {str(e)}"
    
    # 1. Image Generation Intent
    if is_generate and not is_image_search:
        if status_callback:
            status_callback("🎨 Generating creative visual...")
        description = _extract_image_prompt(clean_prompt, history)
        logger.info(f"[Direct Tool] Executing image_generate_tool for prompt: '{description}'")
        try:
            result = tools.image_generate_tool.func(description=description)
            from app.logic.bus import tool_result_bus, job_id_context
            jid = job_id_context.get()
            if jid:
                logger.info(f"[Direct Tool] Saving direct image result to bus for job {jid}")
                tool_result_bus.set_result(jid, result)
            if chunk_callback:
                chunk_callback(result)
            return result
        except Exception as e:
            logger.error(f"[Direct Tool] image_generate_tool failed: {e}", exc_info=True)
            return f"Error generating image: {str(e)}"
            
    # 2. Image Search Intent
    if is_image_search or (any(kw in p for kw in ['image', 'picture', 'photo']) and any(kw in p for kw in ['search', 'find', 'lookup'])):
        if status_callback:
            status_callback("🔍 Searching for real-world images...")
        query = clean_prompt
        patterns_to_strip = [
            r'(?i)^\s*(now\s+)?(can\s+you\s+)?(search|find|show me|lookup)(\s+an?|\s+the)?\s*(image|picture|photo)\s+(of|about|depicting)?\s*',
            r'(?i)^\s*(real\s+)?(picture|photo|image)\s+of\s*'
        ]
        for pattern in patterns_to_strip:
            query = re.sub(pattern, '', query)
        query = re.sub(r'[?\.]+$', '', query).strip()
        
        logger.info(f"[Direct Tool] Executing image_search_tool for query: '{query}'")
        try:
            result = tools.image_search_tool.func(query=query)
            from app.logic.bus import tool_result_bus, job_id_context
            jid = job_id_context.get()
            if jid:
                logger.info(f"[Direct Tool] Saving direct image search result to bus for job {jid}")
                tool_result_bus.set_result(jid, result)
            if chunk_callback:
                chunk_callback(result)
            return result
        except Exception as e:
            logger.error(f"[Direct Tool] image_search_tool failed: {e}", exc_info=True)
            return f"Error searching image: {str(e)}"
            
    # 3. Web Search Intent
    is_web_search = any(kw in p for kw in ['search web', 'search the web', 'google search', 'web search', 'search for']) or p.startswith('search ')
    if is_web_search:
        if status_callback:
            status_callback("🔍 Scouring the web for real-time data...")
        query = clean_prompt
        patterns_to_strip = [
            r'(?i)^\s*(now\s+)?(can\s+you\s+)?(search\s+(the\s+)?web|search|google|lookup)(\s+for)?\s*'
        ]
        for pattern in patterns_to_strip:
            query = re.sub(pattern, '', query)
        query = re.sub(r'[?\.]+$', '', query).strip()
        
        logger.info(f"[Direct Tool] Executing search_tool for query: '{query}'")
        try:
            result = tools.search_tool.func(query=query)
            from app.logic.bus import tool_result_bus, job_id_context
            jid = job_id_context.get()
            if jid:
                logger.info(f"[Direct Tool] Saving direct search result to bus for job {jid}")
                tool_result_bus.set_result(jid, result)
            if chunk_callback:
                chunk_callback(result)
            return result
        except Exception as e:
            logger.error(f"[Direct Tool] search_tool failed: {e}", exc_info=True)
            return f"Error searching the web: {str(e)}"

    return None

def run_helper_agent(user_prompt: str, img_data: str = None, target_model: str = "gemma4:e2b", sys_config: dict = None, history: List[dict] = None, persona: bool = False, abort_event: Any = None, user_id: str = None, status_callback=None, chunk_callback=None, intent: dict = None):
    """Orchestrates the specialized agents via a decoupled modular pipeline."""
    
    # 0. Set history context for active tool executions
    from app.logic.tools import active_history_context
    active_history_context.set(history)

    # Check for early abort
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
    routing_prompt = _reconstruct_contextual_prompt(user_prompt, history)
    intent_prompt = _normalize_prompt_for_intent(routing_prompt)

    # 2. Intent Detection (use pre-computed if available)
    visual_continuation_prompt = _resolve_visual_task_continuation(user_prompt, history)
    if visual_continuation_prompt:
        user_prompt = visual_continuation_prompt
        intent = {
            "is_sensitive": False,
            "requires_tools": True,
            "complexity": "single",
            "is_local": not _is_cloud_model(target_model),
            "force_direct_tool": True,
        }
    elif intent is None:
        intent = _detect_intent(intent_prompt, target_model, history)
    
    # Fast Path Direct Tool Execution (for local models to bypass ReAct loop failures)
    direct_res = _try_direct_tool_execution(user_prompt, intent, history, target_model=target_model, status_callback=status_callback, chunk_callback=chunk_callback)
    if direct_res is not None:
        return direct_res
    
    # 3. Routing Adjustments
    # Smart Routing Optimization: If the heavy Gemma 4 model is selected for a direct conversational query
    # (no tools or images involved), internally route to the lightweight, fine-tuned helper model
    # to keep response times under 1.5s while preserving UI representation.
    if target_model == "gemma4:e2b" and intent.get("complexity") == "direct" and not img_data:
        logger.info("[Smart Routing] Overriding local gemma4:e2b to gemma2:2b for fast direct chat response.")
        target_model = "gemma2:2b"

    if persona or (intent["is_sensitive"] and target_model != "helper"):
        target_model = "helper"

    # 4. Context Assembly
    context_data = _assemble_context(user_prompt, img_data, history, intent, user_id=user_id, status_callback=status_callback)

    # 5. Engine Execution
    if abort_event and abort_event.is_set(): return "Operation cancelled."
    
    if not intent["is_local"] and _is_cloud_model(target_model):
        result = _execute_cloud(intent, context_data, target_model, sys_config, history, status_callback=status_callback, chunk_callback=chunk_callback, abort_event=abort_event)
    else:
        result = _execute_local(intent, context_data, target_model, sys_config, history, status_callback=status_callback, chunk_callback=chunk_callback, abort_event=abort_event)

    # 6. Result Hardening
    hardened = _harden_result(result, sys_config, target_model=target_model)
    from app.logic.bus import tool_result_bus, job_id_context
    jid = job_id_context.get()
    if jid:
        logger.info(f"[Agents] Storing final hardened result on bus for job {jid}")
        tool_result_bus.set_result(jid, hardened)
    return hardened

def ask_the_helper(prompt: str, img_data: str = None, target_model: str = "gemma4:e2b", sys_config: dict = None, history: List[dict] = None, persona: bool = False, abort_event: Any = None, user_id: str = None, status_callback=None, chunk_callback=None, intent: dict = None):
    return run_helper_agent(prompt, img_data, target_model, sys_config, history, persona, abort_event, user_id, status_callback=status_callback, chunk_callback=chunk_callback, intent=intent)
