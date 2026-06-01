# Project Manifest: The All Time Helper

## Overview
A pro-grade Agentic Assistant with a 3D interactive mascot and a hierarchical agent swarm. Built on FastAPI + CrewAI + ChromaDB + ES6 Modular Frontend.

## Neural Memory (RAG)
- **Engine**: ChromaDB (Local Persistent)
- **Path**: `.project_brain/`
- **Primary Tools**: `recall_memory`, `archive_insight`
- **Chunking**: AST-aware (Python `ast` module, JS regex extraction, paragraph-based fallback)
- **Rebuild Command**: `python scripts/rebuild_memory.py`
- **Strategy**: Always use `recall_memory` for architectural context to save user tokens.

## Key Architectural Decisions
1. **Frontend**: ES6 modular architecture (`app.js` → `state.js`, `api.js`, `ui.js`, `mascot.js`). Monolith `main_v3.js` retired.
2. **State**: Reactive `AppState` class with `subscribe(key, cb)` / `set(key, val)` pattern. Single source of truth.
3. **Concurrency**: Custom `InferenceQueue` (1 worker by default, per-job timeout, backpressure at depth 8). Replaces raw `asyncio.to_thread`.
4. **Swarm Routing**: `_detect_intent()` returns `complexity: swarm|single|direct`. Single-tool tasks bypass hierarchy.
5. **Mascot Interaction**: "Teacher Peek" 3D tilt with 2s idle settle and 5s ultra-lazy return transition.
6. **One-Word Mode**: Hardened at Identity Level (Manager Agent goal injection).
7. **Agentic Swarm**: CrewAI with Process.hierarchical for complex tasks (email delegation).
8. **Local Fallback**: Routes sensitive/privacy-focused queries to local 'helper' (Ollama) model.

## ⚠️ Critical Rules for AI Agents
1. **DO NOT** modify `main_v3.js` — it is archived for rollback only. All frontend work goes in the ES6 modules.
2. **DO NOT** bypass `InferenceQueue` — never use raw `asyncio.to_thread` for inference in `chat.py`.
3. **Frontend changes** must respect the module boundary: state in `state.js`, DOM in `ui.js`, networking in `api.js`.
4. **Window bridge** lives in `app.js` only. No other module should set `window.*` except for legacy globals in `utils.js`.
5. **RAG re-index** after any structural file changes: `python scripts/rebuild_memory.py`.
6. **index.html** loads `app.js` as `type="module"`. Other scripts (`utils.js`, `palette.js`, `particles.js`) are non-module globals.

## User Preferences
- **Tone**: Professional, technical, but encouraging.
- **Efficiency**: Minimize token usage by using the semantic index instead of raw file reads.

## Progress Snapshot - 2026-06-01

### Pipeline Reliability
- ChromaDB neural memory is non-fatal: query failures, disk I/O errors, and bad-index errors are logged and return empty context instead of killing chat.
- Memory operations are guarded by a module-level lock, and `repair_memory_store(preserve=True)` can rebuild a fresh Chroma store while preserving existing documents when export succeeds.
- `_assemble_context()` continues when `future_memory.result()` fails, so agent requests survive neural-memory outages.
- Ngrok CORS startup patch uses Starlette middleware `kwargs` and only appends scheme-bearing origins such as `https://...`.

### Tool Result And Streaming
- Tool-result propagation was hardened through `tool_result_bus` writes for direct tool execution, CrewAI fast exits, and final hardened results.
- Status callback state moved from `threading.local()` to `ContextVar` so callback and abort scope survive executor handoffs.
- `chat.py` final-yield logic recognizes broader tool outputs, including `EMAIL_DRAFT_PAYLOAD:`, email success states, errors, and markdown image results.

### Email Widget And Draft Recovery
- `_harden_result()` can recover local-model JSON email drafts, including nested `send_email_tool` plans, into `EMAIL_DRAFT_PAYLOAD:` for the frontend widget.
- Deterministic image-to-email workflows bypass cloud/CrewAI routing: generate image, attach image, draft email payload, and return the widget without depending on OpenRouter.
- Follow-up email-template edits are deterministic: prompts like `fill address@example.com for the to and "body text" for the body` update the prior draft and return a fresh `EMAIL_DRAFT_PAYLOAD:` instead of raw JSON.
- Vague attachment prompts are context-aware: `attach the above` reuses recent images when unambiguous, resolves recipient from history, and asks whether to use image/text/both/summary when both are present.

### Generated Image Attachments
- Generated-image email attachments fail closed unless backend downloads real image bytes.
- Shared image download validation checks HTTP 200, image MIME/magic bytes, minimum byte size, timeout/retry behavior, and filename extension correction.
- `send_email_tool`, `resolve_chat_image`, and `send_or_simulate_email` use real base64 image bytes instead of raw Pollinations URLs.

### Chat Image Rendering UX
- Pollinations-generated image markdown with `uid` is rendered through the local upscale job flow instead of `/api/image_proxy`, preventing duplicate backend/browser fetch collisions.
- `static/js/utils.js` owns generated-image polling; `ui.js` calls polling hooks from all render paths.
- `/api/upscale/status/{job_id}` is durable: if in-memory status is gone but `static/uploads/upscaled_{job_id}.jpg` exists, it returns `ready`.
- When upscaling succeeds, chat history is rewritten from the Pollinations URL to the local `/static/uploads/upscaled_*.jpg` URL, so refresh/reload does not recreate the loader.

### Visual Task Continuity
- History-derived visual task continuity keeps follow-up refinements in the image pipeline.
- Prompts such as `long gown...`, `go ahead`, or `stop asking me so many questions` continue the prior visual task unless there is a clear context switch to email/search/code/factual chat.
- Clarification budget is effectively one turn for visual generation; the system favors action over repeated questioning once visual intent is established.

### Verification
- Latest backend regression suite: `python -B -m unittest app.tests.test_hardening` passed with 35 tests.
- JS syntax checks for the image-rendering changes passed with `node --check static/js/utils.js`, `node --check static/js/ui.js`, and `node --check static/js/app.js`.
