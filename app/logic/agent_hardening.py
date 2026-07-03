import json
import re
from typing import Callable


def harden_result(
    result,
    *,
    target_model: str,
    intent: dict | None,
    user_prompt: str | None,
    is_cloud_model: Callable[[str], bool],
    rewrite_pollinations_images: Callable[[str], str],
    is_pasted_explanation: Callable[[str], bool],
    looks_like_tool_leak: Callable[[str], bool],
    email_payload_from_json: Callable[[str], str | None],
    image_generate: Callable[..., str],
    logger,
):
    """Sanitize model output and recover only deterministic supported payloads."""
    if not result:
        return result

    for marker in ("### System:", "STRICT RULE:", "Your personal goal is:", "Role:", "Goal:", "Backstory:"):
        if marker in str(result):
            result = str(result).split(marker)[0].strip()

    result = rewrite_pollinations_images(str(result))
    if (
        intent
        and not intent.get("requires_tools")
        and intent.get("complexity") == "direct"
        and is_pasted_explanation(user_prompt or "")
        and looks_like_tool_leak(str(result))
    ):
        logger.warning("[Agents] Suppressed raw tool-call leak for direct pasted technical explanation request")
        return (
            "I could not produce a valid direct explanation because the model returned an invalid tool-call plan. "
            "Please retry the same explanation request."
        )

    result_text = str(result).strip()
    if "EMAIL_DRAFT_PAYLOAD:" not in result_text and "send_email_tool" in result_text:
        payload = email_payload_from_json(result_text)
        if payload:
            logger.warning("[Agents] Converted leaked send_email_tool JSON plan into email draft payload")
            return payload

    if target_model and is_cloud_model(target_model):
        return result
    if "EMAIL_DRAFT_PAYLOAD:" in result_text:
        return result

    json_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", result_text)
    if not json_match:
        return result
    try:
        candidate = json.loads(json_match.group(1))
    except Exception:
        return result

    def find_email_dict(data):
        if isinstance(data, dict):
            if any(key in data for key in ("recipient", "to")) and "subject" in data and "body" in data:
                return data
            for value in data.values():
                found = find_email_dict(value)
                if found:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = find_email_dict(item)
                if found:
                    return found
        return None

    email_data = find_email_dict(candidate)
    if email_data:
        draft = {
            "recipient": email_data.get("recipient") or email_data.get("to"),
            "subject": email_data.get("subject"),
            "body": email_data.get("body"),
            "tone": email_data.get("tone", "modern"),
            "attachment_content": email_data.get("attachment_content"),
            "attachment_filename": email_data.get("attachment_filename") or "report.txt",
        }
        prefix = result_text.split(json_match.group(1))[0].strip()
        prefix = re.sub(r"```json\s*$", "", prefix).strip()
        prefix = re.sub(r"```\s*$", "", prefix).strip()
        payload = f"EMAIL_DRAFT_PAYLOAD:{json.dumps(draft)}"
        return f"{prefix}\n\n{payload}" if prefix else payload

    def contains_send_plan(data):
        if isinstance(data, dict):
            return "send_email_tool" in data or any(contains_send_plan(value) for value in data.values())
        if isinstance(data, list):
            return any(contains_send_plan(item) for item in data)
        return False

    def find_image_description(data):
        if isinstance(data, dict):
            image_plan = data.get("image_generate_tool")
            if isinstance(image_plan, dict):
                return image_plan.get("description")
            for value in data.values():
                found = find_image_description(value)
                if found:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = find_image_description(item)
                if found:
                    return found
        return None

    description = None if contains_send_plan(candidate) else find_image_description(candidate)
    if description:
        try:
            return image_generate(description=description)
        except Exception as exc:
            logger.warning(f"[Agents] Failed to recover image tool plan from final answer: {exc}")
    return result
