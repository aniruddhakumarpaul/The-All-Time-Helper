CODE_KEYWORDS = [
    "code", "bug", "logic", "python", "javascript", "html", "css", "develop", "compile", "debug", "git",
    "refactor", "function", "class",
]
VISUAL_KEYWORDS = [
    "draw", "paint", "sketch", "scetch", "generate", "create", "artwork", "photo of", "show me a picture of",
    "real picture of", "look like", "image", "shot", "wallpaper", "render", "pics", "pic", "capture",
    "acrylic", "acrilic", "drawing", "drawin", "painting", "panting", "illustration", "portrait", "potrait",
    "canvas", "sketching",
]
EMAIL_KEYWORDS = [
    "email", "send", "sent", "dispatch", "mail", "forward", "admin_key_provided", "to him", "to her",
    "to them", "tell him", "tell her", "tell them", "message him", "message her",
]


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
        "Analyze the user prompt and return only JSON with keys requires_tools (boolean), "
        "complexity (direct, single, or swarm), and category (email, visual, code, search, or casual)."
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
            raw = response.choices[0].message.content.strip()
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
            raw = response.json().get("message", {}).get("content", "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
        data = json.loads(raw)
        complexity = data.get("complexity")
        return {
            "requires_tools": bool(data.get("requires_tools", False)),
            "complexity": complexity if complexity in {"direct", "single", "swarm"} else "direct",
            "category": data.get("category", "casual"),
        }
    except Exception as exc:
        logger.warning(f"[Prompt Analyzer] Failed structured analysis: {exc}")
        return None
import json
import re

import requests
