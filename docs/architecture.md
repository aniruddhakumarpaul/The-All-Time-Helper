# Architecture

The All Time Helper is a FastAPI-based agentic assistant with a modular ES6 frontend, a CrewAI-driven backend, local Ollama fallback, and ChromaDB-backed neural memory.

## Core Layers
- `app/main.py`: FastAPI app startup, lifespan work, CORS, static files, and API wiring.
- `app/routes/chat.py`: streaming chat transport and final result yielding.
- `app/logic/agents.py`: intent classification, routing, direct tool execution, and agent orchestration.
- `app/logic/tools.py`: image, email, search, and memory-facing tools.
- `app/logic/memory.py`: ChromaDB-backed semantic memory and repair logic.
- `static/js/app.js`: frontend orchestrator and chat state persistence.
- `static/js/ui.js`: DOM rendering and widget composition.
- `static/js/utils.js`: markdown rendering and legacy global helpers.

## Design Rules
- Prefer deterministic direct tool paths for obvious workflows.
- Keep `main_v3.js` archived as rollback only.
- Use `InferenceQueue` for inference instead of raw thread offload.
- Keep frontend state in `state.js`, DOM work in `ui.js`, and network calls in `api.js`.

