import json
import re
from copy import deepcopy
from typing import Callable, Optional
from urllib.parse import unquote


_DRAFT_MARKERS = ("EMAIL_DRAFT_CONTEXT:", "EMAIL_DRAFT_PAYLOAD:")
_CONTEXT_BLOCK_RE = re.compile(r'\[Attached Context \d+\]\s*"""[\s\S]*?"""')


def _json_span_after_marker(text: str, marker: str) -> Optional[tuple[int, int]]:
    raw = str(text or "")
    marker_idx = raw.find(marker)
    if marker_idx == -1:
        return None
    payload = raw[marker_idx + len(marker):]
    start_idx = payload.find("{")
    if start_idx == -1:
        return None
    absolute_start = marker_idx + len(marker) + start_idx
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start_idx, len(payload)):
        ch = payload[idx]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return absolute_start, marker_idx + len(marker) + idx + 1
    return None


def extract_email_draft_from_prompt(prompt: str) -> Optional[dict]:
    raw = str(prompt or "")
    for marker in _DRAFT_MARKERS:
        span = _json_span_after_marker(raw, marker)
        if not span:
            continue
        start, end = span
        try:
            draft = json.loads(raw[start:end])
        except Exception:
            continue
        if isinstance(draft, dict):
            return draft
    return None


def latest_email_draft_from_history(history: list | None) -> Optional[dict]:
    for message in reversed(history or []):
        if isinstance(message, dict):
            content = str(message.get("content") or message.get("c") or "")
        else:
            content = str(message or "")
        draft = extract_email_draft_from_prompt(content)
        if draft:
            return draft
    return None


def clean_prompt_without_attached_context(prompt: str) -> str:
    cleaned = _CONTEXT_BLOCK_RE.sub("", str(prompt or ""))
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def is_generated_image_email_draft_request(prompt: str) -> bool:
    raw = str(prompt or "")
    if not any(marker in raw for marker in _DRAFT_MARKERS):
        return False
    clean = clean_prompt_without_attached_context(raw).lower()
    if not clean:
        return False
    if any(term in clean for term in ("search image", "search photo", "find image", "find photo", "real image", "real photo")):
        return False
    return bool(
        re.search(r"\bcontent\s+will\s+be\s+an?\s+image\b", clean)
        or re.search(r"\b(generate|create|make|draw|paint|sketch|render)\s+(?:[a-z0-9]+\s+){0,8}(image|picture|pic|photo|artwork|portrait|wallpaper|scene|illustration)\b", clean)
        or re.search(r"\bimage\s+of\s+", clean)
    )


def image_description_from_prompt(prompt: str) -> str:
    clean = clean_prompt_without_attached_context(prompt)
    clean = re.sub(r"(?i)^\s*(please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)?", "", clean)
    clean = re.sub(r"(?i)^\s*content\s+will\s+be\s+an?\s+image\s+(of|about|depicting)?\s*", "", clean)
    clean = re.sub(r"(?i)^\s*(generate|create|make|draw|paint|sketch|render)\s+(me\s+)?(an?\s+)?(image|picture|pic|photo|artwork|illustration)\s*(of\s+)?", "", clean)
    clean = re.sub(r"(?i)^\s*(generate|create|make|draw|paint|sketch|render)\s+(me\s+)?", "", clean)
    clean = re.sub(r"[?.!]+$", "", clean).strip()
    return clean or "a polished creative image"


def extract_generated_image_url(tool_result: str) -> Optional[str]:
    markdown_match = re.search(r'!\[[^\]]*\]\(([^)]+)\)', str(tool_result or ""))
    if markdown_match:
        return markdown_match.group(1)
    url_match = re.search(r'https?://\S+', str(tool_result or ""))
    if url_match:
        return url_match.group(0).rstrip(').,')
    return None


def filename_from_description(description: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(description or "generated_image")).strip("_").lower()
    return f"{(slug or 'generated_image')[:42]}_image.png"


def _draft_attachment_hint(draft: dict) -> str:
    names = []
    if draft.get("attachment_filename"):
        names.append(str(draft.get("attachment_filename")))
    for item in draft.get("attachments") or []:
        if isinstance(item, dict):
            name = item.get("filename") or item.get("name")
            if name:
                names.append(str(name))
    for name in names:
        stem = re.sub(r"\.[a-zA-Z0-9]{2,5}$", "", unquote(name))
        stem = re.sub(r"[_\-]+", " ", stem).strip()
        stem = re.sub(r"\bimage\b", "", stem, flags=re.I).strip()
        if stem:
            return stem
    subject = str(draft.get("subject") or "").strip()
    return subject or "the attached image"


def _looks_like_body_fill_text(prompt: str) -> bool:
    clean = clean_prompt_without_attached_context(prompt).lower()
    if not clean:
        return False
    has_body_target = any(term in clean for term in (
        "body", "content", "message", "email text", "email copy", "make relevant", "make relevent", "relevant body", "relevent body"
    ))
    has_write_action = any(term in clean for term in (
        "write", "compose", "draft", "fill", "generate", "make something", "make relevant", "make relevent", "i am lazy", "i'm lazy", "lazy"
    ))
    return has_body_target and has_write_action


def is_email_body_fill_request(prompt: str) -> bool:
    raw = str(prompt or "")
    if not any(marker in raw for marker in _DRAFT_MARKERS):
        return False
    return _looks_like_body_fill_text(raw)


def _fallback_body_for_draft(draft: dict, prompt: str) -> str:
    subject = str(draft.get("subject") or "").strip()
    hint = _draft_attachment_hint(draft)
    recipient = str(draft.get("recipient") or draft.get("to") or "").strip()
    greeting = "Hi," if not recipient else "Hi,"
    subject_lower = subject.lower()
    if "horror" in subject_lower or "doll" in subject_lower or "annable" in hint.lower() or "annabelle" in hint.lower():
        return (
            f"{greeting}\n\n"
            f"I have attached the requested image featuring {hint}. It has been created with a dim, atmospheric style and a realistic horror-inspired look.\n\n"
            "Please review the attachment and let me know if you would like any changes to the mood, lighting, or overall visual direction.\n\n"
            "Best regards,"
        )
    return (
        f"{greeting}\n\n"
        f"I have attached the requested file related to {hint}. Please review it when you have a moment.\n\n"
        "Let me know if you would like any changes or additional details included.\n\n"
        "Best regards,"
    )


def _is_blankish(value: object) -> bool:
    return not str(value or "").strip()


def _extract_prompt_email(prompt: str) -> str | None:
    match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", clean_prompt_without_attached_context(prompt))
    return match.group(0) if match else None


def _extract_prompt_subject(prompt: str) -> str | None:
    clean = clean_prompt_without_attached_context(prompt)
    for raw_line in clean.splitlines():
        line = raw_line.strip(" -:\t")
        if not line or re.search(r"[\w.+-]+@[\w.-]+\.\w+", line):
            continue
        lowered = line.lower()
        if any(term in lowered for term in ("body", "lazy", "write", "compose", "draft", "make relevant", "make relevent")):
            continue
        if len(line) <= 80:
            return line
    return None


def _apply_prompt_details_to_draft(draft: dict, prompt: str) -> None:
    prompt_email = _extract_prompt_email(prompt)
    if prompt_email and _is_blankish(draft.get("recipient") or draft.get("to")):
        draft["recipient"] = prompt_email
    prompt_subject = _extract_prompt_subject(prompt)
    current_subject = str(draft.get("subject") or "").strip().lower()
    if prompt_subject and (not current_subject or current_subject in {"image attachment", "generated image", "requested content", "email draft"}):
        draft["subject"] = prompt_subject


def _body_update_payload_from_draft(draft: dict, prompt: str, *, logger=None) -> str:
    updated = deepcopy(draft)
    updated.setdefault("recipient", updated.get("to") or "")
    updated.setdefault("subject", "Requested Content")
    updated.setdefault("tone", "modern")
    _apply_prompt_details_to_draft(updated, prompt)
    updated["body"] = _fallback_body_for_draft(updated, prompt)
    if logger:
        logger.info("[EmailWidget] Filled email draft body from targeted draft context.")
    return f"EMAIL_DRAFT_PAYLOAD:{json.dumps(updated)}"


def build_email_draft_body_update_payload(prompt: str, *, logger=None) -> Optional[str]:
    if not is_email_body_fill_request(prompt):
        return None
    draft = extract_email_draft_from_prompt(prompt)
    if not draft:
        return None
    return _body_update_payload_from_draft(draft, prompt, logger=logger)


def build_email_draft_body_update_payload_from_history(prompt: str, history: list | None, *, logger=None) -> Optional[str]:
    if not _looks_like_body_fill_text(prompt):
        return None
    draft = extract_email_draft_from_prompt(prompt) or latest_email_draft_from_history(history)
    if not draft:
        return None
    return _body_update_payload_from_draft(draft, prompt, logger=logger)


def build_generated_image_email_draft_payload(
    prompt: str,
    image_generate: Callable[[str], str],
    *,
    status_callback=None,
    logger=None
) -> Optional[str]:
    if not is_generated_image_email_draft_request(prompt):
        return None
    draft = extract_email_draft_from_prompt(prompt)
    if not draft:
        return None

    description = image_description_from_prompt(prompt)
    if status_callback:
        status_callback("🎨 Generating image for email widget...")
    if logger:
        logger.info(f"[EmailWidget] Generating image for draft attachment: '{description}'")

    tool_result = image_generate(description=description)
    image_url = extract_generated_image_url(tool_result)
    if not image_url:
        return f"ERROR: Image generation did not return an attachable URL. Result: {tool_result}"

    filename = filename_from_description(description)
    updated = deepcopy(draft)
    updated.setdefault("recipient", updated.get("to") or "")
    updated.setdefault("subject", "Generated Image")
    updated.setdefault("body", "")
    updated.setdefault("tone", "modern")
    updated["attachment_content"] = image_url
    updated["attachment_filename"] = filename
    updated["attachment_type"] = "image/png"
    updated["attachments"] = [{
        "content": image_url,
        "filename": filename,
        "name": filename,
        "type": "image/png",
        "content_type": "image/png",
    }]
    return f"EMAIL_DRAFT_PAYLOAD:{json.dumps(updated)}"
