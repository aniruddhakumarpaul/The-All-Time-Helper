# THE ALL TIME HELPER - PROJECT MAP
Last Sync: 2026-04-27 (Vision & Interaction Hardening)

## 🧠 Core Architecture
- **Framework**: FastAPI (Python 3.12+)
- **Brain**: CrewAI Agent Swarm (Hierarchical / Multi-Agent)
- **Vision Sub-system**: CLIP (Referential) & BLIP (Semantic) pixel analysis.
- **RAG Engine**: ChromaDB-powered Neural Memory.
- **Resilience**: Tenacity-powered Exponential Backoff & API Key Rotation.

## 📁 Component Registry
- **Entry Point**: `app/main.py` (FastAPI lifespan, router mounting)
- **Neural Vision Layer**: `app/logic/vision_pipeline.py` **[NEW]** (Pixel-to-Semantic translation)
- **Agents**: `app/logic/agents.py` (Swarm Factory, Multimodal Context Injection, Local Tool Unlocking)
- **Skills**: `app/logic/tools.py` (Visionary Art, DDG Search, Email, Astrology, Palm Analysis)
- **Memory Logic**: `app/logic/memory.py` (ChromaDB RAG integration)
- **Transport**: `app/routes/chat.py` (Streaming NDJSON, Neural Context /retrieve_context)
- **Frontend Logic**: `static/js/main_v3.js` (State Mgmt, Hold-G-to-Grab Neural Retrieval, Swipe Gestures)
- **Design System**: `static/css/style_v3.css` (Glassmorphism, Neural Grab Visual Feedback)

## 🔄 Interaction & Performance Logic
1. **Multimodal Grounding**: `agents.py` injects `[VISUAL_CONTEXT]` from the vision pipeline into agent tasks, enabling Llama-3-70B to "see" chat images.
2. **Neural Grab UX**: `main_v3.js` implements a dedicated "Grab Mode" triggered by holding the **'G' key**, decoupling RAG-dragging from standard text selection.
3. **Local Tool Empowerment**: Removed the "Gemma Lock" to allow local models (Gamma) to access image generation tools.
4. **Sidebar Ghost-Free Sync**: `toggleSidebar` explicitly clears inline styles to prevent "Ghost State" after swipe gestures.
5. **Surgical History Truncation**: Chat editing (`submitEdit`) now flushes the UI and truncates history arrays before resubmission to prevent display pollution.
6. **Iterative Safety**: Hard limit of `max_iter=3` on local agents to prevent infinite loops during tool execution.
7. **Connection Heartbeat**: `chat.py` implements a heartbeat pulse during agent execution to prevent `ERR_HTTP2_PROTOCOL_ERROR`.
8. **Semantic Persistence**: ChromaDB updated by `scripts/rebuild_memory.py` to ensure all historical code logic is retrievable.
9. **Responsive Resilience**: Unified state management handles 503 failovers and long-running task status gracefully.
