# THE ALL TIME HELPER - PROJECT MAP
Last Sync: 2026-04-16

## 🧠 Core Architecture
- **Framework**: FastAPI (Python 3.12+)
- **Brain**: CrewAI Agent Swarm (Hierarchical)
- **Deployment**: Ngrok Tunneling (Port 9000)

## 📁 Component Registry
- **Entry Point**: `app/main.py` (Server init, lifespan, mounting)
- **Agents**: `app/logic/agents.py` (Manager, Senior Dev, Secretary, Mystic)
- **Skills**: `app/logic/tools.py` (Visionary Image, DDG Search, Email, Zodiac, Palm)
- **Transport**: `app/routes/chat.py` (Unified Agentic NDJSON Stream)
- **Auth**: `app/routes/auth.py` (JWT & OTP Logic)
- **Storage**: `app/database.py` (SQLite `users.db`, tables: `users`, `chats`)
- **Frontend**: `static/js/main_v3.js` (State: `user`, `chats`, `activeId`, `selectedModel`)
- **Visuals**: `templates/index.html` (Glassmorphism UI, Bot Visuals)

## 🔄 Execution Logic
1. `main_v3.js` sends prompt/model to `/chat`.
2. `chat.py` calls `ask_the_helper`.
3. `agents.py` builds the Crew (Manager + Specialists).
4. Tools are triggered based on task description.
5. Final response is returned as a single or streamed block.
