import os
import sys
import subprocess

# Ensure the parent project directory is injected into the Python path
# This allows you to run this file directly from anywhere without breaking imports!
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from app.routes import auth, chat
from app.database import init_db

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    try:
        from pyngrok import ngrok
        ngrok_token = os.getenv("NGROK_TOKEN")
        if ngrok_token:
            ngrok.set_auth_token(ngrok_token)
            
            # Check for existing tunnels to avoid redundant overhead on reload
            tunnels = ngrok.get_tunnels()
            if not tunnels:
                # Try to kill ghost ngrok instances quietly on Windows
                if os.name == 'nt':
                    subprocess.run("taskkill /F /IM ngrok.exe /T", shell=True, capture_output=True)
                
                public_url = ngrok.connect(9000).public_url
                print("\n" + "="*50)
                print("THE ALL TIME HELPER - PRO IS ONLINE!")
                print(f"PUBLIC (Ngrok): {public_url}")
                print(f"LOCAL: http://localhost:9000")
                print("="*50 + "\n")
            else:
                print(f"🌍 THE ALL TIME HELPER - PRO IS STILL ONLINE via {tunnels[0].public_url}")
    except Exception as e:
        print("Ngrok failed to start:", e)
    
    yield
    # Shutdown logic (optional)
    if os.getenv("NGROK_TOKEN"):
        try:
            from pyngrok import ngrok
            ngrok.kill()
        except:
            pass

app = FastAPI(title="The All Time Helper - Pro", lifespan=lifespan)

# Allow CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

@app.get("/")
async def serve_ui(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})

@app.get("/status")
async def get_status():
    import requests
    try:
        requests.get("http://localhost:11434", timeout=0.5)
        return {"running": True}
    except:
        return {"running": False}

if __name__ == "__main__":
    import uvicorn
    print("\n[+] BINDING TO: 0.0.0.0:9000 (Ngrok Bridge Optimized)")
    uvicorn.run("app.main:app", host="0.0.0.0", port=9000, reload=True, reload_dirs=[base_dir])
