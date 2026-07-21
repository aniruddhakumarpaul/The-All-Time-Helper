# Architecture

The All Time Helper is a FastAPI-based agentic assistant with a modular ES6 frontend, a CrewAI-driven backend, local Ollama fallback, and ChromaDB-backed neural memory.

## Core Layers
- `app/factory.py`: FastAPI construction, lifespan work, CORS, static files, and router wiring.
- `app/main.py`: Thin ASGI import and local run entry point; owns the optional Ngrok session when executed directly.
- `app/routes/health.py`: UI, health, and upscale-status routes.
- `app/routes/proxy.py`: SSRF-resistant image proxy route.
- `app/services/ngrok.py`: Optional local Ngrok lifecycle, enabled only with `ENABLE_NGROK`; production ASGI imports never start tunnels.
- `app/schema_migrations.py`: Ordered transactional SQLite schema migrations and version tracking.
- `app/routes/chat.py`: streaming chat transport, bounded request models, owner-scoped attachment hydration, sync, context retrieval, and final result yielding.
- `app/routes/jobs.py`: owner-scoped active-task visibility and cancellation.
- `app/routes/admin.py`: authenticated, sanitized user-facing system readiness; it does not expose raw runtime configuration.
- `app/logic/agents.py`: compatibility facade, direct-tool routing, and top-level agent orchestration.
- `app/logic/agent_model_registry.py`: cloud model registry, API-key selection, fallback candidates, and the short-lived provider health circuit used by Helper Auto.
- `app/logic/cloud_token_budget.py`: cloud output caps plus package-level offline metadata, telemetry defaults, repository-local CrewAI storage, and bundled HTTPS trust roots applied before LiteLLM, CrewAI, or Ngrok imports.
- `app/logic/response_policy.py`: shared assistant identity, response-shape directives, and honesty contract for direct and agentic routes.
- `app/logic/agent_intent.py`: shared actionable-image/tool-discussion detection, code/direct routing policy, structured prompt analysis, and specialist selection.
- `app/logic/agent_context.py`: parallel memory/vision context assembly, validated structured multimodal payloads, and compact de-duplicated chat history.
- `app/logic/agent_cloud.py`: cloud tool/chat execution and provider fallback handling.
- `app/logic/agent_local.py`: local tool/chat execution and cloud fallback handling.
- `app/logic/agent_hardening.py`: result sanitization and deterministic payload recovery.
- `app/logic/tools.py`: image, email, search, and memory-facing tools with argument-free outcome/latency telemetry; DDGS and process-level HTTPS clients use the bundled `certifi` CA path on Windows while keeping TLS verification enabled. When anonymous search providers all fail, a configured OpenRouter key can invoke the cited web-search server tool with a three-result cap.
- `app/logic/memory.py`: ChromaDB-backed semantic memory with lock-guarded operations and a recoverable transient-failure circuit.
- `static/js/app.js`: frontend orchestrator and chat state persistence.
- `static/js/ui.js`: DOM rendering, persisted response preferences, non-blocking user feedback, and explicit per-message context-drag handles.
- `static/js/composer_context_tray.js`: bounded prompt context, explicit drag sources, and composer drop handling; message text remains selectable unless hold-`G` grab mode is active.
- `static/js/palette.js`: Ctrl+K command/search listbox, route switching, workspace shortcuts, and keyboard selection.
- `static/js/api.js`: normalized JSON/HTTP/network error contract for active frontend requests.
- `static/js/dialog_manager.js`: top-modal focus trapping, background isolation with `inert`, and invoking-control focus restoration.
- static/js/bootstrap.js: request-origin setup, centralized expired-session recovery, and ordered supplemental extension loading; active controls are never removed after page load.
- `static/js/utils.js`: markdown rendering and legacy global helpers.
- `static/js/motion_enhancements.js`: additive prompt motion plus delegated pointer and keyboard feedback for static and dynamic controls.
- `static/css/premium_motion.css`: additive motion timing, press/release feedback, and reduced-motion handling for the legacy visual shell.
- `static/css/product_controls.css`: accessibility-safe native-control resets, preference controls, and glass status feedback layered over the restored visual shell.
- Static and rendered controls bind events from JavaScript modules; active HTML contains no inline event attributes. See `docs/csp.md` for the not-yet-enabled CSP candidate.

## Design Rules
- Prefer deterministic direct tool paths for obvious workflows; explicit search and image actions must not initialize a CrewAI specialist merely because a larger local or cloud model is selected.
- Pass validated images natively to capable cloud/local models. Use cached Moondream perception only for simple local questions or text-only model routes, and never place base64 in textual prompts or logs.
- Keep `main_v3.js` archived as rollback only.
- Use `InferenceQueue` for execution instead of raw thread offload: model work remains serialized on one GPU-safe lane, while strictly classified model-free tools use two bounded workers with the same ownership, timeout, cancellation, and backpressure controls.
- Keep frontend state in `state.js`, DOM work in `ui.js`, and network calls in `api.js`.
- Default new requests to `helper-auto`; resolve to the configured cloud route when credentials exist and to the local multimodal route otherwise.
- Apply the shared response policy to direct cloud, direct local, and agentic execution so route changes do not change the assistant's honesty or output-quality contract.
- Treat Think, Research, Create, and Act as human-facing intent lanes. Provider and model IDs remain implementation controls, not the primary product vocabulary.
