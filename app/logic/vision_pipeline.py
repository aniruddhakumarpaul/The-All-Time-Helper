import base64
import binascii
import hashlib
import io
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageStat

from app.logger import logger
from app.logic.attachment_store import ATTACHMENT_ROOT
from app.logic.safe_fetch import SafeFetchError, safe_fetch_url

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_VISION_IMAGE_BYTES = int(os.getenv("VISION_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
MAX_VISION_IMAGE_PIXELS = int(os.getenv("VISION_MAX_IMAGE_PIXELS", str(25_000_000)))
MAX_VISION_IMAGE_DIMENSION = int(os.getenv("VISION_MAX_IMAGE_DIMENSION", "1024"))
ALLOWED_LOCAL_IMAGE_ROOTS = (
    (REPO_ROOT / "static" / "uploads").resolve(),
    Path(ATTACHMENT_ROOT).resolve(),
)
_IMAGE_MIME_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "JPG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
_BASIC_COLOR_RGB = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "gray": (128, 128, 128),
    "red": (220, 35, 45),
    "orange": (240, 130, 30),
    "yellow": (240, 220, 45),
    "green": (35, 160, 70),
    "cyan": (35, 190, 200),
    "blue": (35, 85, 220),
    "purple": (130, 65, 180),
    "pink": (230, 120, 170),
    "brown": (125, 80, 45),
}


class VisionPipeline:
    """Validated, cached image perception with a lightweight local-first path."""

    def __init__(self):
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.model_name = os.getenv("VISION_MODEL", "moondream")
        self.fallback_model = os.getenv("VISION_FALLBACK_MODEL", "gemma4:e2b")
        self.timeout_seconds = max(10, int(os.getenv("VISION_TIMEOUT_SECONDS", "120")))
        self.cache_ttl_seconds = max(0, int(os.getenv("VISION_CACHE_TTL_SECONDS", "900")))
        self.cache_items = max(1, int(os.getenv("VISION_CACHE_ITEMS", "32")))
        self._cache = OrderedDict()
        self._cache_lock = threading.RLock()
        logger.info("[Vision] Perception engine initialized (primary=%s, fallback=%s)", self.model_name, self.fallback_model)

    @staticmethod
    def _read_local_image(source: str) -> bytes:
        if source.startswith("/static/"):
            candidate = REPO_ROOT / source.lstrip("/")
        elif source.startswith("static/"):
            candidate = REPO_ROOT / source
        else:
            candidate = Path(source)
        resolved = candidate.resolve()
        if not any(resolved.is_relative_to(root) for root in ALLOWED_LOCAL_IMAGE_ROOTS):
            raise ValueError("Local image path is outside an allowed upload directory.")
        if not resolved.is_file() or resolved.stat().st_size > MAX_VISION_IMAGE_BYTES:
            raise ValueError("Local image is unavailable or exceeds the size limit.")
        return resolved.read_bytes()

    @staticmethod
    def _decode_image_source(img_source) -> bytes:
        if isinstance(img_source, bytes):
            return img_source
        if not isinstance(img_source, str):
            raise ValueError("Unsupported image source.")
        source = img_source.strip()
        if source.lower().startswith(("http://", "https://")):
            response = safe_fetch_url(source, timeout=15, max_bytes=MAX_VISION_IMAGE_BYTES)
            if response.status_code != 200:
                raise SafeFetchError(f"Remote image returned HTTP {response.status_code}.", 502)
            return response.content
        if source.startswith(("/static/", "static/")) or (len(source) < 1024 and Path(source).is_absolute()):
            return VisionPipeline._read_local_image(source)
        payload = source.split(",", 1)[1] if source.lower().startswith("data:image/") and "," in source else source
        compact = "".join(payload.split())
        if not compact or len(compact) > MAX_VISION_IMAGE_BYTES * 2:
            raise ValueError("Encoded image is empty or exceeds the size limit.")
        try:
            return base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Encoded image is invalid.") from exc

    @staticmethod
    def _validate_and_resize(img_bytes: bytes) -> tuple[bytes, str]:
        if not img_bytes or len(img_bytes) > MAX_VISION_IMAGE_BYTES:
            raise ValueError("Image exceeds the size limit.")
        with Image.open(io.BytesIO(img_bytes)) as probe:
            width, height = probe.size
            image_format = str(probe.format or "").upper()
            if width <= 0 or height <= 0 or width * height > MAX_VISION_IMAGE_PIXELS:
                raise ValueError("Image dimensions exceed the safety limit.")
            probe.verify()

        media_type = _IMAGE_MIME_TYPES.get(image_format)
        if media_type and max(width, height) <= MAX_VISION_IMAGE_DIMENSION:
            return img_bytes, media_type

        with Image.open(io.BytesIO(img_bytes)) as image:
            image.thumbnail((MAX_VISION_IMAGE_DIMENSION, MAX_VISION_IMAGE_DIMENSION))
            buffered = io.BytesIO()
            image.convert("RGB").save(buffered, format="JPEG", quality=88, optimize=True)
            return buffered.getvalue(), "image/jpeg"

    def prepare_image(self, img_source):
        """Return a validated multimodal payload without exposing the source in logs."""
        try:
            raw, media_type = self._validate_and_resize(self._decode_image_source(img_source))
            encoded = base64.b64encode(raw).decode("ascii")
            return {
                "base64": encoded,
                "data_url": f"data:{media_type};base64,{encoded}",
                "media_type": media_type,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "byte_size": len(raw),
            }
        except Exception as exc:
            logger.warning("[Vision] Image preparation rejected (%s)", type(exc).__name__)
            return None

    def _encode_image(self, img_source):
        prepared = self.prepare_image(img_source)
        return prepared["base64"] if prepared else None

    @staticmethod
    def _source_kind(source) -> str:
        if isinstance(source, bytes):
            return "uploaded-bytes"
        value = str(source or "").strip()
        if value.lower().startswith(("http://", "https://")):
            return f"remote:{urlparse(value).hostname or 'unknown'}"
        if value.startswith(("/static/", "static/")) or (len(value) < 1024 and Path(value).is_absolute()):
            return "local-upload"
        return "inline-upload"

    def _cache_get(self, key: str):
        if self.cache_ttl_seconds <= 0:
            return None
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if not cached:
                return None
            if now - cached["stored_at"] > self.cache_ttl_seconds:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return dict(cached)

    def _cache_set(self, key: str, description: str, model: str):
        if self.cache_ttl_seconds <= 0:
            return
        with self._cache_lock:
            self._cache[key] = {
                "description": description,
                "model": model,
                "stored_at": time.monotonic(),
            }
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_items:
                self._cache.popitem(last=False)

    @staticmethod
    def _clean_model_description(content: str | None) -> str | None:
        cleaned = str(content or "").strip()
        if not cleaned:
            return None
        lowered = cleaned.lower()
        invalid_markers = (
            "image not generated", "not generated by lumina", "no image was provided",
            "cannot view the image", "unable to see the image",
        )
        if any(marker in lowered for marker in invalid_markers):
            return None
        cleaned = re.sub(r"^!+\s*image\s*!+\s*", "", cleaned, flags=re.IGNORECASE).strip()
        return cleaned or None

    @staticmethod
    def _deterministic_visual_answer(prepared: dict, prompt: str) -> str | None:
        normalized = " ".join(str(prompt or "").lower().split())
        asks_dominant_color = "dominant color" in normalized or "dominant colour" in normalized
        asks_image_color = bool(
            re.search(r"\bwhat (?:is|are)(?: the)? colou?r(?:s)? (?:in|of) (?:this|the) image\b", normalized)
        )
        asks_dimensions = any(
            phrase in normalized
            for phrase in ("image dimensions", "image resolution", "size in pixels", "how many pixels")
        )
        if not asks_dominant_color and not asks_image_color and not asks_dimensions:
            return None

        raw = base64.b64decode(prepared["base64"], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if asks_dimensions:
                return f"The image is {width} by {height} pixels."
            sample = image.convert("RGB")
            sample.thumbnail((64, 64))
            mean = tuple(round(value) for value in ImageStat.Stat(sample).mean[:3])
        color_name = min(
            _BASIC_COLOR_RGB,
            key=lambda name: sum((mean[index] - _BASIC_COLOR_RGB[name][index]) ** 2 for index in range(3)),
        )
        return f"The dominant color in the image is {color_name}."

    @staticmethod
    def _analysis_prompt(user_prompt: str) -> str:
        return " ".join(str(user_prompt or "Describe this image.").split())[:2000]

    def _request_model(self, model: str, prepared: dict, prompt: str) -> tuple[str | None, float]:
        started = time.perf_counter()
        if model.lower().startswith("moondream"):
            prompts = (
                f"Describe this image briefly. Focus on visible facts needed for this request: {prompt}",
                "Describe this image in one concise paragraph, including important objects, colors, and visible text.",
            )
            for attempt, focused_prompt in enumerate(prompts):
                payload = {
                    "model": model,
                    "prompt": focused_prompt,
                    "images": [prepared["base64"]],
                    "stream": False,
                }
                response = requests.post(
                    f"{self.ollama_url}/api/generate",
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code != 200:
                    logger.warning("[Vision] Model %s returned HTTP %s", model, response.status_code)
                    return None, time.perf_counter() - started
                content = self._clean_model_description(response.json().get("response"))
                if content:
                    return content, time.perf_counter() - started
                if attempt == 0:
                    logger.info("[Vision] Lightweight model returned empty output; retrying with a description prompt")
            return None, time.perf_counter() - started

        model_prompt = (
            "Use only visible evidence in the image to answer the user's request. Be concise but specific. "
            "Quote visible text exactly when relevant, and say when a detail is uncertain. "
            f"User request: {prompt}"
        )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": model_prompt, "images": [prepared["base64"]]}],
            "stream": False,
            "options": {"num_predict": 384, "temperature": 0.1},
        }
        response = requests.post(f"{self.ollama_url}/api/chat", json=payload, timeout=self.timeout_seconds)
        elapsed = time.perf_counter() - started
        if response.status_code != 200:
            logger.warning("[Vision] Model %s returned HTTP %s", model, response.status_code)
            return None, elapsed
        message = response.json().get("message", {})
        content = self._clean_model_description(message.get("content"))
        return content, elapsed

    def analyze_chat_images(self, urls, user_prompt, *, allow_fallback: bool = True):
        if not urls:
            return None

        selected_source = urls[0]
        prepared = self.prepare_image(selected_source)
        if not prepared:
            return None

        prompt = self._analysis_prompt(user_prompt)
        cache_key = hashlib.sha256(f"{prepared['sha256']}\n{prompt.lower()}".encode("utf-8")).hexdigest()
        cached = self._cache_get(cache_key)
        if cached:
            logger.info("[Vision] Cache hit model=%s bytes=%s", cached["model"], prepared["byte_size"])
            return {
                "url": selected_source,
                "description": cached["description"],
                "model": cached["model"],
                "elapsed_seconds": 0.0,
                "cached": True,
            }

        deterministic_answer = self._deterministic_visual_answer(prepared, prompt)
        if deterministic_answer:
            self._cache_set(cache_key, deterministic_answer, "deterministic-vision")
            logger.info("[Vision] Deterministic visual analysis complete bytes=%s", prepared["byte_size"])
            return {
                "url": selected_source,
                "description": deterministic_answer,
                "model": "deterministic-vision",
                "elapsed_seconds": 0.0,
                "cached": False,
            }

        models = [self.model_name]
        if allow_fallback and self.fallback_model and self.fallback_model not in models:
            models.append(self.fallback_model)
        logger.info(
            "[Vision] Analysis started source=%s bytes=%s primary=%s",
            self._source_kind(selected_source),
            prepared["byte_size"],
            self.model_name,
        )
        for model in models:
            try:
                description, elapsed = self._request_model(model, prepared, prompt)
            except Exception as exc:
                logger.warning("[Vision] Model %s failed (%s)", model, type(exc).__name__)
                continue
            if description:
                self._cache_set(cache_key, description, model)
                logger.info("[Vision] Analysis complete model=%s duration_ms=%d", model, round(elapsed * 1000))
                return {
                    "url": selected_source,
                    "description": description,
                    "model": model,
                    "elapsed_seconds": elapsed,
                    "cached": False,
                }
            logger.warning("[Vision] Model %s returned no usable content", model)
        return None


vision_sys = VisionPipeline()