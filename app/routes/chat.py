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
    persona: bool = False

class RetrieveRequest(BaseModel):
    text: str
    n: int = 3

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

from app.logic.memory import query_memory
from app.logic.neural_explainer import explain_neural_context

@router.post("/retrieve_context")
def retrieve_context(req: RetrieveRequest, current_user: str = Depends(get_current_user)):
    try:
        results = query_memory(req.text, n_results=req.n)
        
        # New: Generate an AI explanation for the snippets
        snippet_list = [r['content'] for r in results]
        explanation = explain_neural_context(req.text, snippet_list)
        
        return {
            "success": True, 
            "results": results, 
            "explanation": explanation
        }
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
                # Immediate feedback for the user
                yield json.dumps({"status": "Initializing Neural Core..."}).encode() + b'\n'
                await asyncio.sleep(0.5)

                if req.img:
                    yield json.dumps({"status": "Vision Pipeline Processing Image..."}).encode() + b'\n'
                else:
                    yield json.dumps({"status": "Scanning Semantic Memory..."}).encode() + b'\n'

                # Run the Agentic Brain in a separate thread
                task = asyncio.create_task(asyncio.to_thread(ask_the_helper, prompt, img, target_model, sys_config, history, req.persona))
                
                # HEARTBEAT LOOP: Yield a newline or status every 5s to keep the connection alive
                while not task.done():
                    yield b"\n" 
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
