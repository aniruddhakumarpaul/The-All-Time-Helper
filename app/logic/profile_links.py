import re
from urllib.parse import quote


_USERNAME_RE = re.compile(r"(?<![\w.])@?([a-zA-Z0-9._]{1,30})(?![\w.])")
_PLATFORM_URLS = {
    "instagram": "https://www.instagram.com/{username}/",
    "insta": "https://www.instagram.com/{username}/",
    "ig": "https://www.instagram.com/{username}/",
    "github": "https://github.com/{username}",
    "x": "https://x.com/{username}",
    "twitter": "https://x.com/{username}",
    "tiktok": "https://www.tiktok.com/@{username}",
}
_PLATFORM_ALIASES = tuple(_PLATFORM_URLS.keys())


def _extract_platform(text: str) -> str | None:
    lowered = str(text or "").lower()
    for platform in _PLATFORM_ALIASES:
        if re.search(rf"\b{re.escape(platform)}\b", lowered):
            return platform
    return None


def _extract_username(text: str) -> str | None:
    raw = str(text or "")
    explicit = re.search(r"(?:username|handle|profile)\s*[:=]?\s*@?([a-zA-Z0-9._]{1,30})\b", raw, flags=re.I)
    if explicit:
        return explicit.group(1).strip("._")
    at_handle = re.search(r"@([a-zA-Z0-9._]{1,30})\b", raw)
    if at_handle:
        return at_handle.group(1).strip("._")
    tokens = [match.group(1) for match in _USERNAME_RE.finditer(raw)]
    stopwords = {
        "search", "web", "for", "profile", "link", "username", "handle", "instagram", "insta", "ig",
        "github", "twitter", "x", "tiktok", "find", "lookup", "url", "the", "a", "an", "of",
    }
    candidates = [token for token in tokens if token.lower() not in stopwords and any(ch in token for ch in "._0123456789")]
    return candidates[-1].strip("._") if candidates else None


def resolve_public_profile_link_request(prompt: str) -> str | None:
    """Return a deterministic public profile URL answer for username-link requests.

    This is intentionally not an identity lookup. It only formats the public URL
    pattern for a user-supplied handle on a named platform.
    """
    text = str(prompt or "").strip()
    lowered = text.lower()
    if not any(term in lowered for term in ("profile", "username", "handle", "link", "url")):
        return None
    platform = _extract_platform(text)
    if not platform:
        return None
    username = _extract_username(text)
    if not username:
        return None
    template = _PLATFORM_URLS[platform]
    safe_username = quote(username, safe="._")
    url = template.format(username=safe_username)
    canonical = "Instagram" if platform in {"instagram", "insta", "ig"} else "X/Twitter" if platform in {"x", "twitter"} else platform.title()
    return (
        f"{canonical} profile URL candidate for @{username}:\n\n"
        f"{url}\n\n"
        "This is the deterministic public profile URL pattern for the supplied username. "
        "If the account is private, renamed, suspended, or does not exist, the URL may still open to an unavailable profile page."
    )
