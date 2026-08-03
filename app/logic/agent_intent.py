import json
import re

import requests

CODE_KEYWORDS = [
    "code", "bug", "logic", "python", "javascript", "html", "css", "develop", "compile", "debug", "git",
    "refactor", "function", "class", "typescript", "react", "node", "sql", "bash", "powershell",
    "java", "rust", "website", "web app", "api endpoint",
]
VISUAL_KEYWORDS = [
    "draw", "paint", "sketch", "scetch", "generate", "create", "artwork", "photo of", "show me a picture of",
    "real picture of", "look like", "image", "shot", "wallpaper", "render", "pics", "pic", "capture",
    "acrylic", "acrilic", "arcilic", "drawing", "drawin", "painting", "panting", "illustration", "portrait", "potrait",
    "canvas", "sketching",
]
EMAIL_KEYWORDS = [
    "email", "send", "sent", "dispatch", "mail", "forward", "admin_key_provided", "approval_confirmed", "to him", "to her",
    "to them", "tell him", "tell her", "tell them", "message him", "message her",
]


_IMAGE_OUTPUT_TERMS = (
    "image", "picture", "pic", "photo", "artwork", "portrait", "wallpaper", "scene", "scenery",
    "illustration", "painting", "drawing", "sketch", "canvas", "visual", "acrylic", "acrilic", "arcilic",
    "watercolor", "watercolour", "digital art", "concept art", "3d art",
)
_IMAGE_SEARCH_MARKERS = (
    "search image", "search photo", "find image", "find photo", "real image", "real photo",
    "real picture", "look up image", "lookup image",
)


def is_tool_capability_discussion(prompt: str) -> bool:
    """Return True for questions/debugging about tools rather than requests to run one."""
    text = re.sub(r"\s+", " ", str(prompt or "").lower()).strip()
    if not text or not any(term in text for term in ("tool", "tool calling", "feature", "pipeline", "capability")):
        return False
    if re.search(r"\b(?:by|and then)\s+(?:generate|create|draw|paint|render)\b", text):
        return False
    meta_terms = (
        "why", "how does", "how do", "do we have", "do you have", "can it", "can you use",
        "available", "enabled", "working", "work properly", "check", "debug", "inspect", "fix",
        "approach it", "capability", "feature", "pipeline",
    )
    return any(term in text for term in meta_terms)


def is_compound_email_media_request(prompt: str) -> bool:
    """Identify image generation requests whose result must update an email draft."""
    text = re.sub(r"\s+", " ", str(prompt or "")).lower().strip()
    if not text or is_tool_capability_discussion(text):
        return False
    if re.search(r"\b(?:reference|real|actual|existing|stock)\b", text):
        return False
    has_generation = bool(re.search(r"\b(?:generate|create|draw|paint|render|make|produce)\b", text))
    has_visual = bool(re.search(r"\b(?:image|photo|picture|artwork|illustration)\b", text))
    has_attachment = bool(re.search(r"\b(?:attach|add|include)\b", text))
    has_email_surface = bool(re.search(r"\b(?:email|mail|draft|widget|template)\b", text))
    return has_generation and has_visual and has_attachment and has_email_surface

def is_image_generation_request(prompt: str) -> bool:
    """Detect an actionable creative-image request without firing on tool discussion."""
    text = re.sub(r"\s+", " ", str(prompt or "").lower()).strip(" .?!")
    text = re.sub(r"\b(?:genetrate|genrate|generete)\b", "generate", text)
    if not text or any(marker in text for marker in _IMAGE_SEARCH_MARKERS):
        return False
    if is_tool_capability_discussion(text):
        return False

    has_visual_term = any(term in text for term in _IMAGE_OUTPUT_TERMS)
    stripped = re.sub(
        r"^(?:(?:ok(?:ay)?|please|now|hey|hello|hi|just)\s+)*(?:(?:can|could|would|will)\s+you\s+)?",
        "",
        text,
    ).strip()

    if re.match(r"^(?:draw|paint|sketch|illustrate)\b", stripped):
        return True
    if has_visual_term and re.match(r"^(?:generate|create|make|render|produce|design)\b", stripped):
        return True
    if has_visual_term and re.match(r"^use\s+(?:the\s+)?(?:image\s+)?(?:generation\s+)?tool\s+to\s+(?:generate|create|make|draw|paint|render)\b", stripped):
        return True
    if has_visual_term and re.search(
        r"\b(?:i|we)\s+(?:want|need|would like|'d like)\s+(?:(?:you\s+)?to\s+(?:see|have|generate|create|make|draw|paint|sketch|render|produce)|(?:an?|the)\s+)",
        text,
    ):
        return True
    if has_visual_term and re.match(r"^(?:show|give)\s+me\s+", stripped) and any(
        medium in text for medium in ("acrylic", "acrilic", "arcilic", "watercolor", "watercolour", "artwork", "illustration", "painting", "drawing", "sketch", "concept art")
    ):
        return True
    if re.search(r"\bcontent\s+will\s+be\s+an?\s+(?:image|picture|artwork)\b", text):
        return True
    return False

def specialist_for_prompt(prompt: str, *, swarm: bool = False) -> str:
    text = str(prompt or "").lower()
    if swarm:
        return "manager"
    if any(keyword in text for keyword in EMAIL_KEYWORDS):
        return "secretary"
    if any(keyword in text for keyword in CODE_KEYWORDS):
        return "developer"
    if any(keyword in text for keyword in VISUAL_KEYWORDS):
        return "artist"
    return "generalist"


def _clean_classifier_text(value) -> str:
    raw = str(value or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    return raw


def analyze_prompt_via_llm(
    user_prompt: str,
    target_model: str,
    *,
    is_cloud_model,
    get_cloud_config,
    get_cloud_api_key,
    ollama_url: str,
    logger,
) -> dict | None:
    system_prompt = (
        "Analyze the user prompt and return only JSON with keys requires_tools (boolean), complexity "
        "(direct, single, or swarm), and category (email, visual, code, search, memory, or casual). "
        "Available external actions are web search, real-image search, creative image generation, email drafting, "
        "approved email delivery, and scoped memory recall/archive. Code writing, debugging, explanation, planning, "
        "summarization, and ordinary conversation are direct and require no tool. Use single for one external action "
        "and swarm only when two or more external actions must be coordinated."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Prompt: {user_prompt}"},
    ]
    try:
        if is_cloud_model(target_model):
            import litellm

            config = get_cloud_config(target_model)
            response = litellm.completion(
                model=config["classifier_model"],
                messages=messages,
                api_key=get_cloud_api_key(target_model),
                temperature=0.0,
                max_tokens=40,
                timeout=4.0,
            )
            raw = _clean_classifier_text(getattr(response.choices[0].message, "content", None))
        else:
            response = requests.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": target_model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.0, "num_predict": 40},
                },
                timeout=6.0,
                verify=False,
            )
            response.raise_for_status()
            raw = _clean_classifier_text(response.json().get("message", {}).get("content"))
        if not raw:
            return None
        data = json.loads(raw)
        complexity = data.get("complexity")
        return {
            "requires_tools": bool(data.get("requires_tools", False)),
            "complexity": complexity if complexity in {"direct", "single", "swarm"} else "direct",
            "category": data.get("category", "casual"),
        }
    except Exception as exc:
        logger.warning("[Prompt Analyzer] Failed structured analysis (%s)", type(exc).__name__)
        return None
