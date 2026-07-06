from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import anyio
from fastapi import APIRouter
from fastapi.responses import Response

from app.logger import logger
from app.logic.safe_fetch import SafeFetchError, safe_fetch_url

router = APIRouter()


POLLINATIONS_HOSTS = {"image.pollinations.ai", "pollinations.ai"}


def _normalize_pollinations_query(parsed):
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered_key = key.lower()
        lowered_value = value.lower()
        if lowered_key == "model" and lowered_value == "turbo":
            value = "flux"
        if lowered_key == "nologo" and lowered_value == "true":
            continue
        query_items.append((key, value))
    return urlencode(query_items, doseq=True)


def normalize_pollinations_image_url(url: str) -> str:
    """Normalize supported Pollinations image URL variants before proxying."""
    parsed = urlparse(str(url or "").replace("&amp;", "&"))
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in POLLINATIONS_HOSTS:
        return str(url or "")

    query = _normalize_pollinations_query(parsed)

    if host == "pollinations.ai" and parsed.path.startswith("/p/"):
        prompt = parsed.path[len("/p/"):].strip("/") or "image"
        return urlunparse((
            "https",
            "image.pollinations.ai",
            "/prompt/" + quote(prompt, safe=""),
            "",
            query,
            "",
        ))

    if host == "image.pollinations.ai":
        return urlunparse(parsed._replace(query=query))

    return str(url or "")


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
    if (parsed.hostname or "").lower() == "image.pollinations.ai" and response.status_code in {401, 402, 403, 429}:
        return Response(
            content="Pollinations rejected or rate-limited this image request.",
            media_type="text/plain",
            status_code=response.status_code,
        )
    if response.status_code != 200:
        return Response(status_code=response.status_code)

    content_type = response.headers.get("content-type") or response.headers.get("Content-Type") or "image/png"
    if not content_type.lower().startswith("image/"):
        return Response(content="Image provider returned a non-image response.", media_type="text/plain", status_code=415)
    return Response(content=response.content, media_type=content_type)
