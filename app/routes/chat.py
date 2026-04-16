from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
import sqlite3
import json
import traceback
import time
from pydantic import BaseModel
from typing import List, Optional, Any
from app.database import get_db
from app.repository import ChatRepository
from app.security import get_current_user

router = APIRouter()

class ChatRequest(BaseModel):
    prompt: str
    history: List[dict] = []
    model: str = "gemma2:2b"
    img: Optional[str] = None
    name: str = "Human"
    sys: dict = {}

@router.get("/get_chats")
def get_chats(current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    chats_array = ChatRepository.get_chats_for_user(db, current_user)
    return {"success": True, "chats": chats_array}

@router.post("/sync_chats")
def sync_chats(chats: List[dict], current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    try:
        ChatRepository.sync_user_chats(db, current_user, chats)
        return {"success": True}
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

from app.logic.agents import ask_the_helper

import asyncio

@router.post("/chat")
async def chat_endpoint(req: ChatRequest, current_user: str = Depends(get_current_user)):
    # Decision Engine: Choose between Pro (Agentic) and Local (Ollama)
    target_model = req.model
    prompt = req.prompt
    img = req.img
    history = req.history
    sys_config = req.sys
    
    # New Unified Agentic Flow (Supports both Cloud & Local with Tools)
    try:
        async def agent_stream():
            try:
                # Run the Agentic Brain in a separate thread to avoid blocking the event loop
                # We wrap it in a task so we can yield heartbeats while waiting
                task = asyncio.create_task(asyncio.to_thread(ask_the_helper, prompt, img, target_model, sys_config, history))
                
                # HEARTBEAT LOOP: Yield a space every 5s to keep the connection alive
                while not task.done():
                    yield b" \n" 
                    await asyncio.sleep(5)
                
                result = await task
                # Yield in the NDJSON format the frontend expects
                yield json.dumps({"message": {"content": str(result)}, "done": True}).encode() + b'\n'
            except Exception as e:
                traceback.print_exc()
                yield json.dumps({"message": {"content": f"⚠️ **Agent Error:** {str(e)}"}, "done": True}).encode() + b'\n'
        
        return StreamingResponse(agent_stream(), media_type="application/x-ndjson")
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}
