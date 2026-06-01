# THE ALL TIME HELPER - PROJECT MAP
Last Sync: 2026-06-01 (Pipeline Reliability, Image UX, Email Widget Continuity)

## ðŸ§  Core Architecture
- **Framework**: FastAPI (Python 3.12+)
- **Brain**: CrewAI Agent Swarm (Hierarchical / Multi-Agent)
- **Vision Sub-system**: Moondream (Local Ollama) pixel-to-semantic analysis.
- **RAG Engine**: ChromaDB-powered Neural Memory with AST-aware chunking.
- **Concurrency**: Custom InferenceQueue with backpressure (replaces raw asyncio.to_thread).
- **Resilience**: Tenacity-powered Exponential Backoff & API Key Rotation.

## ðŸ“ Component Registry

### Backend
- **Entry Point**: `app/main.py` (FastAPI lifespan, router mounting)
- **Agents**: `app/logic/agents.py` (Swarm Factory, `_detect_intent()` complexity routing, Multimodal Context Injection)
- **Skills**: `app/logic/tools.py` (Visionary Art, DDG Search, Email, Astrology, Palm Analysis)
- **Memory Logic**: `app/logic/memory.py` (ChromaDB RAG integration)
- **Inference Queue**: `app/inference_queue.py` **[NEW]** (Async worker pool with backpressure & timeout)
- **Transport**: `app/routes/chat.py` (Streaming NDJSON, Neural Context /retrieve_context)
- **Memory Rebuild**: `scripts/rebuild_memory.py` (AST-aware chunking: Python AST + JS regex)

### Frontend (ES6 Modular)
- **Entry Point**: `static/js/app.js` **[NEW]** (Orchestrator â€” imports modules, window.* bridge, send/load/save logic)
- **State Manager**: `static/js/state.js` **[NEW]** (Reactive AppState with subscribe()/set())
- **API Client**: `static/js/api.js` **[NEW]** (All fetch/networking â€” auth, streaming, sync)
- **UI Controller**: `static/js/ui.js` **[NEW]** (DOM manipulation â€” addMsg, renderHist, modals, theme)
- **Mascot Engine**: `static/js/mascot.js` **[NEW]** (Cursor tracking, jiggle/pop, bot reactions)
- **Utilities**: `static/js/utils.js` (renderMarkdown, copyCode, downloadCode â€” global script)
- **Command Palette**: `static/js/palette.js` (Ctrl+K command palette â€” global script)
- **Particles**: `static/js/particles.js` (Canvas particle system â€” global script)
- **Legacy Backup**: `static/js/main_v3.js` (Original monolith â€” NOT loaded, kept for rollback)
- **Design System**: `static/css/style_v3.css` (Glassmorphism, Neural Grab Visual Feedback)

## ðŸ”„ Interaction & Performance Logic
1. **Swarm Complexity Routing**: `_detect_intent()` classifies as `swarm`/`single`/`direct` â€” single-tool tasks skip the full hierarchy, saving ~60% tokens.
2. **AST-Aware RAG**: Python files chunked by function/class via `ast` module. JS files chunked by regex function extraction. No more mid-function splits.
3. **Inference Queue**: 1-worker async pool with per-job timeout and max queue depth of 8. Prevents GPU contention on weak local hardware.
4. **ES6 Module Architecture**: Frontend split from 1133-line monolith into 5 focused modules with reactive state management.
5. **Neural Grab UX**: `app.js` implements "Grab Mode" triggered by holding **'G' key**, decoupling RAG-dragging from text selection.
6. **Sidebar Ghost-Free Sync**: `toggleSidebar` clears inline styles to prevent ghost state after swipe gestures.
7. **Surgical History Truncation**: Chat editing (`submitEdit`) flushes UI and truncates history arrays before resubmission.
8. **Iterative Safety**: Hard limit of `max_iter=3` on local agents to prevent infinite loops.
9. **Connection Heartbeat**: `chat.py` pulses during agent execution to prevent `ERR_HTTP2_PROTOCOL_ERROR`.
10. **Reactive State**: `state.subscribe(key, callback)` enables decoupled, event-driven UI updates across modules.
11. **Ngrok Resilience**: `main.py` dynamically detects external tunnels and updates CORS origins to prevent `ERR_NGROK_334` and CORS blocks.
12. **Proactive Security Guardrails**: `agents.py` performs pre-execution checks for sensitive tasks (e.g., Email). Instantly prompts for `ADMIN_KEY` if missing, bypassing slow agentic loops.
13. **Weak Model Balancing**: Automatically reduces `max_iter` and simplifies task grounding for small models (e.g., Gemma 2B) to prevent stalls and hallucinations.
14. **Non-Fatal Neural Memory**: `memory.py` treats ChromaDB failures as recoverable. Query/write/delete paths are lock-guarded, unhealthy memory returns empty context, and `repair_memory_store(preserve=True)` can rebuild a store without blocking chat.
15. **Tool Result Bus Recovery**: Direct tool execution, CrewAI fast exits, and hardened final results write to `tool_result_bus`, allowing streaming recovery of tool payloads that would otherwise be lost.
16. **ContextVar Callback Scope**: Agent status callbacks and abort events use `ContextVar` instead of `threading.local()` so status updates survive executor and CrewAI thread boundaries.
17. **Deterministic Image Email Pipeline**: Image-generation-to-email requests bypass cloud/CrewAI routing, generate or resolve the image first, attach real image bytes, and return `EMAIL_DRAFT_PAYLOAD:` for the widget.
18. **Generated Image Byte Safety**: URL image attachments must download valid image bytes with MIME/magic-byte validation and minimum-size checks. Raw Pollinations URLs are not accepted as ready attachments.
19. **Generated Image Chat UX**: Pollinations `uid` images use local upscale status polling, durable disk fallback via `/api/upscale/status/{job_id}`, and saved-chat URL rewriting to `/static/uploads/upscaled_*.jpg`.
20. **Visual Task Continuity**: Recent chat history keeps unresolved visual generation tasks active, so follow-up refinements and proceed/frustration messages route back to `image_generate_tool`.
21. **Conversational Email Widget Edits**: Follow-up prompts like `attach the above`, `fill ... for the to`, and `... for the body` update or create `EMAIL_DRAFT_PAYLOAD:` widgets directly instead of returning raw `send_email_tool` JSON plans.
