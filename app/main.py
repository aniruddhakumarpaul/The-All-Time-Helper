import os
import sys
import subprocess
import logging
import os
import sys

# --- BOOTSTRAP PATHS ---
# This allows running main.py directly from any directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from app.routes import auth, chat
from app.database import init_db
from app.logger import logger
from app.logic.upscaler import UpscaleManager

# Ensure the parent project directory is injected into the Python path
# This allows you to run this file directly from anywhere without breaking imports!
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run Pre-Flight Diagnostics
    try:
        from app.diagnostics import run_startup_diagnostics
        run_startup_diagnostics()
        
        # Memory Optimization Hook
        from app.logic.memory import prune_stale_memories
        prune_stale_memories(days=30)
    except Exception as e:
        logger.error(f"[!] Diagnostics/Pruning Error: {e}")
        
    # Startup logic: Ngrok Bridge
    try:
        from pyngrok import ngrok
        ngrok_token = os.getenv("NGROK_TOKEN")
        if ngrok_token:
            ngrok.set_auth_token(ngrok_token)
            
            # Check for existing tunnels via pyngrok
            tunnels = ngrok.get_tunnels()
            public_url = None
            
            if not tunnels:
                # Fallback: Check local ngrok API directly (handles cases where ngrok was started externally)
                import requests
                try:
                    res = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
                    if res.status_code == 200:
                        data = res.json().get("tunnels", [])
                        if data:
                            public_url = data[0].get("public_url")
                            logger.info(f"🌍 Detected external Ngrok tunnel: {public_url}")
                except Exception:
                    pass
            else:
                public_url = tunnels[0].public_url
                logger.info(f"🌍 THE ALL TIME HELPER - PRO IS ONLINE via {public_url}")

            if not public_url:
                # Try to kill ghost ngrok instances quietly on Windows
                if os.name == 'nt':
                    subprocess.run("taskkill /F /IM ngrok.exe /T", shell=True, capture_output=True)
                
                try:
                    tunnel = ngrok.connect(9000)
                    public_url = tunnel.public_url
                    logger.info(f"🚀 Started NEW Ngrok tunnel: {public_url}")
                except Exception as e:
                    logger.error(f"❌ Ngrok connect failed: {e}")

            # Ensure the public URL is in ALLOWED_ORIGINS for CORS
            if public_url and public_url not in ALLOWED_ORIGINS:
                ALLOWED_ORIGINS.append(public_url)
                # Also add the base domain without protocol if needed by some middlewares
                domain = public_url.replace("https://", "").replace("http://", "")
                if domain not in ALLOWED_ORIGINS:
                    ALLOWED_ORIGINS.append(domain)
                logger.info(f"🔒 CORS origins updated to include Ngrok")
    except Exception as e:
        logger.error(f"Ngrok manager failure: {e}")
    
    yield
    # Shutdown logic (optional)
    if os.getenv("NGROK_TOKEN"):
        try:
            from pyngrok import ngrok
            ngrok.kill()
        except:
            pass

app = FastAPI(title="The All Time Helper - Pro", lifespan=lifespan)

# SECURITY FIX: Lock CORS to known origins instead of wildcard
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:9000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Database
init_db()

# Ensure static and template directories exist for Mounting
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
static_dir = os.path.join(base_dir, "static")
templates_dir = os.path.join(base_dir, "templates")

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

if os.path.exists(templates_dir):
    templates = Jinja2Templates(directory=templates_dir)

# Include Routers
app.include_router(auth.router)
app.include_router(chat.router)


def _normalize_pollinations_image_url(url: str) -> str:
    """Normalize Pollinations image URLs before proxying."""
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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


@app.get("/api/image_proxy")
async def image_proxy(url: str):
    """Proxies image requests to bypass CORS/Referrer blocks using 'requests'."""
    import requests
    import anyio
    from urllib.parse import urlparse
    
    # SECURITY FIX #15: Validate URL to prevent SSRF attacks
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return Response(status_code=400)
        # Block internal/private IPs
        blocked_hosts = ["localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "metadata.google.internal"]
        if parsed.hostname and (parsed.hostname in blocked_hosts or parsed.hostname.startswith("10.") or parsed.hostname.startswith("192.168.")):
            return Response(status_code=403)
    except Exception:
        return Response(status_code=400)
    
    try:
        normalized_url = _normalize_pollinations_image_url(url)

        def fetch():
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
            }
            return requests.get(normalized_url, headers=headers, timeout=60.0, allow_redirects=True)
            
        response = await anyio.to_thread.run_sync(fetch)

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
            print(f"DEBUG: Proxy failed for {url} with status {response.status_code}")
            return Response(status_code=response.status_code)
            
        return Response(
            content=response.content, 
            media_type=response.headers.get("content-type", "image/png")
        )
    except Exception as e:
        print(f"DEBUG: Proxy Exception: {e}")
        return Response(status_code=500)

@app.get("/")
async def serve_ui(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})

@app.get("/status")
async def get_status():
    # FIX #12: Non-blocking status check using anyio
    import requests
    import anyio
    try:
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        await anyio.to_thread.run_sync(lambda: requests.get(ollama_url, timeout=0.5))
        return {"running": True}
    except Exception:
        return {"running": False}

# API: Upscale Status Polling
@app.get("/api/upscale/status/{job_id}")
async def get_upscale_status(job_id: str):
    status = UpscaleManager.get_status(job_id)
    if not status:
        return {"success": False, "error": "Job not found"}
    return {"success": True, **status}

if __name__ == "__main__":
    import uvicorn
    logger.info("[Main] BINDING TO: 0.0.0.0:9000 (Ngrok Bridge Optimized)")
    # FIX #14: Exclude .project_brain (ChromaDB) and scratch dirs from reload watching
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=9000, 
        reload=True, 
        reload_dirs=[os.path.join(base_dir, "app"), os.path.join(base_dir, "static"), os.path.join(base_dir, "templates")]
    )
