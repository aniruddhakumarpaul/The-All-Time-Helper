import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable


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
        if status_callback:
            status_callback("Analyzing Visual Context...")
        image_keywords = (
            "this", "that", "image", "picture", "photo", "look", "see", "describe", "analyze", "what is",
            "tell me about", "color", "colour", "who", "where", "context",
        )
        refers_to_image = any(keyword in clean_prompt.lower() for keyword in image_keywords)
        image_description = "No image context available."
        prompt_with_image = user_prompt

        if img_data:
            images = runtime.image_items(img_data)
            local_urls = []
            for item in images:
                image_base64 = runtime.image_base64(item)
                local_url = runtime.save_image(image_base64) if image_base64 else None
                if local_url:
                    local_urls.append(local_url)
            if local_urls:
                markdown = "\n".join(f"![Uploaded Image]({url})" for url in local_urls)
                prompt_with_image = f"{markdown}\n{user_prompt}"
            if refers_to_image:
                descriptions = []
                for item in images:
                    image_base64 = runtime.image_base64(item)
                    vision_source = runtime.image_source(item)
                    if not intent["is_local"]:
                        description = runtime.process_cloud(image_base64, runtime.next_groq_key()) if image_base64 else None
                        description = description or (runtime.process_local(image_base64) if image_base64 else None)
                    else:
                        result = runtime.vision_system.analyze_chat_images([vision_source], clean_prompt) if vision_source else None
                        description = result["description"] if result else (runtime.process_local(image_base64) if image_base64 else None)
                    if description:
                        descriptions.append(description)
                if descriptions:
                    image_description = "\n".join(
                        f"Image {index + 1}: {description}" for index, description in enumerate(descriptions)
                    )
                return (
                    f"--- YOUR VISUAL PERCEPTION ---\n{image_description}\n--- END VISUAL PERCEPTION ---\n\n{user_prompt}",
                    image_description,
                )
        elif refers_to_image and history:
            image_urls = []
            for message in reversed(history):
                content = message.get("content", message.get("c", ""))
                matches = re.findall(r"!\[.*?\]\((https?://.*?|/static/.*?|/api/image_proxy.*?)\)", content)
                if matches:
                    image_urls.extend(reversed(matches))
                    if len(image_urls) >= 3:
                        break
            if image_urls:
                generic = ("how does the image look", "describe it", "what is this", "this", "in the picture")
                targets = [image_urls[0]] if any(item in clean_prompt.lower() for item in generic) else image_urls
                result = runtime.vision_system.analyze_chat_images(targets, clean_prompt)
                if result:
                    image_description = result["description"]
                    return (
                        f"--- CURRENT VISUAL FOCUS ---\nImage: {result['url']}\nActual Content: {image_description}\n"
                        f"--- END VISUAL FOCUS ---\n\n{user_prompt}",
                        image_description,
                    )
        return prompt_with_image, image_description

    def task_memory():
        prompt_lower = clean_prompt.lower()
        triggers = (
            "architecture", "code", "function", "file", "logic", "decide", "decision", "plan", "why did",
            "project", "helper", "memory", "database", "implement", "design",
        )
        if not intent.get("requires_tools") and not any(trigger in prompt_lower for trigger in triggers):
            return ""
        if status_callback:
            status_callback("Accessing Neural Memory...")
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
        final_prompt, image_description = vision_future.result()
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
        "resolved_email": resolve_recent_email(history),
    }
