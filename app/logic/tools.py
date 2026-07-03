import base64
import html
import json
import mimetypes
import os
import re
import smtplib
import time
from contextvars import ContextVar
from urllib.parse import urlparse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import requests
from crewai.tools import tool
from dotenv import load_dotenv
from app.logic.memory import query_memory, log_insight, admin_auth_context
from app.logger import logger
from app.logic.upscaler import UpscaleManager
from app.security import verify_admin_key
from app.logic.attachment_store import detect_file_type, resolve_attachment_reference
from app.logic.exceptions import AgentFastExit
from app.logic.safe_fetch import SafeFetchError, safe_fetch_url


active_history_context: ContextVar[list] = ContextVar("active_history_context", default=[])


@tool("web_search_text")
def search_tool(query: str) -> str:
    """Useful for searching the web for text-based information, news, and technical questions."""
    try:
        from ddgs import DDGS
        import time as _time

        query = query.strip()
        logger.info(f"DEBUG: [Search] Query (ddgs v9): {query}")

        results = []
        ddgs = DDGS()

        if any(kw in query.lower() for kw in ["recent", "news", "latest", "events", "politics"]):
            try:
                results.extend(ddgs.news(query, max_results=5))
                logger.info(f"DEBUG: [Search] News returned {len(results)} results")
            except Exception as ne:
                logger.error(f"DEBUG: [Search] News failed: {ne}")
                _time.sleep(1)

        if len(results) < 3:
            try:
                web_results = ddgs.text(query, max_results=5)
                results.extend(web_results)
                logger.info(f"DEBUG: [Search] Web returned {len(web_results)} results")
            except Exception as te:
                logger.error(f"DEBUG: [Search] Web failed: {te}")

        if not results:
            return "No reliable results found. The search engine may be temporarily unavailable."

        output = []
        for r in results[:8]:
            title = r.get('title', 'N/A')
            snippet = r.get('body', r.get('snippet', r.get('description', 'N/A')))
            url = r.get('href', r.get('link', r.get('url', 'N/A')))
            output.append(f"Title: {title}\nSnippet: {snippet}\nURL: {url}\n")
        return "\n---\n".join(output)
    except Exception as e:
        logger.error(f"DEBUG: [Search] Global Failure: {str(e)}")
        return f"Search Error: {str(e)}"


def _render_safe_markdown(text: str) -> str:
    def render_inline(value: str) -> str:
        parts = []
        cursor = 0
        for match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", value):
            parts.append(html.escape(value[cursor:match.start()]))
            label, target = match.group(1), match.group(2).strip()
            parsed = urlparse(target)
            if parsed.scheme.lower() in {"http", "https", "mailto"}:
                parts.append(
                    f'<a href="{html.escape(target, quote=True)}" rel="noopener noreferrer">{html.escape(label)}</a>'
                )
            else:
                parts.append(html.escape(label))
            cursor = match.end()
        parts.append(html.escape(value[cursor:]))
        return "".join(parts).replace("\n", "<br>")

    rendered = []
    cursor = 0
    for match in re.finditer(r"```[^\n]*\n(.*?)```", text, flags=re.DOTALL):
        rendered.append(render_inline(text[cursor:match.start()]))
        rendered.append(f"<pre><code>{html.escape(match.group(1))}</code></pre>")
        cursor = match.end()
    rendered.append(render_inline(text[cursor:]))
    return "".join(rendered)


def _build_html_body(personalized_body: str, tone: str) -> str:
    formatted_body = _render_safe_markdown(str(personalized_body or ""))
    tone_config = {
        "formal": {"bg": "#f9fafb", "h_bg": "#ffffff", "txt": "#111827", "h_txt": "EXECUTIVE CORRESPONDENCE", "h_sub": "Official Communication Layer", "border": "2px solid #111827"},
        "informal": {"bg": "#fdf2f8", "h_bg": "#fce7f3", "txt": "#be185d", "h_txt": "Hello there!", "h_sub": "A message from your friend's AI", "border": "1px solid #fce7f3"},
        "modern": {"bg": "#ffffff", "h_bg": "linear-gradient(135deg, #6366f1 0%, #a855f7 100%)", "txt": "white", "h_txt": "The All Time Helper", "h_sub": "Your AI Executive Assistant", "border": "1px solid #e5e7eb"}
    }
    t = tone_config.get(tone, tone_config["modern"])
    return f"""
    <html>
        <body style="font-family: 'Inter', Arial, sans-serif; margin: 0; padding: 40px; background-color: {t['bg']};">
            <div style="max-width: 650px; margin: auto; border: {t['border']}; border-radius: 8px; overflow: hidden; background: white; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
                <div style="background: {t['h_bg']}; padding: 30px; text-align: center; color: {t['txt']}; border-bottom: 1px solid #e5e7eb;">
                    <h1 style="margin: 0; font-size: 24px; text-transform: uppercase; letter-spacing: 2px;">{t['h_txt']}</h1>
                    <p style="margin: 8px 0 0; opacity: 0.7; font-size: 14px;">{t['h_sub']}</p>
                </div>
                <div style="padding: 40px; color: #1f2937; line-height: 1.8; font-size: 16px; white-space: pre-wrap; height: auto;">{formatted_body}</div>
                <div style="padding: 20px; text-align: center; font-size: 12px; color: #9ca3af; background: #f9fafb;">Dispatched via All Time Helper Secure Swarm</div>
            </div>
        </body>
    </html>
    """


def _authorized_for_sensitive_tool() -> bool:
    return verify_admin_key(admin_auth_context.get())


def _download_image_attachment(url: str):
    try:
        response = safe_fetch_url(
            url,
            headers={"Accept": "image/*"},
            timeout=30,
            max_bytes=10 * 1024 * 1024,
            request_get=requests.get,
        )
        if response.status_code != 200:
            return None, None, f"Image download failed with status {response.status_code}."
        content_type = (response.headers.get("content-type") or response.headers.get("Content-Type") or "").lower()
        extension = detect_file_type(response.content)
        if not content_type.startswith("image/") or extension not in {"png", "jpg", "gif", "webp"}:
            return None, None, "Remote attachment is not a supported image."
        return base64.b64encode(response.content).decode("ascii"), extension, None
    except (SafeFetchError, Exception) as exc:
        return None, None, f"Unable to fetch image attachment: {exc}"


def _prepare_attachment(content, filename: str, fallback: str = "attachment.png"):
    if isinstance(content, dict):
        prepared = dict(content)
        prepared["filename"] = prepared.get("filename") or prepared.get("name") or filename or fallback
        return prepared
    value = str(content or "").strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        encoded, extension, error = _download_image_attachment(value)
        if error:
            raise ValueError(error)
        return {"content": encoded, "filename": filename or f"attachment.{extension}"}
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1]
    return {"content": value, "filename": filename or fallback}


def _normalize_attachments(
    attachment_content=None,
    attachment_filename: str = "attachment.png",
    attachments=None,
):
    normalized = []
    if attachment_content:
        prepared = _prepare_attachment(attachment_content, attachment_filename, "attachment.png")
        if prepared:
            normalized.append(prepared)
    for index, item in enumerate(attachments or []):
        if not isinstance(item, dict):
            continue
        filename = item.get("filename") or item.get("name") or f"attachment-{index + 1}.png"
        source = item if item.get("id") and not item.get("content") and not item.get("data") else item.get("content") or item.get("data")
        prepared = _prepare_attachment(source, filename, filename)
        if prepared:
            for key in ("id", "name", "type", "content_type", "size", "sha256"):
                if item.get(key) is not None:
                    prepared[key] = item[key]
            duplicate = any(
                existing.get("id") == prepared.get("id") and prepared.get("id")
                or (
                    existing.get("filename") == prepared.get("filename")
                    and existing.get("content") == prepared.get("content")
                )
                for existing in normalized
            )
            if not duplicate:
                normalized.append(prepared)
    return normalized


def _resolve_send_attachments(attachments, owner: str | None):
    resolved = []
    for item in attachments:
        current = item
        if item.get("id"):
            if not owner:
                raise ValueError("Attachment owner is required for stored files.")
            current = resolve_attachment_reference(item, owner)
        content = current.get("content") or current.get("data")
        prepared = _prepare_attachment(content, current.get("filename") or current.get("name"), "attachment.png")
        if not prepared:
            continue
        try:
            raw = base64.b64decode(prepared["content"], validate=True)
        except Exception as exc:
            raise ValueError(f"Attachment is not valid base64: {exc}") from exc
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError("Attachment exceeds 10 MB.")
        prepared["bytes"] = raw
        prepared["content_type"] = current.get("content_type") or current.get("type") or mimetypes.guess_type(prepared["filename"])[0] or "application/octet-stream"
        resolved.append(prepared)
    return resolved


def _offload_long_body(body: str, attachments: list):
    if len(body) < 800:
        return body, attachments
    technical = "```" in body or re.search(r"(?m)^#{1,6}\s|^\s*[-*]\s", body)
    extension = "md" if technical else "txt"
    note = "Please find the detailed technical content attached." if technical else "Please find the detailed content attached."
    attachments.insert(
        0,
        {
            "filename": f"email-body.{extension}",
            "bytes": body.encode("utf-8"),
            "content_type": "text/markdown" if technical else "text/plain",
        },
    )
    return note, attachments


def _valid_recipients(recipient: str) -> list[str]:
    candidates = [value.strip() for value in str(recipient or "").split(",")]
    return [value for value in candidates if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)]


def send_or_simulate_email(
    recipient: str,
    subject: str,
    body: str,
    attachments=None,
    *,
    tone: str = "modern",
    attachment_content=None,
    attachment_filename: str = "attachment.png",
    owner: str | None = None,
) -> str:
    """Deterministically validate and simulate or send an approved email."""
    recipients = _valid_recipients(recipient)
    if not recipients:
        return "ERROR: No valid recipients found."
    try:
        normalized = _normalize_attachments(attachment_content, attachment_filename, attachments)
        resolved = _resolve_send_attachments(normalized, owner)
    except ValueError as exc:
        return f"ERROR: {exc}"
    body, resolved = _offload_long_body(str(body or ""), resolved)

    mode = os.getenv("EMAIL_MODE", "SIMULATE").upper()
    if mode == "SIMULATE":
        with open("simulated_emails.log", "a", encoding="utf-8") as stream:
            stream.write(f"TO: {', '.join(recipients)}\nSUBJECT: {subject}\nBODY: {body}\n")
            for item in resolved:
                stream.write(f"Attachment: {item['filename']} ({len(item['bytes'])} bytes)\n")
            stream.write("---\n")
        return f"SIMULATE SUCCESS: Email prepared for {', '.join(recipients)}."

    if mode != "LIVE":
        return f"ERROR: Unsupported EMAIL_MODE '{mode}'."

    from app.logic.bus import job_id_context
    from app.logic.memory import user_context
    from app.database import DB_FILE
    import sqlite3

    current_job = job_id_context.get()
    if current_job:
        try:
            with sqlite3.connect(DB_FILE) as conn:
                row = conn.execute("SELECT status FROM email_send_log WHERE job_id = ?", (current_job,)).fetchone()
            if row:
                return row[0]
        except sqlite3.Error as exc:
            logger.warning(f"Email idempotency lookup failed: {exc}")

    sender_email = os.getenv("SENDER_EMAIL")
    sender_pwd = os.getenv("SENDER_PWD")
    if not sender_email or not sender_pwd:
        return "ERROR: SMTP credentials not configured."

    try:
        with smtplib.SMTP(os.getenv("SMTP_SERVER", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587")), timeout=30) as server:
            server.starttls()
            server.login(sender_email, sender_pwd)
            for target in recipients:
                message = MIMEMultipart("mixed")
                message["Subject"] = str(subject or "")[:998]
                message["From"] = f"The All Time Helper <{sender_email}>"
                message["To"] = target
                alternative = MIMEMultipart("alternative")
                alternative.attach(MIMEText(body, "plain", "utf-8"))
                alternative.attach(MIMEText(_build_html_body(body, tone), "html", "utf-8"))
                message.attach(alternative)
                for item in resolved:
                    main_type, sub_type = item["content_type"].split("/", 1)
                    part = MIMEBase(main_type, sub_type)
                    part.set_payload(item["bytes"])
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", "attachment", filename=item["filename"])
                    message.attach(part)
                server.send_message(message)
        result = f"LIVE SUCCESS: Email broadcasted to {', '.join(recipients)}."
        if current_job:
            try:
                with sqlite3.connect(DB_FILE) as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO email_send_log (job_id, user_email, recipients, status, timestamp) VALUES (?, ?, ?, ?, ?)",
                        (current_job, user_context.get(), ",".join(recipients), result, time.time()),
                    )
                    conn.commit()
            except sqlite3.Error as exc:
                logger.warning(f"Email idempotency write failed: {exc}")
        return result
    except Exception as exc:
        logger.error(f"SMTP error: {exc}", exc_info=True)
        return f"ERROR: Failed to send email. {exc}"


@tool("send_email_tool")
def send_email_tool(
    recipient: str,
    subject: str,
    body: str,
    raw_attachment_text: str = "",
    attachment_content: str = None,
    attachment_filename: str = "report.txt",
    is_html: bool = True,
    tone: str = "modern",
    attachments: list = None,
) -> str:
    """Build a validated email draft. This agent tool never performs SMTP delivery."""
    recipients = _valid_recipients(recipient)
    if not recipients:
        raise AgentFastExit("ERROR: No valid recipients found.")
    draft_body = str(body or "")
    if raw_attachment_text and raw_attachment_text.strip() not in draft_body:
        draft_body = f"{draft_body}\n\n{raw_attachment_text.strip()}".strip()
    try:
        normalized = _normalize_attachments(attachment_content, attachment_filename, attachments)
    except ValueError as exc:
        raise AgentFastExit(f"ERROR: {exc}")

    primary = normalized[0] if normalized else {}
    draft = {
        "recipient": ", ".join(recipients),
        "subject": str(subject or "")[:998],
        "body": draft_body,
        "tone": tone if tone in {"formal", "informal", "modern"} else "modern",
        "attachment_content": primary.get("content"),
        "attachment_filename": primary.get("filename") or attachment_filename,
    }
    if normalized:
        draft["attachments"] = normalized
    raise AgentFastExit(f"EMAIL_DRAFT_PAYLOAD:{json.dumps(draft)}")


def resolve_chat_images(reference: str, history: list | None = None, max_images: int = 6):
    found = []
    for message in reversed(history or active_history_context.get() or []):
        if not isinstance(message, dict):
            continue
        candidates = message.get("attachments") or message.get("img") or message.get("i") or []
        if not isinstance(candidates, list):
            candidates = [candidates]
        for index, item in enumerate(candidates):
            if isinstance(item, dict):
                content = item.get("content") or item.get("data")
                if content:
                    found.append((content, item.get("filename") or item.get("name") or f"image-{index + 1}.png"))
            elif isinstance(item, str) and item:
                found.append((item, f"image-{index + 1}.png"))
            if len(found) >= max_images:
                return found
        content = str(message.get("content") or message.get("c") or "")
        for url in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", content):
            filename = os.path.basename(urlparse(url).path) or "image.png"
            if url.startswith("/static/"):
                path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), url.lstrip("/"))
                if os.path.isfile(path):
                    with open(path, "rb") as stream:
                        found.append((base64.b64encode(stream.read()).decode("ascii"), filename))
            else:
                encoded, _, error = _download_image_attachment(url)
                if not error:
                    found.append((encoded, filename))
            if len(found) >= max_images:
                return found
    return found


def resolve_chat_image(reference: str, history: list | None = None):
    images = resolve_chat_images(reference, history, max_images=1)
    return images[0] if images else None


@tool("calculate_horoscope")
def calculate_horoscope(sign: str) -> str:
    """Provides a daily horoscope reading based on the user's zodiac sign."""
    readings = {
        "aries": "A day of bold action. Your energy is contagious.",
        "taurus": "Patience and persistence bring rewards today.",
        "gemini": "Communication is your strength. Share your ideas.",
        "cancer": "Trust your intuition. Home and family bring comfort.",
        "leo": "Creativity is your superpower today. Express yourself.",
        "virgo": "Attention to detail leads to excellence.",
        "libra": "Balance and harmony guide your decisions.",
        "scorpio": "Intuition is high. Trust your gut feelings.",
        "sagittarius": "Adventure calls. Embrace new perspectives.",
        "capricorn": "Stamina and focus will lead to major progress.",
        "aquarius": "Innovation sparks big breakthroughs today.",
        "pisces": "Empathy and creativity flow naturally for you."
    }
    sign = sign.lower().strip()
    return readings.get(sign, "The stars are in flux for you today. Stay open to new possibilities and be kind to yourself.")


@tool("analyze_palm_lines")
def analyze_palm_lines(palm_description: str) -> str:
    """Analyzes a description of palm lines to provide an entertainment reading."""
    return f"Based on your palm: '{palm_description}', I see a path of great resilience and emotional depth. Your lines suggest a major turning point approaching that will lead to spiritual growth."


POLLINATIONS_IMAGE_MODEL = "flux"
POLLINATIONS_IMAGE_HOST = "image.pollinations.ai"


@tool("image_generate_tool")
def image_generate_tool(description: str) -> str:
    """AI image generator for fictional, conceptual, fantasy, and creative requests."""
    import urllib.parse

    clean_desc = description.strip().replace('\n', ' ')
    encoded = urllib.parse.quote(clean_desc)
    seed = (abs(hash(clean_desc)) + int(time.time())) % 1000000

    try:
        base_url = f"https://{POLLINATIONS_IMAGE_HOST}/prompt/{encoded}?model={POLLINATIONS_IMAGE_MODEL}&width=1024&height=1024&nologo=true&seed={seed}"
        upscale_id = UpscaleManager.start_upscale(base_url)
        image_url_with_uid = f"{base_url}&uid={upscale_id}"
        logger.info(f"[ART ENGINE] Generated HD URL with UID: {image_url_with_uid}")
        return f"![{clean_desc}]({image_url_with_uid})"
    except Exception as e:
        logger.error(f"[ART ENGINE] Failed to trigger upscale: {e}")
        base_url = f"https://{POLLINATIONS_IMAGE_HOST}/prompt/{encoded}?model={POLLINATIONS_IMAGE_MODEL}&width=1024&height=1024&nologo=true&seed={seed}"
        return f"![{clean_desc}]({base_url})"


@tool("image_search_tool")
def image_search_tool(query: str) -> str:
    """Search for a real-world image of a product, person, place, vehicle, or other real entity."""
    try:
        from ddgs import DDGS
        ddgs = DDGS()
        results = ddgs.images(query, max_results=1)

        if not results:
            return "No reliable image results found."

        img_url = results[0]['image']
        return f"![{query}]({img_url})"
    except Exception as e:
        logger.error(f"Image Search Error: {str(e)}")
        return f"Image Search Error: {str(e)}"


@tool("recall_memory")
def recall_memory(query: str) -> str:
    """Semantically searches neural memory for code snippets, architectural decisions, and previous activity."""
    try:
        results = query_memory(query)
        if not results:
            return "No relevant memories found for this query."

        output = []
        for r in results:
            source = r['metadata'].get('file', 'Unknown')
            output.append(f"--- Memory from {source} ---\n{r['content']}\n")
        return "\n".join(output)
    except Exception as e:
        logger.error(f"Memory Retrieval Error: {str(e)}")
        return f"Memory Retrieval Error: {str(e)}"


@tool("archive_insight")
def archive_insight(title: str, body: str) -> str:
    """Permanently saves an architectural decision, user preference, or project milestone to neural memory."""
    try:
        log_insight(title, body)
        return f"Successfully archived insight: '{title}' to Neural Memory."
    except Exception as e:
        return f"Memory Archive Error: {str(e)}"
