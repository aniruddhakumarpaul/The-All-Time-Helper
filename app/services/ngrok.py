import os
import sys
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.logger import logger


@dataclass
class NgrokSession:
    public_url: str | None = None
    started_tunnel: bool = False
    listener: any = None


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_token(value: str | None) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _looks_fake_token(value: str | None) -> bool:
    token = _clean_token(value).lower()
    return not token or token.startswith("your-") or "placeholder" in token or "optional-" in token


def start_ngrok_if_enabled(port: int = 9000) -> NgrokSession:
    """Start Ngrok using the official python-ngrok embedded library (bypasses AV block)."""
    if not _enabled(os.getenv("ENABLE_NGROK")):
        return NgrokSession()

    token = _clean_token(os.getenv("NGROK_TOKEN"))
    if _looks_fake_token(token):
        logger.warning("ENABLE_NGROK is set, but NGROK_TOKEN is missing or still a placeholder.")
        return NgrokSession()

    try:
        import ngrok
    except ImportError:
        logger.info("Official 'ngrok' package not found. Installing it to bypass Windows Defender...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "ngrok"])
            import ngrok
        except Exception as exc:
            logger.error(f"Failed to auto-install official ngrok package: {exc}")
            return NgrokSession()

    try:
        logger.info("Starting embedded Ngrok tunnel...")
        # Start the tunnel with the auth token
        listener = ngrok.forward(port, authtoken=token)
        public_url = listener.url()
        logger.info(f"Started Ngrok tunnel: {public_url}")
        return NgrokSession(public_url=public_url, started_tunnel=True, listener=listener)
    except Exception as exc:
        logger.warning(f"Ngrok startup failed; continuing without tunnel: {exc}")
        return NgrokSession()


def stop_ngrok(session: NgrokSession) -> None:
    """Stop only a tunnel created by this process."""
    if not session.started_tunnel or not getattr(session, "listener", None):
        return
    try:
        import ngrok
        ngrok.disconnect(session.listener.url())
    except Exception as exc:
        logger.warning(f"Ngrok shutdown failed: {exc}")
