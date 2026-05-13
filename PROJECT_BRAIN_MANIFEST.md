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
3. **Concurrency**: Custom `InferenceQueue` (2 workers, 180s timeout, backpressure at depth 10). Replaces raw `asyncio.to_thread`.
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
