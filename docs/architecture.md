# Architecture

The All Time Helper is a FastAPI-based agentic assistant with a modular ES6 frontend, a CrewAI-driven backend, local Ollama fallback, and ChromaDB-backed neural memory.

## Core Layers
- `POST /chat/jobs`: creates an authenticated server-owned job before subscription; `GET /chat/jobs/{job_id}/events?after=N` is the authoritative NDJSON cursor stream, while `GET /chat/jobs/{job_id}` remains the snapshot/recovery surface. All job responses are private and no-store.
- `app/logic/chat_job_registry.py`: SQLite WAL persistence is the default shared local backend. It stores owner-scoped bounded events, terminal content, TTLs, cancellation intent, cursor sequences, event compaction, and retention limits. A process-local cancel event is only an execution optimization; a watcher polls durable cancellation so another worker/tab can stop the local queue.
- `app/factory.py`: FastAPI construction, lifespan work, CORS, static files, and router wiring.
- `app/main.py`: Thin ASGI import and local run entry point; owns the optional Ngrok session when executed directly.
- `app/routes/health.py`: UI, health, and upscale-status routes.
- `app/routes/proxy.py`: SSRF-resistant image proxy route.
- `app/services/ngrok.py`: Optional local Ngrok lifecycle, enabled only with `ENABLE_NGROK`; production ASGI imports never start tunnels.
- `app/schema_migrations.py`: Ordered transactional SQLite schema migrations and version tracking.
- `app/routes/chat.py`: streaming chat transport, bounded request models, owner-scoped attachment hydration, sync, context retrieval, and final result yielding.
- `app/routes/jobs.py`: owner-scoped active-task visibility and cancellation.
- `app/routes/admin.py`: authenticated, sanitized user-facing system readiness; it does not expose raw runtime configuration.
- `app/observability.py`: opt-in application-owned OpenTelemetry providers, trace/metric instruments, context propagation, LiteLLM attempt accounting, and fail-open lifecycle handling. It never captures prompt, response, tool, attachment, recipient, credential, or owner content.
- `app/logic/usage_ledger.py`: dedicated schema-version-1 SQLite-WAL provider-attempt ledger with pseudonymous owner scopes, unique event IDs, bounded retention/count/logical storage, short retrying writes, and aggregate-only reads.
- `app/routes/usage.py`: authenticated owner-scoped `GET /usage/summary` aggregates for the fixed `24h`, `7d`, and `30d` windows; responses are private/no-store and raw usage rows are not exposed.
- `app/logic/agents.py`: compatibility facade, direct-tool routing, and top-level agent orchestration.
- `app/logic/agent_model_registry.py`: authoritative request-route and provider-model registry, API-key selection, fallback candidates, and the short-lived provider health circuit used by Helper Auto. Arbitrary request text is not treated as an Ollama model; unsupported routes fail with a controlled HTTP 400, while the intentionally dynamic Gemini namespace remains bounded and explicit.
- `app/logic/telemetry_metadata.py`: semantic telemetry firewall. Model and provider fields are not trusted by name: registered identifiers remain attributable, dynamic provider identifiers become finite `*/custom` buckets, and operation/source/status/error/fallback/workflow/queue dimensions use explicit finite classifications.
- `app/logic/cloud_token_budget.py`: cloud output caps plus package-level offline metadata, telemetry defaults, repository-local CrewAI storage, and bundled HTTPS trust roots applied before LiteLLM, CrewAI, or Ngrok imports.
- `app/logic/response_policy.py`: shared assistant identity, response-shape directives, and honesty contract for direct and agentic routes.
- `app/logic/agent_intent.py`: shared actionable-image/tool-discussion detection, code/direct routing policy, structured prompt analysis, and specialist selection.
- `app/logic/agent_context.py`: parallel memory/vision context assembly, validated structured multimodal payloads, and compact de-duplicated chat history.
- `app/logic/agent_cloud.py`: cloud tool/chat execution and provider fallback handling.
- `app/logic/agent_local.py`: local tool/chat execution and cloud fallback handling.
- `app/logic/agent_hardening.py`: result sanitization and deterministic payload recovery.
- `app/logic/workflow_orchestrator.py`: typed intent classification and planning plus the runtime adapter that claims durable workflow/action state before invoking existing tools. The compatibility `PendingWorkflowStore` is a facade over SQLite, not process-local authority.
- `app/logic/workflow_store.py`: versioned SQLite-WAL workflow runs, actions, approvals, and bounded events. Owner-scoped transactions provide execution leases, per-action claims, durable cancellation, safe interruption recovery, capacity enforcement, and sanitized recovery DTOs without executing or replaying work.
- `app/logic/action_outbox.py`: schema-versioned external-mutation dispatch ledger in the workflow database. It stores pseudonymous owner scope, hashed idempotency keys, payload fingerprints, leases, dispatch boundaries, and terminal outcomes without storing action bodies or authorization credentials.
- `app/logic/capability_policy.py`: policy version 1, a code-defined frozen capability registry, structured fail-closed decisions, immutable capability-to-handler adapters, exhaustive workflow/tool inventories, trusted execution context, and low-cardinality `[CapabilityTrace]` records. Model/tool arguments are never approval evidence.
- `app/routes/workflows.py`: authenticated owner-only workflow list/detail/cancel recovery surfaces. Every success and not-found response is private and no-store; plans, arguments, outputs, recipients, and credentials are not exposed.
- `app/logic/tools.py`: image, draft, search, and memory-facing tools with argument-free outcome/latency telemetry. `build_email_draft_tool` is the non-delivering draft tool; `send_email_tool` remains a compatibility alias only. DDGS and process-level HTTPS clients use the bundled `certifi` CA path on Windows while keeping TLS verification enabled. When anonymous search providers all fail, a configured OpenRouter key can invoke the cited web-search server tool with a three-result cap.
- `app/services/email_delivery_service.py`: protected request-scoped Admin Key verification, draft/recipient validation, owner-scoped attachment delivery, transactional outbox dispatch, and sanitized results shared by the HTTP route and workflow executor. The historical `email_send_log` is read-only compatibility evidence, not dispatch authority.
- `app/logic/memory.py`: ChromaDB-backed semantic memory with lock-guarded operations and a recoverable transient-failure circuit.
- `static/js/app.js`: frontend orchestrator and chat state persistence; masked approval input is request-only and active draft context is reset on chat changes before structured cards are replayed. Local-first cache writes remain intact when `/sync_chats` fails, with one visible warning per failure window.
- `GET /chat/jobs/{job_id}` reconnects an authenticated owner to a durable active or terminal response. SQLite WAL rows carry a provisional launch deadline followed by an execution lease and heartbeat; an expired launch or active lease is converted once to a safe failed/interrupted terminal result, never replayed automatically.
- `app/logic/chat_job_registry.py`: bounded, owner-scoped SQLite job events and terminal results with atomic publish/cancel/fail/complete transitions. Event payloads and retained event storage are UTF-8 byte bounded, terminal results are preserved during compaction, and active leased jobs are not pruned as ordinary retention records.
- static/js/email_draft.js: editable draft cards, live metadata-only workflow context, multiple attachment chips, sandboxed preview, explicit handle/fallback actions, and superseding older cards when an updated draft is rendered.
- `static/js/ui.js`: DOM rendering, persisted response preferences, non-blocking user feedback, and explicit per-message context-drag handles. The rename control temporarily becomes a fixed, animated popover so long titles are not clipped by the sidebar.
- `static/css/product_controls.css`: final prompt sizing contract and control overrides applied after density styles.
- static/js/composer_context_tray.js: the single owner of bounded prompt context, explicit drag sources, MIME transfer, deduplication, and composer drop handling; message text remains selectable unless hold-G grab mode is active. Email cards expose only a dedicated drag handle.
- `static/js/palette.js`: Ctrl+K command/search listbox, route switching, workspace shortcuts, and keyboard selection.
- `static/js/api.js`: normalized JSON/HTTP/network error contract for active frontend requests.
- `static/js/dialog_manager.js`: top-modal focus trapping, background isolation with `inert`, and invoking-control focus restoration.
- `static/js/runtime_config.js`: server-rendered UI feature flags; `OUTSIDE_CLICK_DISMISS=false` disables backdrop/outside-click dismissal while preserving Escape and explicit close controls.
- static/js/bootstrap.js: request-origin setup, centralized expired-session recovery, and ordered supplemental extension loading; active controls are never removed after page load.
- `static/js/utils.js`: markdown rendering and legacy global helpers.
- `static/js/motion_enhancements.js`: additive prompt motion, delegated pointer/keyboard feedback, and sign-out fire lifecycle timing. CSS hover starts the sign-out fire independently, while JavaScript only keeps it alive through the return transition.
- `static/css/premium_motion.css`: additive motion timing, press/release feedback, and reduced-motion handling for the legacy visual shell.
- `static/css/product_controls.css`: accessibility-safe native-control resets, preference controls, and glass status feedback layered over the restored visual shell.
- Static and rendered controls bind events from JavaScript modules; active HTML contains no inline event attributes. See `docs/csp.md` for the not-yet-enabled CSP candidate.

## Design Rules
- Prefer deterministic direct tool paths for obvious workflows; explicit search and image actions must not initialize a CrewAI specialist merely because a larger local or cloud model is selected.
- Plan known compound email workflows before the cloud/local split. Execute the plan once inside the existing bounded tool lane: independent search actions may overlap, dependent update/attach/delivery actions wait, and no workflow action resubmits into the queue.
- Treat draft building as terminal only after all plan dependencies finish. Research, image search/generation, and attachment preparation are non-sensitive; only actual delivery may pause for owner-scoped request authorization.
- Required dependencies are success-only while optional research dependencies are settled-only; a failed generated image blocks attachment and returns controlled failure prose without replacing the active email widget.
- Reference, actual, real, and existing images use image search. Explicit generate/create/draw/paint/render requests use image generation unless real/reference wording makes search authoritative.
- On cancellation, stop scheduling new workflow actions and return the existing controlled NDJSON cancellation shape. Blocking third-party calls are best-effort cancellation boundaries and must not trigger delivery afterward.
- Pass validated images natively to capable cloud/local models. Use cached Moondream perception only for simple local questions or text-only model routes, and never place base64 in textual prompts or logs.
- Keep `main_v3.js` archived as rollback only.
- Use `InferenceQueue` for execution instead of raw thread offload: model work remains serialized on one GPU-safe lane, while strictly classified model-free tools use two bounded workers with the same ownership, timeout, cancellation, and backpressure controls.
- Keep frontend state in `state.js`, DOM work in `ui.js`, and network calls in `api.js`.
- Default new requests to `helper-auto`; resolve to the configured cloud route when credentials exist and to the local multimodal route otherwise.
- Apply the shared response policy to direct cloud, direct local, and agentic execution so route changes do not change the assistant's honesty or output-quality contract.
- Treat Think, Research, Create, and Act as human-facing intent lanes. Provider and model IDs remain implementation controls, not the primary product vocabulary.
- Document attachments are extracted as bounded text context and are never classified as visual inputs; only validated image MIME types enter vision analysis.

## Workflow And Persistence Reliability

- Phase 3 trace hierarchy starts at `helper.chat.execute`, retains context through the bounded queue and `asyncio.to_thread`, and adds queue, workflow, action, capability, memory, and GenAI provider spans. Random job/workflow IDs may be trace attributes for correlation; owner identity is never exported.
- OpenTelemetry export is disabled by default (`HELPER_OTEL_ENABLED=false`). When enabled, the application uses OTLP HTTP/protobuf and low-cardinality service Resource attributes. CrewAI telemetry remains disabled; no console exporter is installed.
- Provider attempt metrics and ledger writes are finalized exactly once. Tokens are recorded only from provider/LiteLLM usage, cloud cost remains null when unreported, and known local Ollama operations use cost source `local` with cost `0.0`.
- Request and response model labels are registry-backed at export and ledger boundaries. Unregistered OpenRouter, Ollama, and Gemini metadata is bucketed before export; other unknown model/provider metadata becomes `unknown`. Capability IDs must come from the frozen registry rather than merely matching a safe-character regex.
- Usage-ledger logical storage is the sum of stored UTF-8 blob byte lengths plus the schema's fixed per-row overhead. Every successful record transaction prunes oldest telemetry rows until the configured byte cap is restored; business chat and workflow databases are never involved.
- The usage ledger is disposable operational evidence, not business state. A lock, storage pressure, schema/exporter error, or shutdown timeout drops telemetry with a sanitized warning and cannot alter chat, workflow, capability-policy, approval, or delivery outcomes.
- The SQLite usage ledger is a single-host implementation. Multi-host aggregation and a frontend analytics dashboard are intentionally outside this phase.
- `ChatJob` owns durable assistant-response transport and browser reconnect. `WorkflowRun` owns durable multi-step business/action execution; a chat job may link to a workflow by `job_id`, but neither replaces or owns the other's lifecycle.
- `WorkflowRun` records what business action should happen. `ActionOutbox` is the durable authorization-to-dispatch boundary for an external mutation. The external provider remains the ultimate source of truth for whether the outside-world mutation happened.
- Workflow schema version 1 uses a dedicated `WORKFLOW_DB_FILE` with WAL, foreign keys, idempotent initialization, owner-scoped run/action/approval/event rows, bounded UTF-8 logical storage, and no migration of the main chat database.
- Before an action runs, the store atomically verifies owner, workflow lease, cancellation, dependencies, and pending state. Only the winning claimant invokes the tool. Results are written under the same execution identity, and stale workers cannot complete actions or workflows after lease loss.
- Every workflow action maps explicitly to a registered capability. Policy evaluates durable owner, execution lease, approval state, and cancellation before the atomic action claim; `WorkflowAction.sensitive` remains compatibility metadata and cannot lower registry risk or approval requirements.
- `email.deliver` is a high-risk, owner-required, idempotent external mutation allowed only from workflow and HTTP lanes. Workflow delivery requires durable approval, a valid lease, uncancelled state, and request-scoped authorization verification; HTTP delivery requires an authenticated owner and request-scoped authorization verification. `EmailDeliveryService` then independently re-verifies the Admin Key and retains draft validation, attachment ownership, and idempotency.
- Approval pauses persist the sanitized plan, completed action IDs, metadata-only draft, approval target, and expiry. The request-scoped Admin Key is validated before a paused workflow is claimed and is never persisted, logged, sent to a model, or reconstructed.
- For external workflow actions, the action claim and outbox intent/claim are committed by one `BEGIN IMMEDIATE` transaction in `.runtime/workflows.db`. Result settlement updates the outbox and workflow action in one transaction, so successfully persisted states cannot contradict each other.
- Outbox recovery preserves the dispatch boundary: `claimed` with no dispatch start returns to `prepared` after lease expiry and requires fresh request-scoped authorization; expired `dispatching` becomes terminal `unknown_external_result`. Startup initializes, recovers, and prunes only; it never dispatches or automatically retries email.
- Exactly-once local claim is not exactly-once external-world effect. SMTP has no provider-side idempotency key, so a crash or transport ambiguity after dispatch begins is deliberately non-retryable and may require manual reconciliation.
- Outbox capacity uses exact SQLite UTF-8/blob byte accounting and prunes only safe terminal history. `prepared`, `claimed`, `dispatching`, and `unknown_external_result` are never deleted to admit work; admission fails before sender invocation when capacity cannot be reclaimed.
- Cancellation is a durable idempotent intent. It blocks new action claims and pending delivery, is observed by the lease heartbeat, and retains terminal history until bounded retention pruning.
- Browser workflow durability automation is deferred because the current UI does not expose a stable workflow ID after approval pause. `test_workflow_store.py` instead serves the authenticated HTTP list/detail/cancel surface and verifies owner concealment, no-store headers, and sanitized DTOs.
- Compound requests that generate/create an image and attach it to an email are recognized before deterministic direct-tool fallback. They resolve the active draft from prompt/history context on the backend; a missing, malformed, or unsupported draft returns a controlled widget-compatible clarification and never generates a standalone image.
- `BUILD_EMAIL_DRAFT` and `IMAGE_GENERATE` are independent actions for a new compound draft. `ATTACH_IMAGE` requires both to succeed. If generation or attachment fails, an existing draft is not replaced; a newly built draft may be returned without the image with explicit failure wording.
- `/chat` emits low-cardinality `[WorkflowRoute]` fields only: candidate, marker presence, active-draft resolution, intent, plan creation, fallback lane, and bounded prompt/history sizes. It never logs prompts, draft contents, addresses, tool arguments, or image data.
- `[CapabilityTrace]` logs only capability, source, decision, reason, registry risk/effect, and workflow/job presence. It excludes arguments, prompts, queries, recipients, credentials, headers, attachment names/URLs, and outputs.
- `/sync_chats` rolls back failed writes, retries only transient SQLite locked/busy failures once, and maps persistent failures to safe categories. `app.database_health` and `python -m app.cli.db_health` provide bounded, non-destructive checks without repair or user-content output.

## Interaction Surfaces

- `static/js/composer_context_tray.js` is the sole drag/drop owner for message text, images, widgets, explicit email handles, external files, and typed prompt context. The selectable text contract is preserved by classifying image targets before the optional hold-G text-grab mode.
- `static/js/ui.js` owns the shared image viewer and image action rail. `static/js/utils.js` installs that rail on hydrated markdown images, while `static/js/app.js` supplies prompt, download, copy, focus, and modal lifecycle actions.
- `static/js/email_draft_contract.js` and `static/js/email_draft.js` keep image/document attachment metadata typed, owner-safe, and byte-free in prompt context. `app/routes/chat.py` serves owner-validated attachment bytes only through the authenticated private media route.
- `static/css/email_draft.css` owns attachment widget presentation. `static/css/product_controls.css` owns shared image actions, viewer layout, and responsive settings modal behavior. The template keeps one image viewer instance and cache-busts the active interaction modules.
- Served-shell browser checks validate the FastAPI HTML/CSS surface; active native drag and context transfer are validated by Playwright against the loaded interaction modules. Browser checks must fail when Chromium cannot launch rather than silently skipping.
