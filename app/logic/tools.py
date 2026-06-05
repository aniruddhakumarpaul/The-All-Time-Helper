import os
import smtplib
import time
from email.mime.text import MIMEText
from crewai.tools import tool
from app.logic.memory import query_memory, log_insight, user_context
from app.logger import logger
from app.logic.upscaler import UpscaleManager
from app.logic.attachment_store import AttachmentStoreError, resolve_attachment_reference
from contextvars import ContextVar
from typing import Optional, List, Any, Tuple
import base64
import requests
import re
import urllib.parse

active_history_context: ContextVar[Optional[List[dict]]] = ContextVar("active_history", default=None)

STOP_WORDS = {
    'a', 'about', 'above', 'after', 'again', 'against', 'all', 'am', 'an', 'and', 'any', 'are', 'as', 'at',
    'be', 'because', 'been', 'before', 'being', 'below', 'between', 'both', 'but', 'by', 'can', 'could',
    'did', 'do', 'does', 'doing', 'down', 'during', 'each', 'few', 'for', 'from', 'further', 'had', 'has',
    'have', 'having', 'he', 'her', 'here', 'hers', 'herself', 'him', 'himself', 'his', 'how', 'i', 'if', 'in',
    'into', 'is', 'it', 'its', 'itself', 'me', 'more', 'most', 'my', 'myself', 'no', 'nor', 'not', 'of', 'off',
    'on', 'once', 'only', 'or', 'other', 'ought', 'our', 'ours', 'ourselves', 'out', 'over', 'own', 'same',
    'she', 'should', 'so', 'some', 'such', 'than', 'that', 'the', 'their', 'theirs', 'them', 'themselves',
    'then', 'there', 'these', 'they', 'this', 'those', 'through', 'to', 'too', 'under', 'until', 'up', 'very',
    'was', 'we', 'were', 'what', 'when', 'where', 'which', 'while', 'who', 'whom', 'why', 'with', 'you', 'your',
    'yours', 'yourself', 'yourselves', 'please', 'attach', 'image', 'photo', 'picture', 'file', 'send', 'email',
    'generated', 'jpg', 'jpeg', 'png', 'gif', 'pdf', 'txt', 'csv', 'zip'
}

def is_base64(s: str) -> bool:
    if any(c.isspace() for c in s.strip()):
        return False
    try:
        s_clean = "".join(s.split())
        if not s_clean or len(s_clean) < 64:
            return False
        if not re.match(r'^[A-Za-z0-9+/]*={0,2}$', s_clean):
            return False
        base64.b64decode(s_clean, validate=True)
        return True
    except Exception:
        return False

def detect_extension_from_bytes(data: bytes) -> Optional[str]:
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    elif data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    elif data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
        return 'gif'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'webp'
    elif data.startswith(b'%PDF'):
        return 'pdf'
    return None


def _download_image_attachment(url: str, timeout: int = 60, min_bytes: int = 1024, attempts: int = 2) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Download a URL attachment and return (base64_content, extension, error)."""
    clean_url = str(url).strip().split("&uid=")[0]
    if not clean_url.startswith(("http://", "https://")):
        return None, None, "Attachment URL must start with http:// or https://"

    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            res = requests.get(clean_url, timeout=timeout)
            if res.status_code != 200:
                last_error = f"HTTP {res.status_code}"
            else:
                content = res.content or b""
                detected_ext = detect_extension_from_bytes(content)
                content_type = (res.headers.get("content-type", "") if hasattr(res, "headers") else "").lower()
                looks_like_image = detected_ext in {"png", "jpg", "gif", "webp"} or content_type.startswith("image/")

                if len(content) < min_bytes:
                    last_error = f"downloaded content too small ({len(content)} bytes)"
                elif not looks_like_image:
                    last_error = f"downloaded content is not an image (content-type: {content_type or 'unknown'})"
                else:
                    if not detected_ext:
                        if "jpeg" in content_type or "jpg" in content_type:
                            detected_ext = "jpg"
                        elif "png" in content_type:
                            detected_ext = "png"
                        elif "gif" in content_type:
                            detected_ext = "gif"
                        elif "webp" in content_type:
                            detected_ext = "webp"
                    return base64.b64encode(content).decode("utf-8"), detected_ext or "png", None
        except Exception as e:
            last_error = str(e)

        if attempt < attempts:
            time.sleep(2)

    return None, None, last_error or "unknown download failure"


def _filename_with_detected_extension(filename: str, detected_ext: Optional[str], fallback: str = "image") -> str:
    if not detected_ext:
        return filename or f"{fallback}.png"
    if not filename or filename == "report.txt":
        return f"{fallback}.{detected_ext}"
    base_name, _ = os.path.splitext(filename)
    return f"{base_name}.{detected_ext}"


def _attachment_size_bytes(attachment_content: Optional[str]) -> int:
    if not attachment_content:
        return 0
    if isinstance(attachment_content, list):
        return sum(_attachment_size_bytes(item.get("content") if isinstance(item, dict) else item) for item in attachment_content)
    content = str(attachment_content).strip()
    try:
        if is_base64(content):
            return len(base64.b64decode("".join(content.split()), validate=True))
    except Exception:
        pass
    return len(content.encode("utf-8"))


def _attachment_label(attachments: list, fallback_filename: str = "report.txt") -> str:
    if not attachments:
        return f"{fallback_filename} (0 bytes)"
    labels = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        filename = att.get("filename") or att.get("name") or fallback_filename
        size = att.get("size")
        if size is None:
            size = _attachment_size_bytes(att.get("content"))
        labels.append(f"{filename} ({size} bytes)")
    return ", ".join(labels)


def _looks_like_code_or_markdown(text: str) -> bool:
    text = text or ""
    lowered = text.lower()

    code_signals = [
        "```",
        "def ",
        "class ",
        "import ",
        "from ",
        "function ",
        "const ",
        "let ",
        "var ",
        "return ",
        "try:",
        "except ",
        "if __name__",
        "pip install",
        "npm ",
        "faiss.",
        "sentence_transformer",
    ]

    markdown_signals = [
        "# ",
        "## ",
        "- ",
        "* ",
        "|",
        "copy\nsave",
    ]

    return any(sig in lowered for sig in code_signals) or sum(sig in lowered for sig in markdown_signals) >= 2


def _prepare_email_send_body(body: str) -> tuple[str, Optional[dict]]:
    body_text = str(body or "")
    cleaned = body_text.strip()
    if not cleaned:
        return body_text, None

    body_length = len(cleaned)
    line_count = cleaned.count("\n") + 1
    should_offload = body_length >= 600 or (body_length >= 400 and line_count >= 4)
    if not should_offload:
        return body_text, None

    looks_technical = _looks_like_code_or_markdown(cleaned)
    body_filename = "email-body.md" if looks_technical else "email-body.txt"
    note = (
        "Please find the detailed technical content attached."
        if looks_technical
        else "Please find the detailed content attached."
    )

    attachment = _prepare_attachment(body_text, body_filename, fallback="email-body")
    if attachment:
        attachment["name"] = body_filename
        attachment["filename"] = body_filename
        attachment["content_type"] = "text/markdown" if looks_technical else "text/plain"
        attachment["type"] = attachment["content_type"]
        return note, attachment

    return body_text, None


def _prepare_attachment(content: Any, filename: str = "report.txt", fallback: str = "attachment") -> Optional[dict]:
    if content is None or len(str(content).strip()) == 0:
        return None
    content_str = str(content).strip()
    attachment_filename = filename or "report.txt"

    markdown_match = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)$', content_str)
    if markdown_match:
        alt_text = markdown_match.group(1)
        content_str = markdown_match.group(2)
        if (not attachment_filename or attachment_filename == "report.txt") and alt_text:
            clean_alt = re.sub(r'[^\w\s]', '_', alt_text).strip().replace(' ', '_')
            attachment_filename = f"{clean_alt[:30]}.png"

    if content_str.startswith("http://") or content_str.startswith("https://"):
        img_b64, detected_ext, error = _download_image_attachment(content_str)
        if not img_b64:
            logger.error(f"[Email Attachment] Error fetching URL {content_str}: {error}")
            from app.logic.exceptions import AgentFastExit
            raise AgentFastExit(f"ERROR: Failed to download image attachment bytes from URL: {error}")
        content_str = img_b64
        attachment_filename = _filename_with_detected_extension(
            attachment_filename,
            detected_ext,
            fallback=f"{fallback}_{int(time.time())}",
        )
    elif content_str.startswith("/static/") or content_str.startswith("static/"):
        local_path = content_str.lstrip("/")
        if os.path.exists(local_path):
            with open(local_path, "rb") as f:
                content_str = base64.b64encode(f.read()).decode("utf-8")
            if not attachment_filename or attachment_filename == "report.txt":
                attachment_filename = os.path.basename(local_path)

    if is_base64(content_str):
        try:
            clean_content = "".join(content_str.split())
            decoded_bytes = base64.b64decode(clean_content, validate=True)
            detected_ext = detect_extension_from_bytes(decoded_bytes)
            if detected_ext:
                current_ext = attachment_filename.lower().split('.')[-1]
                if current_ext == 'txt' or attachment_filename == 'report.txt':
                    base_name, _ = os.path.splitext(attachment_filename)
                    attachment_filename = f"{base_name}.{detected_ext}"
            content_str = clean_content
        except Exception as e:
            logger.error(f"[Email Attachment] Error detecting attachment type: {e}")

    return {"content": content_str, "filename": attachment_filename}


def _normalize_attachments(
    attachment_content: Any = None,
    attachment_filename: str = "report.txt",
    attachments: Optional[list] = None,
    owner: Optional[str] = None,
    resolve_ids: bool = True,
) -> list:
    normalized = []
    if attachments:
        for idx, att in enumerate(attachments):
            if not isinstance(att, dict):
                continue
            filename = att.get("filename") or att.get("attachment_filename") or att.get("name") or f"attachment_{idx + 1}.png"
            content_type = att.get("content_type") or att.get("type")
            if att.get("id"):
                if resolve_ids:
                    try:
                        resolved = resolve_attachment_reference(att, owner or user_context.get(None))
                    except AttachmentStoreError as e:
                        logger.error(f"[Email Attachment] Failed to resolve attachment id {att.get('id')}: {e}")
                        raise
                    prepared = _prepare_attachment(
                        resolved.get("content"),
                        resolved.get("filename") or filename,
                        fallback=f"attachment_{idx + 1}",
                    )
                    if prepared:
                        prepared.update({
                            "id": resolved.get("id"),
                            "name": resolved.get("name") or resolved.get("filename") or filename,
                            "content_type": resolved.get("content_type") or resolved.get("type") or content_type,
                            "type": resolved.get("type") or resolved.get("content_type") or content_type,
                            "size": resolved.get("size"),
                            "sha256": resolved.get("sha256"),
                        })
                        normalized.append(prepared)
                else:
                    normalized.append({
                        "id": att.get("id"),
                        "filename": filename,
                        "name": att.get("name") or filename,
                        "content_type": content_type,
                        "type": content_type,
                        "size": att.get("size"),
                        "sha256": att.get("sha256"),
                    })
                continue
            content = att.get("content") or att.get("attachment_content") or att.get("data")
            prepared = _prepare_attachment(content, filename, fallback=f"attachment_{idx + 1}")
            if prepared:
                if content_type:
                    prepared["content_type"] = content_type
                    prepared["type"] = content_type
                if att.get("size") is not None:
                    prepared["size"] = att.get("size")
                normalized.append(prepared)
    
    if attachment_content:
        already_exists = False
        for item in normalized:
            if item.get("content") == attachment_content:
                already_exists = True
                break
        if not already_exists:
            prepared = _prepare_attachment(attachment_content, attachment_filename, fallback="attachment")
            if prepared:
                normalized.insert(0, prepared)
                
    return normalized

def resolve_chat_image(reference: str, history: List[dict]) -> Optional[tuple]:
    """
    Scans history from last to first to find an image matching the reference description.
    Returns tuple (base64_data, filename) or None.
    """
    if not history or not reference:
        return None, "No history or reference provided"
        
    ref = reference.lower().strip()
    is_generic = (
        any(kw in ref for kw in ["last image", "previous image", "above image", "recent image", "last photo", "previous photo", "above photo", "recent photo", "last picture", "previous picture", "above picture", "recent picture", "last one", "previous one"]) or
        (any(kw in ref for kw in ["above", "last", "previous", "recent"]) and any(img_kw in ref for img_kw in ["image", "photo", "picture", "pic", "art", "draw", "attach", "attachment", "file"])) or
        (ref in ["above", "last", "previous", "recent", "last image", "last photo", "last picture"])
    )
    
    # Clean reference: replace underscores and dots with spaces, keep only alphanumeric and spaces
    ref_clean = ref.replace('_', ' ').replace('.', ' ')
    ref_clean = re.sub(r'[^\w\s]', '', ref_clean)
    ref_words = [w for w in ref_clean.split() if w not in STOP_WORDS]
    if not ref_words:
        ref_words = [w for w in ref_clean.split() if w]
        
    for msg in reversed(history):
        content = msg.get("content", msg.get("c", ""))
        
        # Case 1: User uploaded image
        user_img = msg.get("img") or msg.get("i")
        if user_img:
            if is_generic:
                return user_img, f"upload_{int(time.time())}.png"
            if ref_words:
                content_clean = re.sub(r'[^\w\s]', ' ', content.lower())
                content_words = set(content_clean.split())
                match_count = sum(1 for w in ref_words if w in content_words)
                if match_count == len(ref_words) or match_count >= 2:
                    return user_img, f"attached_{'_'.join(ref_words[:3])}.png"
                
        # Case 2: Generated image in assistant message
        img_matches = re.findall(r'!\[([^\]]*)\]\(([^)]+)\)', content)
        for alt, url in reversed(img_matches):
            alt_clean = re.sub(r'[^\w\s]', ' ', alt.lower())
            alt_words = set(alt_clean.split())
            
            match_count = sum(1 for w in ref_words if w in alt_words)
            if is_generic or (ref_words and (match_count == len(ref_words) or match_count >= 2)):
                try:
                    if url.startswith("/static/"):
                        local_path = url.lstrip("/")
                        if os.path.exists(local_path):
                            with open(local_path, "rb") as f:
                                return base64.b64encode(f.read()).decode("utf-8"), os.path.basename(local_path)
                    
                    if url.startswith("http"):
                        img_b64, detected_ext, error = _download_image_attachment(url)
                        if img_b64:
                            return img_b64, f"generated_{int(time.time())}.{detected_ext or 'png'}"
                        logger.error(f"Failed to retrieve generated image {url}: {error}")
                except Exception as e:
                    logger.error(f"Failed to retrieve generated image {url}: {e}")
                    
    return None, "No matching image found in chat history"


def resolve_chat_images(reference: str, history: List[dict], max_images: int = 2) -> List[tuple]:
    """Resolve one or more recent chat images as (base64_data, filename) tuples."""
    if not history or not reference or max_images <= 0:
        return []

    ref = reference.lower().strip()
    wants_recent = any(kw in ref for kw in ["above", "last", "previous", "recent", "these", "those", "this"])
    results = []
    seen = set()

    def add_result(data, filename):
        if not data:
            return
        key = str(data)[:128]
        if key in seen:
            return
        seen.add(key)
        results.append((data, filename or f"attachment_{len(results) + 1}.png"))

    for msg in reversed(history):
        if len(results) >= max_images:
            break
        content = msg.get("content", msg.get("c", ""))

        user_img = msg.get("img") or msg.get("i")
        if user_img and wants_recent:
            imgs = user_img if isinstance(user_img, list) else [user_img]
            for item in imgs:
                add_result(item, f"upload_{len(results) + 1}_{int(time.time())}.png")
                if len(results) >= max_images:
                    break

        img_matches = re.findall(r'!\[([^\]]*)\]\(([^)]+)\)', content)
        for alt, url in reversed(img_matches):
            if len(results) >= max_images:
                break
            try:
                if url.startswith("/static/"):
                    local_path = url.lstrip("/")
                    if os.path.exists(local_path):
                        with open(local_path, "rb") as f:
                            add_result(base64.b64encode(f.read()).decode("utf-8"), os.path.basename(local_path))
                elif url.startswith("http"):
                    img_b64, detected_ext, error = _download_image_attachment(url)
                    if img_b64:
                        clean_alt = re.sub(r'[^\w\s]', '_', alt).strip().replace(' ', '_') or "generated"
                        add_result(img_b64, f"{clean_alt[:24]}_{len(results) + 1}.{detected_ext or 'png'}")
                    else:
                        logger.error(f"Failed to retrieve generated image {url}: {error}")
            except Exception as e:
                logger.error(f"Failed to retrieve generated image {url}: {e}")

    return results[:max_images]


# 1. Custom DuckDuckGo TEXT Search Tool (using modern ddgs library)
@tool("web_search_text")
def search_tool(query: str) -> str:
    """Useful for searching the web for TEXT-BASED information, news, and technical questions. 
    STRICT RULE: This tool returns ONLY text descriptions and snippets. It CANNOT provide images."""
    try:
        from ddgs import DDGS
        import time
        
        query = query.strip()
        logger.info(f"DEBUG: [Search] Query (ddgs v9): {query}")
        
        results = []
        ddgs = DDGS()
        
        # Strategy 1: News (for recent/political queries)
        if any(kw in query.lower() for kw in ["recent", "news", "latest", "events", "politics"]):
            try:
                results.extend(ddgs.news(query, max_results=5))
                logger.info(f"DEBUG: [Search] News returned {len(results)} results")
            except Exception as ne:
                logger.error(f"DEBUG: [Search] News failed: {ne}")
                time.sleep(1)
        
        # Strategy 2: General web search (supplement or fallback)
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

from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv
import json

def parse_markdown_table(rows) -> str:
    if not rows: return ""
    html_rows = []
    has_header = False
    if len(rows) > 1 and re.match(r'^\|[\s:-|]+$', rows[1]):
        has_header = True
    
    for idx, row in enumerate(rows):
        if has_header and idx == 1:
            continue
        cols = [c.strip() for c in row.split('|')[1:-1]]
        tag = 'th' if (has_header and idx == 0) else 'td'
        cell_style = 'padding: 12px 16px; border-bottom: 1px solid #e2e8f0; text-align: left;'
        if tag == 'th':
            cell_style += 'background-color: #f8fafc; font-weight: 600; border-top: 1px solid #e2e8f0; color: #1e293b;'
        else:
            cell_style += 'color: #334155;'
        html_cols = "".join([f'<{tag} style="{cell_style}">{col}</{tag}>' for col in cols])
        html_rows.append(f'<tr>{html_cols}</tr>')
        
    return f'<div style="overflow-x: auto; margin: 20px 0;"><table style="width: 100%; border-collapse: collapse; font-size: 14px; border: 1px solid #e2e8f0;">{"".join(html_rows)}</table></div>'

def render_markdown_to_html(text: str) -> str:
    # 1. Extract code blocks
    code_blocks = []
    def save_code_block(match):
        lang = match.group(1) or "code"
        code = match.group(2)
        placeholder = f"%%%CODEBLOCK{len(code_blocks)}%%%"
        html = f'<div style="background-color: #0f172a; color: #e2e8f0; font-family: Consolas, Monaco, monospace; padding: 16px; border-radius: 12px; margin: 20px 0; overflow-x: auto; font-size: 13px; line-height: 1.6; border: 1px solid #1e293b;"><div style="font-size: 11px; text-transform: uppercase; color: #94a3b8; margin-bottom: 8px; font-weight: 600; border-bottom: 1px solid #1e293b; padding-bottom: 6px;">{lang}</div><pre style="margin:0; white-space: pre-wrap;">{code.strip()}</pre></div>'
        code_blocks.append(html)
        return placeholder
    
    text = re.sub(r'```(\w+)?\n(.*?)\n```', save_code_block, text, flags=re.DOTALL)
    
    # 2. Extract tables
    tables = []
    def save_table(table_rows):
        placeholder = f"%%%TABLE{len(tables)}%%%"
        html = parse_markdown_table(table_rows)
        tables.append(html)
        return placeholder
        
    lines = text.split('\n')
    in_table = False
    table_rows = []
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('|') and stripped.endswith('|'):
            if not in_table:
                in_table = True
                table_rows = []
            table_rows.append(stripped)
        else:
            if in_table:
                new_lines.append(save_table(table_rows))
                in_table = False
            new_lines.append(line)
    if in_table:
        new_lines.append(save_table(table_rows))
    text = '\n'.join(new_lines)
    
    # 3. Blockquotes
    text = re.sub(r'^>\s+(.+)$', r'<blockquote style="border-left: 4px solid #6366f1; padding-left: 16px; color: #4b5563; margin: 16px 0; font-style: italic; background-color: #f8fafc; padding: 10px 16px; border-radius: 0 8px 8px 0;">\1</blockquote>', text, flags=re.MULTILINE)
    
    # 4. Lists (Stateful)
    processed_lines = []
    in_ul = False
    in_ol = False
    for line in text.split('\n'):
        stripped = line.strip()
        bullet_match = re.match(r'^[\-\*]\s+(.+)$', stripped)
        num_match = re.match(r'^\d+\.\s+(.+)$', stripped)
        
        if bullet_match:
            if in_ol:
                processed_lines.append('</ol>')
                in_ol = False
            if not in_ul:
                processed_lines.append('<ul style="margin: 16px 0; padding-left: 24px; color: #334155;">')
                in_ul = True
            processed_lines.append(f'<li style="margin-bottom: 8px; line-height: 1.6;">{bullet_match.group(1)}</li>')
        elif num_match:
            if in_ul:
                processed_lines.append('</ul>')
                in_ul = False
            if not in_ol:
                processed_lines.append('<ol style="margin: 16px 0; padding-left: 24px; color: #334155;">')
                in_ol = True
            processed_lines.append(f'<li style="margin-bottom: 8px; line-height: 1.6;">{num_match.group(1)}</li>')
        else:
            if in_ul:
                processed_lines.append('</ul>')
                in_ul = False
            if in_ol:
                processed_lines.append('</ol>')
                in_ol = False
            processed_lines.append(line)
    if in_ul: processed_lines.append('</ul>')
    if in_ol: processed_lines.append('</ol>')
    text = '\n'.join(processed_lines)
    
    # 5. Inline formatting (bold, italic, links, inline code, hr, images)
    # Parse images first so they don't conflict with link rendering
    text = re.sub(
        r'!\[([^\]]*)\]\(([^)]+)\)', 
        r'<div style="text-align: center; margin: 24px 0;"><img src="\2" alt="\1" style="max-width: 100%; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 4px 12px rgba(0,0,0,0.05);"><br><em style="font-size: 12px; color: #64748b; display: block; margin-top: 8px;">\1</em></div>', 
        text
    )
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" style="color: #6366f1; text-decoration: none; font-weight: 500;">\1</a>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong style="font-weight: 600; color: #0f172a;">\1</strong>', text)
    text = re.sub(r'__([^_]+)__', r'<strong style="font-weight: 600; color: #0f172a;">\1</strong>', text)
    text = re.sub(r'\*([^*]+)\*', r'<em style="font-style: italic; color: #475569;">\1</em>', text)
    text = re.sub(r'_([^_]+)_', r'<em style="font-style: italic; color: #475569;">\1</em>', text)
    text = re.sub(r'`([^`\n]+)`', r'<code style="background-color: #f1f5f9; color: #6366f1; font-family: Consolas, Monaco, monospace; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; border: 1px solid #e2e8f0; font-weight: 500;">\1</code>', text)
    text = re.sub(r'^\s*[\-\*_]{3,}\s*$', r'<hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 24px 0;">', text, flags=re.MULTILINE)
    
    # 6. Build paragraph tags (excl. placeholders and structural HTML elements)
    paragraphs = []
    for part in text.split('\n\n'):
        part_stripped = part.strip()
        if not part_stripped: continue
        
        if any(marker in part_stripped for marker in ["%%%CODEBLOCK", "%%%TABLE", "<ul", "<ol", "<blockquote", "<hr"]):
            paragraphs.append(part_stripped)
        else:
            part_formatted = part_stripped.replace('\n', '<br>')
            paragraphs.append(f'<p style="margin: 0 0 16px 0; line-height: 1.8; color: #334155;">{part_formatted}</p>')
            
    text = '\n'.join(paragraphs)
    
    # 7. Restore placeholders
    for idx, html in enumerate(code_blocks):
        text = text.replace(f"%%%CODEBLOCK{idx}%%%", html)
    for idx, html in enumerate(tables):
        text = text.replace(f"%%%TABLE{idx}%%%", html)
        
    return text

def _build_html_body(personalized_body: str, tone: str) -> str:
    html_content = render_markdown_to_html(personalized_body)
    
    tone_config = {
        "formal": {
            "font": "'Playfair Display', Georgia, serif",
            "body_bg": "#f8fafc",
            "card_bg": "#ffffff",
            "header_bg": "#0f172a",
            "header_txt": "#ffffff",
            "title": "EXECUTIVE CORRESPONDENCE",
            "subtitle": "Official Communication Layer",
            "border": "1px solid #cbd5e1",
            "footer_bg": "#f1f5f9",
            "footer_txt": "#64748b",
            "shadow": "0 4px 20px rgba(0, 0, 0, 0.02)"
        },
        "informal": {
            "font": "'Quicksand', 'Inter', sans-serif",
            "body_bg": "#fffbeb",
            "card_bg": "#ffffff",
            "header_bg": "linear-gradient(135deg, #f59e0b 0%, #d97706 100%)",
            "header_txt": "#ffffff",
            "title": "Hello There!",
            "subtitle": "Warm message from your assistant",
            "border": "1px solid #fef3c7",
            "footer_bg": "#fffbeb",
            "footer_txt": "#b45309",
            "shadow": "0 8px 24px rgba(217, 119, 6, 0.04)"
        },
        "modern": {
            "font": "'Outfit', 'Inter', sans-serif",
            "body_bg": "#f3f4f6",
            "card_bg": "#ffffff",
            "header_bg": "linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)",
            "header_txt": "#ffffff",
            "title": "The All Time Helper",
            "subtitle": "Your AI Executive Assistant",
            "border": "1px solid #e5e7eb",
            "footer_bg": "#f9fafb",
            "footer_txt": "#9ca3af",
            "shadow": "0 10px 25px -5px rgba(0,0,0,0.05)"
        }
    }
    
    t = tone_config.get(tone, tone_config["modern"])
    
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Outfit:wght@400;500;600&family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=Quicksand:wght@400;500;600&display=swap');
    </style>
</head>
<body style="font-family: {t['font']}; margin: 0; padding: 40px; background-color: {t['body_bg']}; -webkit-font-smoothing: antialiased;">
    <div style="max-width: 650px; margin: auto; border: {t['border']}; border-radius: 16px; overflow: hidden; background: {t['card_bg']}; box-shadow: {t['shadow']};">
        <div style="background: {t['header_bg']}; padding: 35px 30px; text-align: center; color: {t['header_txt']};">
            <h1 style="margin: 0; font-size: 24px; font-weight: 600; letter-spacing: 1.5px; text-transform: uppercase;">{t['title']}</h1>
            <p style="margin: 8px 0 0; opacity: 0.85; font-size: 14px; font-weight: 400;">{t['subtitle']}</p>
        </div>
        <div style="padding: 40px 30px; color: #374151; font-size: 16px; line-height: 1.8;">
            {html_content}
        </div>
        <div style="padding: 24px 30px; text-align: center; font-size: 12px; color: {t['footer_txt']}; background: {t['footer_bg']}; border-top: 1px solid {t['border'].split()[-1] if 'solid' in t['border'] else '#e5e7eb'};">
            Dispatched via All Time Helper Secure Swarm &bull; Live Preview Verified
        </div>
    </div>
</body>
</html>"""

# 2. Email Tool (Bypasses Auth for Drafting, Generates EMAIL_DRAFT_PAYLOAD)
@tool("send_email_tool")
def send_email_tool(recipient: str, subject: str, body: str, attachment_content: Optional[str] = None, tone: Optional[str] = "modern", **kwargs) -> str:
    """Useful for drafting emails. Bypasses immediate SMTP auth check to create edit previews.
    recipient: Destination email(s). Can be a single email or a comma-separated list.
    subject: Professional subject line.
    body: The main content written by the agent.
    attachment_content: Optional attachment. Can be a base64 string, local file path, URL to download, or a description of a past image in the chat (e.g. 'black car', 'above', 'last image') to attach.
    tone: 'formal', 'informal', or 'modern'."""
    
    # Internal variables to match previous implementation and maintain backward compatibility
    attachment_filename = kwargs.get("attachment_filename", "report.txt")
    attachments = kwargs.get("attachments") or []
    owner = kwargs.get("owner") or user_context.get(None)
    chat_image_reference = kwargs.get("chat_image_reference", "")
    raw_attachment_text = kwargs.get("raw_attachment_text", "")
    
    # 0. Resolve past image attachment from chat history if requested
    # Or if attachment_content is a description/URL, auto-resolve/download it
    if attachment_content:
        content_str = str(attachment_content).strip()
        # Parse markdown image tag if present
        markdown_match = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)$', content_str)
        if markdown_match:
            alt_text = markdown_match.group(1)
            url = markdown_match.group(2)
            logger.info(f"[Email Tool] Detected markdown image tag in attachment_content. URL: {url}, alt: {alt_text}")
            content_str = url
            if (not attachment_filename or attachment_filename == "report.txt") and alt_text:
                clean_alt = re.sub(r'[^\w\s]', '_', alt_text).strip().replace(' ', '_')
                attachment_filename = f"{clean_alt[:30]}.png"

        # Check if URL
        if content_str.startswith("http://") or content_str.startswith("https://"):
            logger.info(f"[Email Tool] Fetching URL in attachment_content: {content_str}")
            img_b64, detected_ext, error = _download_image_attachment(content_str)
            if not img_b64:
                logger.error(f"[Email Tool] Error fetching URL {content_str}: {error}")
                from app.logic.exceptions import AgentFastExit
                raise AgentFastExit(f"ERROR: Failed to download image attachment bytes from URL: {error}")
            attachment_content = img_b64
            attachment_filename = _filename_with_detected_extension(
                attachment_filename,
                detected_ext,
                fallback=f"image_{int(time.time())}",
            )
        elif content_str.startswith("/static/") or content_str.startswith("static/"):
            local_path = content_str.lstrip("/")
            if os.path.exists(local_path):
                try:
                    with open(local_path, "rb") as f:
                        attachment_content = base64.b64encode(f.read()).decode("utf-8")
                    if not attachment_filename or attachment_filename == "report.txt":
                        attachment_filename = os.path.basename(local_path)
                except Exception as e:
                    logger.error(f"[Email Tool] Failed to read local file {local_path}: {e}")
        elif not is_base64(content_str):
            chat_image_reference = content_str
            attachment_content = None # Reset to resolve from history

    if chat_image_reference and not attachment_content:
        history = active_history_context.get()
        if history:
            resolved = resolve_chat_image(chat_image_reference, history)
            if isinstance(resolved, tuple) and resolved[0] is not None:
                img_data, resolved_filename = resolved
                attachment_content = img_data
                if not attachment_filename or attachment_filename == "report.txt":
                    attachment_filename = resolved_filename
                logger.info(f"[Email Tool] Successfully resolved chat image '{chat_image_reference}' and set attachment '{attachment_filename}'")
            else:
                failure_reason = resolved[1] if isinstance(resolved, tuple) else "Unknown"
                logger.warning(f"[Email Tool] Failed to resolve chat image matching reference: '{chat_image_reference}' — {failure_reason}")

    # 1. Combine main body with verbatim context
    full_message_body = body.strip()
    if raw_attachment_text.strip() and raw_attachment_text.strip() not in body.strip():
        # Clean up and format clumpy text dynamically
        cleaned_text = raw_attachment_text.strip()
        # Put bullet points on newlines
        cleaned_text = re.sub(r'\s*•\s*', '\n* ', cleaned_text)
        # Separate headings with newlines and markdown tags
        headings = ["Core Architectural Layers", "Key Architectural Characteristics", "References"]
        for heading in headings:
            cleaned_text = re.sub(rf'(?i)(?:\b|(?<=\s)){re.escape(heading)}(?:\b|(?=\s))', f'\n\n### {heading}\n', cleaned_text)
        # Separate reference URLs
        cleaned_text = re.sub(r'(\[\d+\]\s+https?://[^\s]+)', r'\n\1', cleaned_text)
        cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text).strip()
        
        full_message_body += f"\n\n---\n\n{cleaned_text}"
        
    # Run universal type detection to correct filename extension (e.g. if the image was saved as a .txt file)
    if attachment_content and is_base64(str(attachment_content)):
        try:
            clean_content = "".join(str(attachment_content).split())
            decoded_bytes = base64.b64decode(clean_content, validate=True)
            detected_ext = detect_extension_from_bytes(decoded_bytes)
            if detected_ext:
                current_ext = attachment_filename.lower().split('.')[-1]
                if current_ext == 'txt' or attachment_filename == 'report.txt':
                    base_name, _ = os.path.splitext(attachment_filename)
                    attachment_filename = f"{base_name}.{detected_ext}"
                    logger.info(f"[Email Tool] Corrected filename extension to '{detected_ext}' for detected binary content. New filename: '{attachment_filename}'")
        except Exception as e:
            logger.error(f"[Email Tool] Error running type detection on attachment: {e}")

    normalized_attachments = _normalize_attachments(
        attachment_content,
        attachment_filename,
        attachments,
        owner=owner,
        resolve_ids=False,
    )
    if normalized_attachments:
        attachment_content = normalized_attachments[0].get("content")
        attachment_filename = normalized_attachments[0].get("filename") or attachment_filename

    draft = {
        "recipient": recipient,
        "subject": subject,
        "body": full_message_body,
        "tone": tone,
        "attachment_content": attachment_content,
        "attachment_filename": attachment_filename
    }
    if normalized_attachments:
        draft["attachments"] = normalized_attachments
    
    # Serialized draft payload
    from app.logic.exceptions import AgentFastExit
    payload = f"EMAIL_DRAFT_PAYLOAD:{json.dumps(draft)}"
    raise AgentFastExit(payload)

def send_or_simulate_email(recipient: str, subject: str, body: str, tone: str = "modern", attachment_content: str = None, attachment_filename: str = "report.txt", attachments: Optional[list] = None, owner: Optional[str] = None) -> str:
    """Direct dispatch function. Integrates live SMTP or writes to simulated_emails.log."""
    load_dotenv()
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    sender_email = os.getenv("SENDER_EMAIL")
    sender_pwd = os.getenv("SENDER_PWD")
    email_mode = os.getenv("EMAIL_MODE", "SIMULATE").upper()

    is_live = (email_mode == "LIVE") and all([sender_email, sender_pwd])
    send_body, body_attachment = _prepare_email_send_body(body)

    # Parse recipients
    recipients = [r.strip() for r in recipient.split(',') if '@' in r]
    if not recipients:
        return f"ERROR: No valid recipients found in '{recipient}'"

    normalized_attachments = _normalize_attachments(
        attachment_content,
        attachment_filename,
        attachments,
        owner=owner or user_context.get(None),
        resolve_ids=True,
    )
    if body_attachment:
        normalized_attachments = [body_attachment] + normalized_attachments
    if normalized_attachments:
        attachment_content = normalized_attachments[0].get("content")
        attachment_filename = normalized_attachments[0].get("filename") or attachment_filename

    results = []
    
    try:
        if is_live:
            # Native Batch SMTP
            with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
                server.starttls()
                server.login(sender_email, sender_pwd)
                
                for target in recipients:
                    # Personalize salutation per recipient
                    raw_name = re.split(r'[\._\d]', target.split('@')[0])[0]
                    first_name = raw_name.capitalize()
                    
                    # Replace any group greetings with personalized one
                    personalized_body = re.sub(
                        r'^(hi everyone|dear all|hello everyone|greetings all|hi all|hello all|dear team)[,.]?',
                        f'Dear {first_name},',
                        send_body,
                        flags=re.IGNORECASE
                    )
                    if not personalized_body.startswith(f"Dear {first_name}"):
                        personalized_body = f"Dear {first_name},\n\n" + personalized_body
                    
                    msg = MIMEMultipart('mixed')
                    msg['Subject'] = subject
                    msg['From'] = f"The All Time Helper <{sender_email}>"
                    msg['To'] = target
                    
                    alternative = MIMEMultipart('alternative')
                    alternative.attach(MIMEText(personalized_body, 'plain', 'utf-8'))
                    html_content = _build_html_body(personalized_body, tone)
                    alternative.attach(MIMEText(html_content, 'html', 'utf-8'))
                    msg.attach(alternative)

                    for attachment in normalized_attachments:
                        current_content = attachment.get("content")
                        current_filename = attachment.get("filename") or attachment_filename
                        if current_content is None or len(str(current_content).strip()) == 0:
                            continue
                        # Try to decode from base64 if it's base64 encoded
                        att_bytes = None
                        is_b64 = False
                        try:
                            clean_content = "".join(str(current_content).split())
                            if len(clean_content) >= 64 and re.match(r'^[A-Za-z0-9+/]*={0,2}$', clean_content):
                                decoded_bytes = base64.b64decode(clean_content, validate=True)
                                att_bytes = decoded_bytes
                                is_b64 = True
                                logger.info(f"[Email Send] Decoded attachment_content from base64 ({len(att_bytes)} bytes)")
                        except Exception:
                            pass
                        
                        if not is_b64:
                            att_bytes = str(current_content).encode('utf-8')
                            logger.info(f"[Email Send] Attachment content is plain text ({len(att_bytes)} bytes)")

                        if is_b64:
                            # Extension already corrected in send_email_tool before draft payload
                            pass

                        content_type = attachment.get("content_type") or attachment.get("type")
                        maintype, subtype = 'application', 'octet-stream'
                        if content_type and "/" in str(content_type):
                            maintype, subtype = str(content_type).split("/", 1)
                        elif current_filename:
                            ext = current_filename.lower().split('.')[-1]
                            if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
                                maintype = 'image'
                                subtype = 'jpeg' if ext == 'jpg' else ext
                            elif ext == 'pdf':
                                maintype = 'application'
                                subtype = 'pdf'
                            elif ext == 'txt':
                                maintype = 'text'
                                subtype = 'plain'
                            elif ext == 'md':
                                maintype = 'text'
                                subtype = 'markdown'

                        part = MIMEBase(maintype, subtype)
                        part.set_payload(att_bytes)
                        encoders.encode_base64(part)
                        part.add_header('Content-Disposition', 'attachment', filename=current_filename)
                        msg.attach(part)
                        
                    server.send_message(msg)
                    results.append(target)
                    
            final_res = f"LIVE SUCCESS: Email broadcasted to {', '.join(results)}."
            return final_res
        else:
            # Simulation mode
            html_content = _build_html_body(send_body, tone)
            
            # Format visual log entry
            log_entry = f"""
========================================================================
[SIMULATED EMAIL DISPATCH]
Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}
From: The All Time Helper (Simulation Mode)
To: {recipient}
Subject: {subject}
Tone: {tone}
Attachment: {_attachment_label(normalized_attachments, attachment_filename)}
------------------------------------------------------------------------
HTML CONTENT PREVIEW:
{html_content}
========================================================================
"""
            log_file = "simulated_emails.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)
                
            return f"SIMULATE SUCCESS: Simulated email written to simulated_emails.log. Recipients: {recipient}"
    except Exception as e:
        logger.error(f"SMTP error: {e}", exc_info=True)
        return f"ERROR: Failed to send email. {str(e)}"

# 3. Astrology Tool
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

# 4. Palm Reading Tool
@tool("analyze_palm_lines")
def analyze_palm_lines(palm_description: str) -> str:
    """Analyzes a description of palm lines (Life, Heart, Head) to provide a fate reading."""
    return f"Based on your palm: '{palm_description}', I see a path of great resilience and emotional depth. Your lines suggest a major turning point approaching that will lead to spiritual growth."

# 5. Symbolic Image Tool (Pollinations API)
# BUG FIX: The tool now returns ONLY the raw markdown image tag.
# Previously it returned a conversational wrapper ("I have visualized...") which
# caused the LLM to re-summarise and strip the critical '!' character,
# converting the image into a broken hyperlink.
@tool("image_generate_tool")
def image_generate_tool(description: str) -> str:
    """AI IMAGE GENERATOR: Generates a high-definition AI image based on an artistic description.
    Use this for: Fictional things, concept art, fantasy, and creative requests."""
    import urllib.parse
    
    # 1. Clean and encode the prompt
    clean_desc = description.strip().replace('\n', ' ')
    encoded = urllib.parse.quote(clean_desc)
    
    # 2. Dynamic Seed and HD Parameters
    # We use a mix of prompt hash and timestamp to ensure high entropy for the seed.
    seed = (abs(hash(clean_desc)) + int(time.time())) % 1000000
    
    # TRIGGER: Start the background upscale task
    try:
        # We start the upscale task using the base url.
        # Then we append &uid=... to the URL so the frontend can extract the job ID
        # without the LLM stripping it off!
        base_url = f"https://image.pollinations.ai/prompt/{encoded}?model=flux&width=1024&height=1024&nologo=true&seed={seed}"
        upscale_id = UpscaleManager.start_upscale(base_url)
        
        image_url_with_uid = f"{base_url}&uid={upscale_id}"
        logger.info(f"[ART ENGINE] Generated HD URL with UID: {image_url_with_uid}")
        
        return f"![{clean_desc}]({image_url_with_uid})"
    except Exception as e:
        logger.error(f"[ART ENGINE] Failed to trigger upscale: {e}")
        base_url = f"https://image.pollinations.ai/prompt/{encoded}?model=flux&width=1024&height=1024&nologo=true&seed={seed}"
        return f"![{clean_desc}]({base_url})"


# 5.5 Real World Image Search Tool (using modern ddgs library)
@tool("image_search_tool")
def image_search_tool(query: str) -> str:
    """WEB IMAGE SEARCH: Useful for searching for REAL-WORLD images of products, people, places, vehicles, etc.
    Use this when the user wants to see a photo of something that actually exists in the real world."""
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


# 6. Neural Memory Tools
@tool("recall_memory")
def recall_memory(query: str) -> str:
    """Semantically searches the project's 'neural memory' for code snippets, architectural decisions, and previous activity.
    Use this to understand how a feature is implemented or what was decided in the past without reading all files."""
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
    """Permanently saves an architectural decision, user preference, or project milestone to the 'neural memory'.
    Use this to ensure critical context is preserved for future sessions."""
    try:
        log_insight(title, body)
        return f"Successfully archived insight: '{title}' to Neural Memory."
    except Exception as e:
        return f"Memory Archive Error: {str(e)}"
