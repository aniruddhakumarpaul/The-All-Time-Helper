from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
import sqlite3
import json
import base64
import requests
import traceback
import time
from pydantic import BaseModel
from typing import List, Optional, Any
from app.database import get_db
from app.security import get_current_user

import os
from openai import OpenAI

router = APIRouter()

OLLAMA_URL = "http://localhost:11434/api/chat"

class ChatRequest(BaseModel):
    prompt: str
    history: List[dict] = []
    model: str = "gemma2:2b"
    img: Optional[str] = None
    name: str = "Human"
    sys: dict = {}

@router.get("/get_chats")
def get_chats(current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    c = db.cursor()
    c.execute("SELECT id, title, messages_json FROM chats WHERE user_email=? ORDER BY updated_at ASC", (current_user,))
    rows = c.fetchall()
    chats_array = []
    for r in rows:
        ms = []
        if r['messages_json']:
            try:
                ms = json.loads(r['messages_json'])
            except:
                pass
        chats_array.append({
            "id": r['id'],
            "title": r['title'],
            "ms": ms
        })
    return {"success": True, "chats": chats_array}

@router.post("/sync_chats")
def sync_chats(chats: List[dict], current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    print(f"DEBUG: sync_chats called for {current_user}")
    print(f"DEBUG: Received {len(chats)} chats")
    c = db.cursor()
    try:
        c.execute("DELETE FROM chats WHERE user_email=?", (current_user,))
        for chat in chats:
            cid = chat.get('id')
            title = chat.get('title', 'New Chat')
            ms = chat.get('ms', [])
            print(f"DEBUG: Syncing chat {cid} with {len(ms)} messages")
            c.execute("INSERT INTO chats (id, user_email, title, messages_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                      (cid, current_user, title, json.dumps(ms), time.time()))
        db.commit()
        print("DEBUG: Sync committed successfully")
        return {"success": True}
    except Exception as e:
        print(f"ERROR: Sync failed: {e}")
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
    
    # CASE 1: AGENTIC PRO (Groq Swarm)
    if target_model == "agentic-pro":
        try:
            async def agent_stream():
                try:
                    # Run the Agentic Brain in a separate thread to avoid blocking
                    result = await asyncio.to_thread(ask_the_helper, prompt, img)
                    # Yield in the NDJSON format the frontend expects
                    yield json.dumps({"message": {"content": str(result)}, "done": True}).encode() + b'\n'
                except Exception as e:
                    traceback.print_exc()
                    yield json.dumps({"message": {"content": f"⚠️ **Agent Error:** {str(e)}"}, "done": True}).encode() + b'\n'
            
            return StreamingResponse(agent_stream(), media_type="application/x-ndjson")
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e)}

    # CASE 2: LOCAL OLLAMA MODELS (Gemma, Llama, Dolphin, etc.)
    else:
        async def ollama_stream():
            try:
                # Prepare payload for Ollama
                # We extract messages from history and add the latest prompt
                messages = []
                # History is structured as [{"r": "u/b", "c": "text", "i": "base64"}]
                for h in history:
                    role = "user" if h.get("r") == "u" else "assistant"
                    msg = {"role": role, "content": h.get("c", "")}
                    if h.get("i"):
                        msg["images"] = [h["i"]]
                    messages.append(msg)
                
                # Add the current prompt if it's not already the last one (safety check)
                if not history or history[-1].get("c") != prompt:
                    current_msg = {"role": "user", "content": prompt}
                    if img:
                        current_msg["images"] = [img]
                    messages.append(current_msg)

                payload = {
                    "model": target_model,
                    "messages": messages,
                    "stream": True
                }
                
                # Call Ollama
                with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=120) as r:
                    for line in r.iter_lines():
                        if line:
                            yield line + b'\n'
                            
            except Exception as e:
                traceback.print_exc()
                yield json.dumps({"message": {"content": f"⚠️ **Local LLM Error:** {str(e)}\n\n*Check if Ollama is running.*"}, "done": True}).encode() + b'\n'

        return StreamingResponse(ollama_stream(), media_type="application/x-ndjson")
