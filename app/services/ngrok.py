import os
from dataclasses import dataclass
from pathlib import Path

from app.logger import logger


@dataclass
class NgrokSession:
    public_url: str | None = None
    started_tunnel: bool = False


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_token(value: str | None) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _looks_fake_token(value: str | None) -> bool:
    token = _clean_token(value).lower()
    return not token or token.startswith("your-") or "placeholder" in token or "optional-" in token


def _pyngrok_config():
    ngrok_path = str(os.getenv("NGROK_PATH") or "").strip().strip('"').strip("'")
    if not ngrok_path:
        return None
    candidate = Path(ngrok_path)
    if not candidate.is_file():
        logger.warning(f"NGROK_PATH is set but file was not found: {candidate}")
        return None
    try:
        from pyngrok.conf import PyngrokConfig

        return PyngrokConfig(ngrok_path=str(candidate))
    except Exception as exc:
        logger.warning(f"Could not configure NGROK_PATH: {exc}")
        return None


def start_ngrok_if_enabled(port: int = 9000) -> NgrokSession:
    """Start or reuse Ngrok only when explicitly enabled for local development."""
    if not _enabled(os.getenv("ENABLE_NGROK")):
        return NgrokSession()

    token = _clean_token(os.getenv("NGROK_TOKEN"))
    if _looks_fake_token(token):
        logger.warning("ENABLE_NGROK is set, but NGROK_TOKEN is missing or still a placeholder.")
        return NgrokSession()

    try:
        from pyngrok import ngrok

        config = _pyngrok_config()
        if config:
            ngrok.set_auth_token(token, pyngrok_config=config)
            tunnels = ngrok.get_tunnels(pyngrok_config=config)
        else:
            ngrok.set_auth_token(token)
            tunnels = ngrok.get_tunnels()
        if tunnels:
            public_url = str(tunnels[0].public_url).rstrip("/")
            logger.info(f"Using existing Ngrok tunnel: {public_url}")
            return NgrokSession(public_url=public_url)

        tunnel = ngrok.connect(port, pyngrok_config=config) if config else ngrok.connect(port)
        public_url = str(tunnel.public_url).rstrip("/")
        logger.info(f"Started Ngrok tunnel: {public_url}")
        return NgrokSession(public_url=public_url, started_tunnel=True)
    except PermissionError as exc:
        logger.warning(f"Ngrok disabled for this run because Windows denied access: {exc}")
        return NgrokSession()
    except OSError as exc:
        if getattr(exc, "winerror", None) == 5:
            logger.warning(f"Ngrok disabled for this run because Windows denied access: {exc}")
        else:
            logger.warning(f"Ngrok startup failed; continuing without tunnel: {exc}")
        return NgrokSession()
    except Exception as exc:
        logger.warning(f"Ngrok startup failed; continuing without tunnel: {exc}")
        return NgrokSession()


def stop_ngrok(session: NgrokSession) -> None:
    """Stop only a tunnel created by this process."""
    if not session.started_tunnel or not session.public_url:
        return
    try:
        from pyngrok import ngrok

        ngrok.disconnect(session.public_url)
    except Exception as exc:
        logger.warning(f"Ngrok shutdown failed: {exc}")
