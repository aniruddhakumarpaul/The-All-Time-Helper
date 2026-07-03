# Architecture

The All Time Helper is a FastAPI-based agentic assistant with a modular ES6 frontend, a CrewAI-driven backend, local Ollama fallback, and ChromaDB-backed neural memory.

## Core Layers
- `app/factory.py`: FastAPI construction, lifespan work, CORS, static files, and router wiring.
- `app/main.py`: Thin ASGI import and local run entry point.
- `app/routes/health.py`: UI, health, and upscale-status routes.
- `app/routes/proxy.py`: SSRF-resistant image proxy route.
- `app/services/ngrok.py`: Optional local Ngrok lifecycle, enabled only with `ENABLE_NGROK`.
- `app/schema_migrations.py`: Ordered transactional SQLite schema migrations and version tracking.
- `app/routes/chat.py`: streaming chat transport and final result yielding.
- `app/logic/agents.py`: intent classification, routing, direct tool execution, and agent orchestration.
- `app/logic/tools.py`: image, email, search, and memory-facing tools.
- `app/logic/memory.py`: ChromaDB-backed semantic memory and repair logic.
- `static/js/app.js`: frontend orchestrator and chat state persistence.
- `static/js/ui.js`: DOM rendering and widget composition.
- `static/js/utils.js`: markdown rendering and legacy global helpers.
- Static and rendered controls bind events from JavaScript modules; active HTML contains no inline event attributes. See `docs/csp.md` for the not-yet-enabled CSP candidate.

## Design Rules
- Prefer deterministic direct tool paths for obvious workflows.
- Keep `main_v3.js` archived as rollback only.
- Use `InferenceQueue` for inference instead of raw thread offload.
- Keep frontend state in `state.js`, DOM work in `ui.js`, and network calls in `api.js`.
