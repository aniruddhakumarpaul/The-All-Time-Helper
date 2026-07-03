import ipaddress
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Optional

import requests


class SafeFetchError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class SafeFetchResponse:
    content: bytes
    headers: dict
    status_code: int
    url: str


def _host_is_blocked(hostname: str) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return not ip.is_global
    except ValueError:
        return False


def _validate_safe_http_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise SafeFetchError("URL scheme must be http or https", 400)
    if not parsed.hostname:
        raise SafeFetchError("URL host is required", 400)
    if parsed.username or parsed.password:
        raise SafeFetchError("URL credentials are not allowed", 400)
    if _host_is_blocked(parsed.hostname):
        raise SafeFetchError("URL host is not allowed", 403)

    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SafeFetchError(f"URL host could not be resolved: {exc}", 400)

    for addr in addresses:
        ip_text = addr[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            raise SafeFetchError("URL host resolved to an invalid address", 403)
        if not ip.is_global:
            raise SafeFetchError("URL host resolved to a blocked address", 403)
    return parsed


def safe_fetch_url(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: int = 30,
    max_bytes: int = 8 * 1024 * 1024,
    max_redirects: int = 3,
    request_get: Optional[Callable] = None,
) -> SafeFetchResponse:
    request_get = request_get or requests.get
    current_url = str(url or "").strip()
    for _ in range(max_redirects + 1):
        _validate_safe_http_url(current_url)
        response = request_get(
            current_url,
            headers=headers,
            timeout=timeout,
            stream=True,
            allow_redirects=False,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        response_headers = dict(getattr(response, "headers", {}) or {})

        if status in {301, 302, 303, 307, 308}:
            location = response_headers.get("location") or response_headers.get("Location")
            if not location:
                raise SafeFetchError("Redirect response did not include a Location header", 502)
            current_url = urllib.parse.urljoin(current_url, str(location))
            continue

        content_length = response_headers.get("content-length") or response_headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise SafeFetchError("Remote response is too large", 413)
            except ValueError:
                pass

        body = bytearray()
        if hasattr(response, "iter_content"):
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise SafeFetchError("Remote response exceeded the size limit", 413)
        else:
            body.extend(getattr(response, "content", b"") or b"")
            if len(body) > max_bytes:
                raise SafeFetchError("Remote response exceeded the size limit", 413)

        close = getattr(response, "close", None)
        if callable(close):
            close()

        return SafeFetchResponse(
            content=bytes(body),
            headers=response_headers,
            status_code=status,
            url=current_url,
        )

    raise SafeFetchError("Too many redirects", 310)
