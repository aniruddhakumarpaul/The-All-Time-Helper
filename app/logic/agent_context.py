import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from app.logic.agent_intent import is_image_generation_request


@dataclass(frozen=True)
class ContextRuntime:
    clean_prompt: Callable
    image_items: Callable
    image_base64: Callable
    image_source: Callable
    save_image: Callable
    process_cloud: Callable
    process_local: Callable
    next_groq_key: Callable
    vision_system: Any
    query_memory: Callable
    logger: Any


def build_history_context(history: list, user_prompt: str, requires_tools: bool) -> str:
    if not history:
        return ""
    lines = ["<history>"]
    limit = 5 if requires_tools else 15
    current_prompt = str(user_prompt or "").strip()
    for message in history[-limit:]:
        role_value = message.get("role", message.get("r", ""))
        role = "U" if role_value in {"user", "u"} else "A"
        content = str(message.get("content", message.get("c", ""))).strip()
        if current_prompt and content == current_prompt:
            continue
        if message.get("masked", False):
            content = "[MASKED_SECRET]"
        if len(content) > 3000:
            content = f"{content[:3000]}..."
        if content:
            lines.append(f"{role}: {content}")
    lines.append("</history>")
    return "\n" + "\n".join(lines) + "\n"


def resolve_recent_email(history: list) -> str | None:
    for message in reversed((history or [])[-15:]):
        content = str(message.get("content", message.get("c", "")))
        emails = re.findall(r"[\w.-]+@[\w.-]+\.\w+", content)
        if emails:
            return emails[-1]
    return None


def _is_image_creation_prompt(prompt: str) -> bool:
    return is_image_generation_request(prompt)


def _explicitly_references_existing_image(prompt: str) -> bool:
    text = str(prompt or "").lower().strip()
    if not text:
        return False
    explicit_phrases = (
        "this image", "that image", "the image", "above image", "previous image", "last image",
        "this picture", "that picture", "the picture", "above picture", "previous picture", "last picture",
        "this photo", "that photo", "the photo", "above photo", "previous photo", "last photo",
        "in the image", "in this image", "in that image", "in the picture", "in this picture",
        "look at this", "look at the image", "describe this", "describe the image", "what is this",
        "what's this", "what is in this", "what is shown", "based on this", "based on the image",
        "use this image", "use the image", "attach this image", "attach the image",
    )
    if any(phrase in text for phrase in explicit_phrases):
        return True
    return bool(re.fullmatch(r"(this|that|it|above|describe it|analyze it|what is it)", text))


def _should_analyze_history_image(clean_prompt: str) -> bool:
    if _is_image_creation_prompt(clean_prompt) and not _explicitly_references_existing_image(clean_prompt):
        return False
    return _explicitly_references_existing_image(clean_prompt)


def _native_image_input(item, runtime: ContextRuntime):
    source = runtime.image_source(item)
    prepare = getattr(runtime.vision_system, "prepare_image", None)
    if source and callable(prepare):
        prepared = prepare(source)
        if prepared:
            return {
                "base64": prepared["base64"],
                "data_url": prepared["data_url"],
                "media_type": prepared["media_type"],
                "sha256": prepared["sha256"],
                "byte_size": prepared["byte_size"],
            }
        return None

    encoded = runtime.image_base64(item)
    if not encoded:
        return None
    encoded = str(encoded).strip()
    media_type = "image/jpeg"
    if encoded.lower().startswith("data:image/") and "," in encoded:
        header, encoded = encoded.split(",", 1)
        media_type = header[5:].split(";", 1)[0].lower()
    elif isinstance(item, dict):
        candidate = str(item.get("content_type") or item.get("type") or "").lower()
        if candidate in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            media_type = candidate
    return {"base64": encoded, "data_url": f"data:{media_type};base64,{encoded}", "media_type": media_type}


def _is_visual_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return bool(item)
    content_type = str(item.get("content_type") or item.get("type") or "").lower()
    if content_type:
        return content_type.startswith("image/")
    filename = str(item.get("filename") or item.get("name") or "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")) or bool(
        item.get("content") or item.get("data") or item.get("attachment_content") or item.get("path")
    )


def assemble_context(
    user_prompt,
    img_data,
    history,
    intent,
    *,
    runtime: ContextRuntime,
    user_id=None,
    status_callback=None,
):
    clean_prompt = runtime.clean_prompt(user_prompt)

    def task_vision():
        prompt_lower = clean_prompt.lower()
        image_description = "No image context available."

        if img_data:
            images = [item for item in runtime.image_items(img_data) if _is_visual_item(item)][:3]
            native_inputs = []
            if intent.get("native_vision") or intent.get("fast_local_vision"):
                native_inputs = [value for value in (_native_image_input(item, runtime) for item in images) if value]
            if intent.get("native_vision") and native_inputs:
                return user_prompt, image_description, native_inputs

            if status_callback:
                status_callback("Analyzing visual context...")
            descriptions = []
            for item in images:
                image_base64 = runtime.image_base64(item)
                vision_source = runtime.image_source(item)
                if not intent["is_local"]:
                    cloud_vision_key = runtime.next_groq_key()
                    description = runtime.process_cloud(image_base64, cloud_vision_key) if image_base64 and cloud_vision_key else None
                    description = description or (runtime.process_local(image_base64) if image_base64 else None)
                else:
                    if vision_source and intent.get("fast_local_vision"):
                        result = runtime.vision_system.analyze_chat_images(
                            [vision_source], clean_prompt, allow_fallback=False
                        )
                    else:
                        result = runtime.vision_system.analyze_chat_images([vision_source], clean_prompt) if vision_source else None
                    description = result["description"] if result else None
                if description:
                    descriptions.append(str(description).strip())
            if descriptions:
                image_description = "\n".join(
                    f"Image {index + 1}: {description}" for index, description in enumerate(descriptions)
                )
                return (
                    f"--- YOUR VISUAL PERCEPTION ---\n{image_description}\n--- END VISUAL PERCEPTION ---\n\n{user_prompt}",
                    image_description,
                    [],
                )
            if native_inputs:
                return user_prompt, image_description, native_inputs
            return user_prompt, image_description, []
        if _should_analyze_history_image(clean_prompt) and history:
            image_sources = []
            for message in reversed(history):
                if not isinstance(message, dict):
                    continue
                candidates = message.get("attachments") or message.get("img") or message.get("i") or []
                if not isinstance(candidates, list):
                    candidates = [candidates]
                for item in candidates:
                    if not _is_visual_item(item):
                        continue
                    source = runtime.image_source(item)
                    if source and source not in image_sources:
                        image_sources.append(source)
                    if len(image_sources) >= 3:
                        break
                content = str(message.get("content", message.get("c", "")))
                matches = re.findall(r"!\[.*?\]\((https?://.*?|/static/.*?|/api/image_proxy.*?)\)", content)
                for match in reversed(matches):
                    if match not in image_sources:
                        image_sources.append(match)
                    if len(image_sources) >= 3:
                        break
                if len(image_sources) >= 3:
                    break

            if image_sources:
                native_inputs = []
                if intent.get("native_vision") or intent.get("fast_local_vision"):
                    native_inputs = [
                        value for value in (_native_image_input(source, runtime) for source in image_sources) if value
                    ]
                    if intent.get("native_vision") and native_inputs:
                        return user_prompt, image_description, native_inputs
                if status_callback:
                    status_callback("Analyzing visual context...")
                generic = ("how does the image look", "describe it", "what is this", "this", "in the picture")
                targets = [image_sources[0]] if any(item in prompt_lower for item in generic) else image_sources
                if intent.get("fast_local_vision"):
                    result = runtime.vision_system.analyze_chat_images(
                        targets, clean_prompt, allow_fallback=False
                    )
                else:
                    result = runtime.vision_system.analyze_chat_images(targets, clean_prompt)
                if result:
                    image_description = result["description"]
                    raw_reference = str(result.get("url") or "")
                    visible_reference = (
                        raw_reference
                        if raw_reference.startswith(("http://", "https://", "/static/")) and len(raw_reference) <= 200
                        else "uploaded attachment"
                    )
                    return (
                        f"--- CURRENT VISUAL FOCUS ---\nImage: {visible_reference}\nActual Content: {image_description}\n"
                        f"--- END VISUAL FOCUS ---\n\n{user_prompt}",
                        image_description,
                        [],
                    )
                if native_inputs:
                    return user_prompt, image_description, native_inputs
        return user_prompt, image_description, []

    def task_memory():
        prompt_lower = clean_prompt.lower()
        triggers = (
            "architecture", "code", "function", "file", "logic", "decide", "decision", "plan", "why did",
            "project", "helper", "memory", "database", "implement", "design",
        )
        if not intent.get("requires_tools") and not any(trigger in prompt_lower for trigger in triggers):
            return ""
        if status_callback:
            status_callback("Accessing neural memory...")
        memory_filter = None
        if any(keyword in prompt_lower for keyword in ("decide", "decision", "architecture", "plan", "why did")):
            memory_filter = {"type": "insight"}
        elif any(keyword in prompt_lower for keyword in ("code", "function", "file", "logic")):
            memory_filter = {"type": "code"}
        memories = runtime.query_memory(
            clean_prompt, n_results=5, filter_dict=memory_filter, threshold=0.95, user_id=user_id
        )
        if not memories:
            return ""
        return "\n<neural_context>\n" + "".join(f"- {item['content']}\n" for item in memories) + "</neural_context>\n"

    with ThreadPoolExecutor(max_workers=2) as executor:
        vision_future = executor.submit(task_vision)
        memory_future = executor.submit(task_memory)
        final_prompt, image_description, image_inputs = vision_future.result()
        try:
            memory_block = memory_future.result()
        except Exception as exc:
            runtime.logger.error(f"[Memory] Context assembly continuing without neural memory: {exc}", exc_info=True)
            memory_block = ""

    return {
        "final_prompt": final_prompt,
        "memory_block": memory_block,
        "history_context": build_history_context(history, user_prompt, intent.get("requires_tools", False)),
        "image_description": image_description,
        "image_inputs": image_inputs,
        "visual_input_present": bool(
            any(_is_visual_item(item) for item in runtime.image_items(img_data))
            or image_inputs
            or image_description != "No image context available."
        ),
        "resolved_email": resolve_recent_email(history),
    }