import os
from dataclasses import dataclass

from app.logger import logger


@dataclass
class NgrokSession:
    public_url: str | None = None
    started_tunnel: bool = False


def start_ngrok_if_enabled(port: int = 9000) -> NgrokSession:
    """Start or reuse Ngrok only when explicitly enabled for local development."""
    if os.getenv("ENABLE_NGROK", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return NgrokSession()

    token = os.getenv("NGROK_TOKEN")
    if not token:
        logger.warning("ENABLE_NGROK is set, but NGROK_TOKEN is missing.")
        return NgrokSession()

    try:
        from pyngrok import ngrok

        ngrok.set_auth_token(token)
        tunnels = ngrok.get_tunnels()
        if tunnels:
            public_url = str(tunnels[0].public_url).rstrip("/")
            logger.info(f"Using existing Ngrok tunnel: {public_url}")
            return NgrokSession(public_url=public_url)

        tunnel = ngrok.connect(port)
        public_url = str(tunnel.public_url).rstrip("/")
        logger.info(f"Started Ngrok tunnel: {public_url}")
        return NgrokSession(public_url=public_url, started_tunnel=True)
    except Exception as exc:
        logger.error(f"Ngrok startup failed: {exc}")
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
