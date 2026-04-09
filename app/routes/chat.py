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

@router.post("/chat")
def chat_endpoint(req: ChatRequest, current_user: str = Depends(get_current_user)):
    sys_opts = req.sys
    prompt = req.prompt
    history = req.history
    img = req.img
    target_model = req.model
    
    instructions = [
        "You are the AI assistant for 'The All Time Helper'."
    ]
    if sys_opts.get('english'): instructions.append("You MUST respond ONLY in English.")
    if sys_opts.get('oneword'): instructions.append("Respond in ONE WORD only.")
    if sys_opts.get('pers'):
        instructions.append(f"Address the user as {req.name}.")
        
    weather_triggers = ["how is the day today", "weather today", "how's the day today"]
    if any(t in prompt.lower() for t in weather_triggers):
        weather_info = "The current weather in Mumbai, India, is mostly sunny with a temperature of 29°C (85°F), feeling like 31°C (88°F). Humidity is 55% with a 0% chance of rain."
        instructions.append("CRITICAL INSTRUCTION FOR GREETINGS:")
        instructions.append(f"If the user's prompt is exactly, or a close variation of, 'hey how is the day today' or 'how is the day today?', you MUST respond with the following weather info: {weather_info}")
        instructions.append("Respond STRICTLY with a summary of this weather as your entire greeting.")
        instructions.append("Do NOT reply with phrases like 'I am doing well, how are you?' or ask about the user's agenda when this specific phrase is used.")
        
    messages = []
    if instructions:
        messages.append({"role": "system", "content": " ".join(instructions)})
        
    for m in history:
        role = "user" if m['r'] == 'u' else "assistant" # Ollama uses 'assistant', Gemini handles it below
        msg = {"role": role, "content": m['c']}
        if m.get('i'): msg["images"] = [m['i']]
        messages.append(msg)
        
    if not history or history[-1].get('c') != prompt:
        curr_msg = {"role": "user", "content": prompt}
        if img: curr_msg["images"] = [img]
        messages.append(curr_msg)

    if target_model.startswith('gemini-'):
        # STRICTLY use GROQ_API_KEY from .env
        gkey = os.getenv("GROQ_API_KEY")
        if not gkey:
            def error_stream():
                yield json.dumps({"message": {"content": "⚠️ **Error:** Antigravity API Key is not configured on the server. Please contact the administrator."}, "done": True}).encode() + b'\n'
            return StreamingResponse(error_stream(), media_type="application/x-ndjson")

        def groq_stream():
            try:
                # Initialize the Groq client (using OpenAI SDK)
                client = OpenAI(
                    api_key=gkey,
                    base_url="https://api.groq.com/openai/v1"
                )
                
                # Mapping any "Gemini" model from the UI to Groq's best Llama model
                groq_model = "llama-3.3-70b-versatile"
                
                # Construct messages for OpenAI/Groq format
                groq_messages = []
                if instructions:
                    groq_messages.append({"role": "system", "content": " ".join(instructions)})
                
                for m in history:
                    role = "user" if m['r'] == 'u' else "assistant"
                    msg = {"role": role, "content": m['c']}
                    # Note: We are omitting images for now as Groq's Llama is primarily text-focused
                    groq_messages.append(msg)
                
                groq_messages.append({"role": "user", "content": prompt})

                # Call Groq with streaming enabled
                response = client.chat.completions.create(
                    model=groq_model,
                    messages=groq_messages,
                    stream=True
                )
                
                for chunk in response:
                    try:
                        content = chunk.choices[0].delta.content
                        if content:
                            out = json.dumps({"message": {"content": content}, "done": False})
                            yield out.encode() + b'\n'
                    except Exception:
                        continue
                yield json.dumps({"done": True}).encode() + b'\n'
            except Exception as e:
                traceback.print_exc()
                yield json.dumps({"message": {"content": f"⚠️ **Groq Error:** {str(e)}"}, "done": True}).encode() + b'\n'
                
        return StreamingResponse(groq_stream(), media_type="application/x-ndjson")

    # Ollama logic
    payload = {"model": target_model, "messages": messages, "stream": True}
    
    def ollama_stream():
        try:
            with requests.post(OLLAMA_URL, json=payload, stream=True) as r:
                if r.status_code != 200:
                    yield json.dumps({"message": {"content": f"⚠️ **Ollama Error:** Received HTTP status {r.status_code}."}, "done": True}).encode() + b'\n'
                    return
                for line in r.iter_lines():
                    if line: 
                        yield line + b'\n'
        except Exception as e:
            yield json.dumps({"message": {"content": f"⚠️ **Ollama Error:** Could not connect to local Ollama. Make sure Ollama is running.\n\nDetails: {e}"}, "done": True}).encode() + b'\n'
            
    return StreamingResponse(ollama_stream(), media_type="application/x-ndjson")
