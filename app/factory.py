import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

from app.database import init_db
from app.logger import logger
from app.logic.cloud_token_budget import apply_cloud_token_budget

apply_cloud_token_budget()

from app.routes import admin, auth, chat, email_delivery, health, jobs, proxy


def get_allowed_origins() -> list[str]:
    configured = os.getenv("ALLOWED_ORIGINS", "http://localhost:9000")
    origins = [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    public_url = str(os.getenv("NGROK_PUBLIC_URL") or "").strip().rstrip("/")
    if public_url and public_url not in origins:
        origins.append(public_url)
    return origins


def append_cors_origin(app: FastAPI, public_url: str) -> list[str]:
    """Add an exact origin to configured CORS middleware before request handling."""
    origin = str(public_url or "").strip().rstrip("/")
    if not origin:
        return []
    added = []
    for middleware in app.user_middleware:
        if middleware.cls is not CORSMiddleware:
            continue
        origins = middleware.kwargs.setdefault("allow_origins", [])
        if origin not in origins:
            origins.append(origin)
            added.append(origin)
    return added


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.diagnostics import run_startup_diagnostics
        from app.logic.memory import prune_stale_memories

        run_startup_diagnostics()
        prune_stale_memories(days=30)
    except Exception as exc:
        logger.error(f"Diagnostics/pruning failed: {exc}")

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="The All Time Helper - Pro", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_dir = BASE_DIR / "static"
    templates_dir = BASE_DIR / "templates"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(email_delivery.router)
    app.include_router(proxy.router)
    app.include_router(admin.router)
    app.include_router(jobs.router)
    app.include_router(health.router)
    init_db()
    return app
