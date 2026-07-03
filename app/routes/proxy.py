from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import anyio
from fastapi import APIRouter
from fastapi.responses import Response

from app.logger import logger
from app.logic.safe_fetch import SafeFetchError, safe_fetch_url

router = APIRouter()


def normalize_pollinations_image_url(url: str) -> str:
    """Normalize supported Pollinations query options before proxying."""
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() != "image.pollinations.ai":
        return str(url or "")

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == "model" and value.lower() == "turbo":
            value = "flux"
        if key == "nologo" and value.lower() == "true":
            continue
        query_items.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(query_items, doseq=True)))


@router.get("/api/image_proxy")
async def image_proxy(url: str):
    """Proxy bounded image responses while blocking non-public targets and redirects."""
    normalized_url = normalize_pollinations_image_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    try:
        response = await anyio.to_thread.run_sync(
            lambda: safe_fetch_url(normalized_url, headers=headers, timeout=60, max_bytes=8 * 1024 * 1024)
        )
    except SafeFetchError as exc:
        return Response(status_code=exc.status_code)
    except Exception as exc:
        logger.debug(f"Proxy exception: {exc}")
        return Response(status_code=500)

    parsed = urlparse(normalized_url)
    if (parsed.hostname or "").lower() == "image.pollinations.ai" and response.status_code in {401, 402, 403}:
        return Response(
            content="Pollinations rejected this model or account/budget.",
            media_type="text/plain",
            status_code=response.status_code,
        )
    if response.status_code != 200:
        return Response(status_code=response.status_code)

    content_type = response.headers.get("content-type") or response.headers.get("Content-Type") or "image/png"
    if not content_type.lower().startswith("image/"):
        return Response(status_code=415)
    return Response(content=response.content, media_type=content_type)
