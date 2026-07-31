import base64
import hashlib
import json
import os
import re
import time
import uuid
from typing import Any, Dict, Optional


class AttachmentStoreError(ValueError):
    pass


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ATTACHMENT_ROOT = os.getenv("ATTACHMENT_STORE_DIR", os.path.join(REPO_ROOT, ".runtime_attachments"))
MAX_ATTACHMENT_BYTES = int(os.getenv("ATTACHMENT_MAX_BYTES", str(10 * 1024 * 1024)))
ATTACHMENT_TTL_SECONDS = int(os.getenv("ATTACHMENT_TTL_SECONDS", str(24 * 60 * 60)))

ALLOWED_FILE_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "txt": "text/plain",
    "md": "text/markdown",
    "pdf": "application/pdf",
}
ALLOWED_IMAGE_TYPES = {key: value for key, value in ALLOWED_FILE_TYPES.items() if key in {"png", "jpg", "gif", "webp"}}
TEXT_EXTRACTION_MAX_CHARS = 30_000
ATTACHMENT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")

def detect_file_type(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"%PDF"):
        return "pdf"
    return None


def _owner_key(owner: str) -> str:
    if not owner:
        raise AttachmentStoreError("Attachment owner is required.")
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()


def _safe_filename(name: str, fallback: str = "attachment") -> str:
    base = os.path.basename(name or fallback)
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    return base[:120] or fallback


def _owner_dir(owner: str) -> str:
    path = os.path.join(ATTACHMENT_ROOT, _owner_key(owner))
    os.makedirs(path, exist_ok=True)
    return path


def _validate_attachment_id(attachment_id: str) -> str:
    value = str(attachment_id or "").strip().lower()
    if not ATTACHMENT_ID_PATTERN.fullmatch(value):
        raise AttachmentStoreError("Attachment id is invalid.")
    return value

def cleanup_expired_attachments(now: Optional[float] = None) -> None:
    now = now or time.time()
    if not os.path.isdir(ATTACHMENT_ROOT):
        return
    for owner_name in os.listdir(ATTACHMENT_ROOT):
        owner_path = os.path.join(ATTACHMENT_ROOT, owner_name)
        if not os.path.isdir(owner_path):
            continue
        for filename in os.listdir(owner_path):
            path = os.path.join(owner_path, filename)
            try:
                if now - os.path.getmtime(path) > ATTACHMENT_TTL_SECONDS:
                    os.remove(path)
            except OSError:
                pass


def save_attachment_bytes(name: str, content_type: str, data: bytes, owner: str) -> Dict[str, Any]:
    cleanup_expired_attachments()
    if not data:
        raise AttachmentStoreError("Attachment is empty.")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentStoreError(f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes.")

    detected_ext = detect_file_type(data)
    if detected_ext not in ALLOWED_FILE_TYPES:
        if (name or "").lower().endswith((".txt", ".md")):
            detected_ext = "md" if (name or "").lower().endswith(".md") else "txt"
        else:
            raise AttachmentStoreError("Supported attachments are PNG, JPEG, GIF, WEBP, TXT, Markdown, and PDF.")

    verified_type = ALLOWED_FILE_TYPES[detected_ext]
    supplied_type = (content_type or "").split(";")[0].strip().lower()
    if supplied_type and supplied_type != verified_type and not (detected_ext in {"txt", "md"} and supplied_type == "application/octet-stream"):
        raise AttachmentStoreError("Attachment content type does not match the file.")

    attachment_id = uuid.uuid4().hex
    safe_name = _safe_filename(name, f"attachment.{detected_ext}")
    if "." not in safe_name:
        safe_name = f"{safe_name}.{detected_ext}"

    owner_dir = _owner_dir(owner)
    data_path = os.path.join(owner_dir, f"{attachment_id}.{detected_ext}")
    meta_path = os.path.join(owner_dir, f"{attachment_id}.json")
    with open(data_path, "wb") as f:
        f.write(data)

    sha256 = hashlib.sha256(data).hexdigest()
    metadata = {
        "id": attachment_id,
        "name": safe_name,
        "filename": safe_name,
        "type": verified_type,
        "content_type": verified_type,
        "size": len(data),
        "sha256": sha256,
        "created_at": time.time(),
        "path": data_path,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f)
    return {k: metadata[k] for k in ("id", "name", "type", "size", "sha256")}


def resolve_attachment_metadata(attachment_id: str, owner: str) -> Dict[str, Any]:
    attachment_id = _validate_attachment_id(attachment_id)
    meta_path = os.path.join(_owner_dir(owner), f"{attachment_id}.json")
    if not os.path.exists(meta_path):
        raise AttachmentStoreError("Attachment not found or expired.")
    with open(meta_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    data_path = metadata.get("path", "")
    owner_dir = os.path.abspath(_owner_dir(owner))
    resolved_path = os.path.abspath(data_path)
    if not resolved_path.startswith(owner_dir + os.sep) or not os.path.exists(resolved_path):
        raise AttachmentStoreError("Attachment file is unavailable.")
    metadata["path"] = resolved_path
    return metadata


def extract_attachment_text(ref: Dict[str, Any]) -> str:
    """Extract bounded plain text from a resolved text or PDF attachment."""
    content_type = str(ref.get("content_type") or ref.get("type") or "").lower()
    if content_type not in {"text/plain", "text/markdown", "application/pdf"}:
        return ""
    try:
        raw = base64.b64decode(str(ref.get("content") or ""), validate=True)
        if content_type == "application/pdf":
            try:
                import fitz
                import io
                document = fitz.open(stream=raw, filetype="pdf")
                text = "\n".join(page.get_text() for page in document)
            except ImportError:
                return "[PDF attached; PDF text extraction is unavailable in this environment.]"
        else:
            text = raw.decode("utf-8-sig", errors="replace")
    except Exception:
        return "[Attachment text could not be extracted.]"
    return text[:TEXT_EXTRACTION_MAX_CHARS].strip()

def resolve_attachment_reference(ref: Dict[str, Any], owner: str) -> Dict[str, Any]:
    attachment_id = ref.get("id")
    if not attachment_id:
        return dict(ref)

    metadata = resolve_attachment_metadata(attachment_id, owner)
    with open(metadata["path"], "rb") as f:
        data = f.read()
    return {
        "id": attachment_id,
        "content": base64.b64encode(data).decode("utf-8"),
        "filename": metadata.get("filename") or ref.get("filename") or ref.get("name"),
        "name": metadata.get("name") or ref.get("name"),
        "content_type": metadata.get("content_type") or ref.get("content_type") or ref.get("type"),
        "type": metadata.get("type") or ref.get("type"),
        "size": metadata.get("size"),
        "sha256": metadata.get("sha256"),
    }
