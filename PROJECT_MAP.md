# THE ALL TIME HELPER - PROJECT MAP
Last Sync: 2026-04-16 (Performance & Resilience Hardening)

## 🧠 Core Architecture
- **Framework**: FastAPI (Python 3.12+)
- **Brain**: CrewAI Agent Swarm (Hierarchical / Multi-Agent)
- **Deployment**: Ngrok Tunneling (Port 9000)
- **Data Access**: Repository Pattern (Separated from Routes)
- **Resilience**: Tenacity-powered Exponential Backoff & API Key Rotation

## 📁 Component Registry
- **Entry Point**: `app/main.py` (FastAPI lifespan, router mounting)
- **Agents**: `app/logic/agents.py` (Swarm Factory, Cached LLM instantiation, Key Rotation)
- **Skills**: `app/logic/tools.py` (Visionary Art, DDG Search, Email, Astrology, Palm Analysis)
- **Data Layer**: `app/repository.py` (UserRepository & ChatRepository abstractions)
- **Transport**: `app/routes/chat.py` (Streaming NDJSON, System Config Injection)
- **Auth**: `app/routes/auth.py` (JWT, OTP Emailing, UserRepository integration)
- **Storage**: `app/database.py` (SQLite `users.db` initialization & connection pool)
- **Frontend**: `static/js/main_v3.js` (Unified State Mgmt, 503 Failover UI, Stream Sanitizer)
- **Visuals**: `templates/index.html` (Premium Glassmorphism, Theme Onboarding)

## 🔄 Resilience & Performance Logic
1. `agents.py` maintains a `GROQ_KEYS` pool for high-availability cloud tasks.
2. **Orchestration Auto-Scaling**: System uses Hierarchical Swarm for Cloud Pro, but switches to **Single-Agent Direct Mode** for local models to prevent JSON/Tool hallucinations.
3. **Dynamic Tool Pruning**: Automatically restricts "Mystic" tools (Palm/Horoscope) for sensitive topics like Mental Health/Medical to ensure faster, safer responses.
4. **Iterative Safety**: Hard limit of `max_iter=3` on local agents to prevent infinite loops during tool execution.
5. **Connection Heartbeat**: `chat.py` implements a heartbeat pulse during agent execution to prevent `ERR_HTTP2_PROTOCOL_ERROR` during long-running tasks.
6. **Conversation Memory**: `chat.py` now passes full thread history to agents, allowing for coherent multi-turn reasoning.
7. **Capability-Aware Safety**: Selective "Hard Tool Stripping" for local models (Llama 3.2) on sensitive topics to prevent JSON hallucinations, while keeping Cloud Pro (70B) fully empowered.
8. **Humanized Interaction**: Optimized prompt templates for greetings ("Hi", "Hello") to avoid the "Narrator Bug" and ensure direct, conversational responses.
9. `main_v3.js` handles UI state properly including the **Surgical Edit Cancel** logic (no-reload) and 503 failover.
10. **Neural Memory (RAG)**: `app/logic/memory.py` provides semantic context via ChromaDB, updated by `scripts/rebuild_memory.py`.
