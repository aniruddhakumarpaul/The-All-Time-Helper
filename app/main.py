import ipaddress
import os
import socket
import sys
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# --- BOOTSTRAP PATHS ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv()

from app.database import init_db
from app.logger import logger
from app.logic.upscaler import UpscaleManager
from app.routes import auth, chat

ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:9000").split(",") if origin.strip()]


def _is_blocked_ip(ip_text: str) -> bool:
    ip = ipaddress.ip_address(ip_text)
    return any([
        ip.is_private,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ])


def _host_resolves_to_blocked_address(hostname: str) -> bool:
    host = (hostname or "").strip().strip("[]").lower().rstrip(".")
    if not host:
        return True
    if host == "localhost" or host.endswith(".local"):
        return True

    try:
        return _is_blocked_ip(host)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return True

    if not infos:
        return True

    for info in infos:
        address = info[4][0]
        try:
            if _is_blocked_ip(address):
                return True
        except ValueError:
            return True
    return False


def _validate_proxy_url(url: str):
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Unsupported URL scheme")
    if _host_resolves_to_blocked_address(parsed.hostname):
        raise ValueError("Blocked proxy target")
    return parsed


def _normalize_pollinations_image_url(url: str) -> str:
    """Normalize Pollinations image URLs before proxying."""
    if not url:
        return url

    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return url
        if (parsed.hostname or "").lower() != "image.pollinations.ai":
            return url

        query_items = []
        has_model = False
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key == "model":
                has_model = True
                if value.lower() == "turbo":
                    value = "flux"
            if key == "nologo" and value.lower() == "true":
                continue
            query_items.append((key, value))

        if has_model and not any(key == "model" for key, _ in query_items):
            query_items.append(("model", "flux"))

        return urlunparse(parsed._replace(query=urlencode(query_items, doseq=True)))
    except Exception:
        return url


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.diagnostics import run_startup_diagnostics
        run_startup_diagnostics()

        from app.logic.memory import prune_stale_memories
        prune_stale_memories(days=30)
    except Exception as e:
        logger.error(f"[!] Diagnostics/Pruning Error: {e}")

    try:
        from pyngrok import ngrok
        ngrok_token = os.getenv("NGROK_TOKEN")
        if ngrok_token:
            ngrok.set_auth_token(ngrok_token)
            tunnels = ngrok.get_tunnels()
            public_url = None

            if not tunnels:
                import requests
                try:
                    res = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
                    if res.status_code == 200:
                        data = res.json().get("tunnels", [])
                        if data:
                            public_url = data[0].get("public_url")
                            logger.info(f"Detected external Ngrok tunnel: {public_url}")
                except Exception:
                    pass
            else:
                public_url = tunnels[0].public_url
                logger.info(f"THE ALL TIME HELPER - PRO is online via {public_url}")

            if not public_url:
                try:
                    tunnel = ngrok.connect(9000)
                    public_url = tunnel.public_url
                    logger.info(f"Started new Ngrok tunnel: {public_url}")
                except Exception as e:
                    logger.error(f"Ngrok connect failed: {e}")

            if public_url and public_url not in ALLOWED_ORIGINS:
                ALLOWED_ORIGINS.append(public_url)
                logger.info("CORS origins updated to include Ngrok")
    except Exception as e:
        logger.error(f"Ngrok manager failure: {e}")

    yield

    if os.getenv("NGROK_TOKEN"):
        try:
            from pyngrok import ngrok
            ngrok.kill()
        except Exception:
            pass


app = FastAPI(title="The All Time Helper - Pro", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

static_dir = os.path.join(BASE_DIR, "static")
templates_dir = os.path.join(BASE_DIR, "templates")

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

if os.path.exists(templates_dir):
    templates = Jinja2Templates(directory=templates_dir)

app.include_router(auth.router)
app.include_router(chat.router)


@app.get("/api/image_proxy")
async def image_proxy(url: str):
    """Proxy image requests with SSRF-resistant target validation."""
    import anyio
    import requests

    try:
        normalized_url = _normalize_pollinations_image_url(url)
        _validate_proxy_url(normalized_url)
    except ValueError:
        return Response(status_code=403)

    def fetch_with_validated_redirects():
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        }
        current_url = normalized_url
        for _ in range(4):
            parsed_current = _validate_proxy_url(current_url)
            response = requests.get(current_url, headers=headers, timeout=60.0, allow_redirects=False)
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("location")
                if not location:
                    return response
                current_url = urljoin(urlunparse(parsed_current), location)
                _validate_proxy_url(current_url)
                continue
            return response
        raise ValueError("Too many redirects")

    try:
        response = await anyio.to_thread.run_sync(fetch_with_validated_redirects)
        parsed = urlparse(normalized_url)

        if (
            parsed.hostname
            and parsed.hostname.lower() == "image.pollinations.ai"
            and response.status_code in {401, 402, 403}
        ):
            return Response(
                content="Pollinations rejected this model or account/budget.",
                media_type="text/plain",
                status_code=response.status_code,
            )

        if response.status_code != 200:
            logger.debug(f"Proxy failed for {normalized_url} with status {response.status_code}")
            return Response(status_code=response.status_code)

        content_type = response.headers.get("content-type", "image/png")
        if not content_type.lower().startswith("image/"):
            return Response(status_code=415)

        return Response(content=response.content, media_type=content_type)
    except Exception as e:
        logger.debug(f"Proxy exception: {e}")
        return Response(status_code=500)


@app.get("/")
async def serve_ui(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.get("/status")
async def get_status():
    import anyio
    import requests
    try:
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        await anyio.to_thread.run_sync(lambda: requests.get(ollama_url, timeout=0.5))
        return {"running": True}
    except Exception:
        return {"running": False}


@app.get("/api/upscale/status/{job_id}")
async def get_upscale_status(job_id: str):
    status = UpscaleManager.get_status(job_id)
    if not status:
        return {"success": False, "error": "Job not found"}
    return {"success": True, **status}


if __name__ == "__main__":
    import uvicorn
    logger.info("[Main] BINDING TO: 0.0.0.0:9000")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=9000,
        reload=True,
        reload_dirs=[os.path.join(BASE_DIR, "app"), os.path.join(BASE_DIR, "static"), os.path.join(BASE_DIR, "templates")],
    )
