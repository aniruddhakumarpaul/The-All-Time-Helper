import os
import sys
import subprocess
import logging

# ChromaDB's persistent store is unstable in this Windows project path unless
# the interpreter is started with -B. Direct script launches also need to avoid
# passing a path with spaces through reload/spawn machinery, so normalize them
# into a module launch from the project root.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True
if __name__ == "__main__" and (not sys.flags.dont_write_bytecode or __package__ in (None, "")):
    os.chdir(BASE_DIR)
    os.execv(sys.executable, [sys.executable, "-B", "-m", "app.main", *sys.argv[1:]])

# --- ENVIRONMENT GUARD ---
# Python 3.14+ introduces structural changes that break legacy Pydantic v1 dependencies
# used by CrewAI and LangChain. Enforce Python 3.12 for stability.
if sys.version_info >= (3, 14):
    print("\n" + "="*60)
    print("❌ ERROR: INCOMPATIBLE PYTHON VERSION DETECTED")
    print(f"Current: Python {sys.version.split()[0]}")
    print("Required: Python 3.12 (Highly Recommended for Agentic Swarm)")
    print("\nPlease run the project using the verified executable:")
    print("& C:/Users/aniruddha.paul/AppData/Local/Programs/Python/Python312/python.exe -B -m app.main")
    print("="*60 + "\n")
    sys.exit(1)

# --- BOOTSTRAP PATHS ---
# This allows running main.py directly from any directory
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, BackgroundTasks
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

def _append_cors_origin(fastapi_app: FastAPI, public_url: str):
    """Add a runtime origin to Starlette CORS config and any built stack."""
    if not public_url or not public_url.startswith(("http://", "https://")):
        return []

    origin = public_url.rstrip("/")
    if origin not in ALLOWED_ORIGINS:
        ALLOWED_ORIGINS.append(origin)

    for mw in fastapi_app.user_middleware:
        if mw.cls == CORSMiddleware:
            origins = list(mw.kwargs.get("allow_origins", []))
            if origin not in origins:
                origins.append(origin)
            mw.kwargs["allow_origins"] = origins

    current_app = fastapi_app.middleware_stack
    while current_app and hasattr(current_app, "app"):
        if isinstance(current_app, CORSMiddleware):
            current_app.allow_origins = list(current_app.allow_origins)
            if origin not in current_app.allow_origins:
                current_app.allow_origins.append(origin)
        current_app = current_app.app

    return [origin]

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
    except Exception as e:
        logger.error(f"[!] Database initialization error: {e}")

    # Run Pre-Flight Diagnostics
    try:
        from app.diagnostics import run_startup_diagnostics
        run_startup_diagnostics()
        
        # Memory Optimization Hook
        from app.logic.memory import prune_stale_memories, warmup_memory
        prune_stale_memories(days=30)
        warmup_memory()
    except Exception as e:
        logger.error(f"[!] Diagnostics/Pruning Error: {e}")
        
    # Startup logic: Ngrok Bridge
    try:
        from pyngrok import ngrok
        ngrok_token = os.getenv("NGROK_TOKEN")
        use_ngrok = os.getenv("USE_NGROK", "true").lower() == "true"
        if ngrok_token and use_ngrok:
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
                added = _append_cors_origin(app, public_url)
                if added:
                    logger.info("CORS origins updated to include Ngrok")
    except Exception as e:
        logger.error(f"Ngrok manager failure: {e}")
    
    yield
    # Shutdown logic (optional)
    if os.getenv("NGROK_TOKEN") and os.getenv("USE_NGROK", "true").lower() == "true":
        try:
            from pyngrok import ngrok
            ngrok.kill()
        except:
            pass

app = FastAPI(title="The All Time Helper - Pro", lifespan=lifespan)

# SECURITY FIX: Lock CORS to known origins instead of wildcard
ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:9000").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.get("/api/image_proxy")
async def image_proxy(url: str):
    """Proxies image requests to bypass CORS/Referrer blocks using 'requests'."""
    import requests
    from fastapi.responses import Response
    import anyio
    from app.logic.safe_fetch import SafeFetchError, safe_fetch_url
    
    try:
        def fetch():
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
            }
            return safe_fetch_url(
                url,
                headers=headers,
                timeout=60,
                max_bytes=8 * 1024 * 1024,
                request_get=requests.get,
            )
            
        response = await anyio.to_thread.run_sync(fetch)
        
        if response.status_code != 200:
            print(f"DEBUG: Proxy failed for {url} with status {response.status_code}")
            return Response(status_code=response.status_code)

        content_type = str(response.headers.get("content-type", "image/png")).split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            return Response(status_code=415)
            
        return Response(
            content=response.content, 
            media_type=content_type or "image/png"
        )
    except SafeFetchError as e:
        print(f"DEBUG: Proxy blocked {url}: {e}")
        return Response(status_code=e.status_code)
    except Exception as e:
        print(f"DEBUG: Proxy Exception: {e}")
        return Response(status_code=500)

@app.get("/")
async def serve_ui(request: Request):
    response = templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "api_base_url": str(request.base_url).rstrip("/"),
        },
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

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
        local_path = os.path.join(base_dir, "static", "uploads", f"upscaled_{job_id}.jpg")
        if os.path.exists(local_path):
            return {
                "success": True,
                "status": "ready",
                "url": f"/static/uploads/upscaled_{job_id}.jpg",
            }
        return {"success": False, "status": "missing", "error": "Job not found"}
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
