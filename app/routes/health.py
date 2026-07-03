import os
from pathlib import Path

import anyio
import requests
from fastapi import APIRouter, Request

from app.logic.upscaler import UpscaleManager

router = APIRouter()


@router.get("/")
async def serve_ui(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", {"request": request})


@router.get("/status")
async def get_status():
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    try:
        await anyio.to_thread.run_sync(lambda: requests.get(ollama_url, timeout=0.5))
        return {"running": True}
    except Exception:
        return {"running": False}


@router.get("/api/upscale/status/{job_id}")
async def get_upscale_status(job_id: str):
    status = UpscaleManager.get_status(job_id)
    if status:
        return {"success": True, **status}

    base_dir = Path(__file__).resolve().parents[2]
    output = base_dir / "static" / "uploads" / f"upscaled_{job_id}.jpg"
    if output.is_file():
        return {"success": True, "status": "ready", "url": f"/static/uploads/{output.name}"}
    return {"success": False, "status": "missing", "error": "Job not found"}
