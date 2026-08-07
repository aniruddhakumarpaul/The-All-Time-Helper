# Decisions

## Chat Sync
- Browser chat state is local-first and uses newest-wins timestamps (`updatedAt`/`updated_at`) when merging with cloud state.
- Chat timestamps use Unix epoch milliseconds; legacy second-based values are normalized at migration, API, and browser boundaries.
- `static/js/app.js` owns refresh restore end-to-end: it reads local cache, fetches remote chats once, merges once, renders history once, and opens a single active chat once.
- `static/js/ui_restore.js` remains a compatibility no-op, and `static/js/latest_view_guard.js` is no longer part of the active restore path.
- New user messages update the per-user local cache and active-chat ID immediately, before the debounced cloud sync, so refresh cannot reopen an older conversation.
- Chat inference is server-owned after `/chat` returns a job ID: browser disconnects detach only the NDJSON stream; the owner-scoped job registry retains bounded events and the terminal result for refresh recovery. Only explicit Stop, a new follow-up send, or server shutdown ends that job.
- Background history restoration is revision-guarded: starting, opening, or sending in a thread after restore begins must prevent the late merge from replacing the user's chosen view.
- `/sync_chats` is merge-based; it no longer deletes unmentioned chats from a stale client snapshot.
- Chat deletion uses explicit tombstones (`deleted_chat_ids`) so deletes are intentional and can be retried safely.
- Rendered email widgets must update their backing `EMAIL_DRAFT_PAYLOAD:` message whenever fields change, so refresh/export use the current UI state.
- Stream abort handling must preserve completed tool payloads as final results; do not append `[Stopped]` after a complete email widget payload or image markdown.
- Chat export should summarize email widget attachments instead of dumping embedded base64 into markdown files.
- Pasted technical prompts should stay verbatim for model input, while routing uses a separate normalized string and ignores code-like text for attachment inference unless the user explicitly asks to send or attach it.
- User chat bubbles must preserve visible whitespace for pasted prompts, so multiline code remains readable after send without converting user text into bot-style Markdown code blocks.
- Frontend API calls should resolve through an explicit backend base URL, with the served HTML injecting the current request origin and a localhost fallback for local development.
- The startup page loader is lifecycle-driven: it measures real bootstrap elapsed time, updates the progress bar from that elapsed time, and only dismisses after app initialization finishes.
- The intent normalizer must preserve structured technical prompts instead of collapsing them into a single line; only ordinary prose should have whitespace compacted for routing.
- The stop button now cancels the active backend job by job_id, and the chat stream exits early instead of awaiting the full worker future after cancellation.
- The active job ID is persisted separately from chat history in browser storage; reload polls the job endpoint and appends the completed response exactly once, while a follow-up prompt invalidates an in-flight recovery poll before cancelling the old job.
- Pasted code/logs that ask for explanation, syntax breakdown, summary, or description are direct chat requests; they must bypass tool/email routing unless the user explicitly asks to send, attach, edit files, run code, or execute another action.
- Runtime theme changes must keep `data-theme` synchronized on both `<html>` and `<body>` because CSS uses ancestor theme selectors and JS visual effects observe the body attribute.
- Runtime theme changes must also select the matching brand asset: dark mode uses the red `/static/img/logo.png`, while light mode uses the blue `/static/img/logo(2).jpg`; the favicon follows the same mapping.
- Email-template attachment requests using frontend attached-context blocks should produce `EMAIL_DRAFT_PAYLOAD:` directly; raw `send_email_tool` JSON from cloud agents is a recoverable tool-plan leak and must be converted to the email widget payload.
- Page refresh starts the prompt composer fresh: pending prompt text, drag/drop attached contexts, and unsent image attachments are cleared on frontend startup while saved chat history remains persisted.
- Prompt-bar drag/drop contexts are bounded before send to keep multi-context requests model-safe; current submitted prompts are skipped when building backend history context to avoid duplicating attached-context payloads.
- Email image-attachment drafts support a backward-compatible `attachments` array for multi-image requests while preserving the legacy first-attachment fields used by older widgets and send paths.
- Frontend chat persistence must never block user actions: if `localStorage` hits quota, the app falls back to a more aggressively redacted snapshot and keeps the send flow live.
- Email preview rendering strips executable script tags before writing into the sandboxed iframe.
- User-selected image uploads use a temporary backend attachment store and pass file IDs through chat/email flows; base64 JSON remains accepted only for legacy history, generated assets, and fallback paths.
- Email drafts may contain ID-only attachment metadata, but SMTP/send simulation resolves those IDs server-side under the authenticated owner before MIME assembly.
- Email preview iframes keep scripts disabled and use `srcdoc` without `allow-same-origin`; do not add `allow-scripts` for preview rendering.
- Editing a user prompt is a frontend-owned rewrite: truncate local chat state at the edited message, persist that boundary immediately, then resubmit the edited prompt through the normal `/chat` backend route.
- Dragging an email widget into the prompt context bar stores structured draft context and serializes it as `EMAIL_DRAFT_CONTEXT` only at send time, so the next email widget can reuse existing recipient, subject, body, tone, and attachment metadata without clipping JSON as ordinary text.
- Dragging an email widget into context must read the live rendered widget fields and attachment metadata from the widget DOM before serializing `EMAIL_DRAFT_CONTEXT`, so saved `EMAIL_DRAFT_PAYLOAD` text cannot reintroduce stale, fragmented, or escaped body content.
- Email widget drag producers must emit `application/x-helper-email-draft` plus a `text/plain` `EMAIL_DRAFT_CONTEXT:` fallback so the mascot and prompt context handlers can treat email drafts as structured state instead of plain text.
- Dropping an email draft onto the mascot must attach the draft into prompt-context state locally when the drag payload is `application/x-helper-email-draft` or `EMAIL_DRAFT_CONTEXT:`; only ordinary text should continue to use `/retrieve_context`.
- `/retrieve_context` must short-circuit `EMAIL_DRAFT_CONTEXT:` and `EMAIL_DRAFT_PAYLOAD:` markers into a direct email-draft response before semantic memory lookup so draft payloads do not hit `query_memory()` or neural explanation code.
- User-visible user-message bubbles and edit fields must render `display_c` or sanitized visible text only; raw `EMAIL_DRAFT_CONTEXT:` and `EMAIL_DRAFT_PAYLOAD:` markers remain internal API/history payloads and must not leak back into the chat UI on reload or edit.
- User-visible chat bubbles should render attached email drafts as full readable blocks plus image filenames, while `apiPrompt` keeps the serialized attachment payloads for backend processing.
- User-visible attachment cards should stay compact in the chat bubble, and clicking a card should open a full-context sheet rendered from the internal prompt payload, without exposing raw internal markers.
- Long email bodies should be offloaded at send time into `email-body.txt` or `email-body.md` attachments, while the inline email body becomes a short note and existing attachments remain intact.
- Natural replies to the image/text/summary email-attachment clarification, such as "a summary of the relevant text with the image attached", must resolve back into the deterministic email-draft flow and reuse existing attachments instead of falling through to visual image generation.
- Summary replies for attached email widgets must use the previously captured draft body from `EMAIL_DRAFT_CONTEXT` / `EMAIL_DRAFT_PAYLOAD` before any clarification text, so the summary does not accidentally summarize the user's reply sentence.
- The backend API base URL is injected through a root HTML data attribute and read by the JS client at runtime, avoiding executable template expressions inside the inline script block so the template stays parser-friendly in the IDE.
- Bot markdown HTML is untrusted frontend input: render it through `marked`, sanitize it with DOMPurify before `innerHTML`, block unsafe URL protocols, and hydrate trusted code/image controls with DOM event listeners instead of inline handlers.
- Email body HTML is untrusted backend output: preview and SMTP send must share `_build_html_body`, escape user text before markdown transforms, and only emit allowlisted formatting with safe URL protocols.
- SQLite schema changes use explicit versioned migrations. Legacy `users.admin_authorized` values are cleared and ignored at runtime; authorization is request-scoped only.
- LLM tools may build email drafts but cannot send SMTP messages. The deterministic delivery helper validates inputs and uses the inference job ID as its idempotency key.
- Active frontend controls use module-bound listeners instead of inline event attributes. The CSP candidate remains documentation-only until browser smoke verification is complete.
- The sign-out hover effect uses visible but fluid liquid-fire movement, soft ember flow, and a compact close-to-control glow. Dark mode uses near-black hover text against the fire surface; its animated layers remain active through the hover return transition, then stop after the button settles; it must honor `prefers-reduced-motion` and avoid layout-affecting animation.
- The template owns the canonical `email_draft.js?v=4` early load and marks it with `data-helper-extension="email-draft-core"` so bootstrap does not inject a second copy; drag capture must ignore interactive controls inside `.email-draft-card` so edit, use, and send buttons remain clickable.
- Ngrok lifecycle is owned by the direct local launcher (`python -m app.main`), not the FastAPI factory or production ASGI lifespan.
- The direct launcher keeps Uvicorn reload disabled by default for stable Windows multiprocessing; set HELPER_RELOAD=true when development hot reload is explicitly needed.

## Product Acceptance Boundary
- Every visible control has one explicit purpose and one active event owner. In particular, Preferences binds only to `#open-settings-btn`; generic `.set-btn` selectors must never capture Active Tasks or System Status.
- Toggle preference rows delegate to their single switch so the full visual target is clickable without creating a second state owner or double-firing direct switch clicks.
- Native buttons, form labels, accessible names, and `role="switch"` semantics are part of the active shell contract. Icon-only controls must have an accessible name.
- Route, theme, attachment, and dynamic removal controls use native buttons. Choice menus support arrow/Home/End navigation and Escape focus restoration; Escape in the composer must never discard a draft or start a new chat.
- Modal surfaces opt into `data-helper-dialog`. `dialog_manager.js` keeps only the highest visible modal exposed, makes background siblings inert, traps Tab within that modal, and restores focus on close.
- Preferences persist under `helper_preferences_v1`; response style is mirrored to the legacy `helper_response_style_v1` key and sent from `state.responseStyle` as `adaptive`, `concise`, `deep`, or `creative`.
- Auth, upload, export, context, task, and status failures use non-blocking in-app feedback. Active frontend code must not use blocking `alert()`.
- Stopped and failed generations are stored as durable assistant messages so refresh does not erase the outcome.
- Active REST controls treat non-2xx status as failure. Cancellation must not refresh away a failed result or imply success without a confirmed success payload.
- The authenticated System Status panel is user-facing readiness, not an administrator diagnostics dump. It must remain sanitized even though the compatibility route is `/admin/status`.
- Expensive chat/context inputs are bounded at the Pydantic boundary, and backend exception text is logged rather than returned or streamed verbatim.
- `app/tests/test_product_acceptance.py` is the deterministic regression gate for these contracts.
## Restored Legacy Interface
- The active visual shell is the original templates/index.html plus static/css/style_v3.css, including its centered greeting, glass sidebar, floating composer, Outfit typography, logo gradients, and particle canvas.
- static/css/flagship.css and static/js/experience.js are not active entry points. The rejected redesign is retained only under .runtime/ui_redesign_backup_20260717_0430 for reversible recovery.
- Current runtime behavior remains connected to the legacy shell: helper-auto stays the default route, the request-origin API base remains injected, multi-file attachments remain available, and current chat-sync/backend contracts remain unchanged.
- Legacy particle motion is restored intentionally because it is part of the requested old interface.
- Click feedback is owned by the idempotent `motion_enhancements.js` layer: controls react on pointer-down, cancel on drag, release with a short spring, and non-native controls support keyboard activation. The layer must not delay or replace existing click handlers and must respect reduced-motion preferences.
- Message text is a selection surface, not a default native drag source. Context dragging uses the visible message handle; hold-`G` may temporarily enable whole-bubble grab mode for power users.
- Ctrl+K renders one scrollable ARIA listbox whose keyboard index always maps to a rendered option. Its header, results viewport, and footer cannot overlap, and Ctrl+Shift shortcuts must not toggle it.
- Hover may emphasize message, history, and code actions, but those controls keep a visible resting state and core message geometry does not move on hover so element inspection remains stable.
- Helper Auto treats credentials and runtime health separately. A pre-content cloud failure opens a short circuit and retries locally once; explicit cloud choices remain explicit and sanitized status APIs expose only availability and retry timing.
- LiteLLM bundled metadata and CrewAI/OTel telemetry defaults are configured before dependency import so local startup does not perform a blocking metadata fetch; explicit environment overrides remain authoritative.
- DDGS text and image searches, plus dependency HTTP stacks, use the bundled `certifi` CA file rather than the Windows user certificate store; `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE` are set only when not explicitly configured, and TLS verification remains enabled.
- Text search tries stable providers in a fixed `Brave -> Yahoo -> Yandex -> Bing` order and stops on the first non-empty response; one provider outage must not disable research.
- Explicit web/image actions bypass CrewAI on local and cloud routes, and CrewAI storage defaults to `.runtime/crewai`; deterministic tools must not fail because a dependency tries to open a user-profile SQLite database.
- The default OpenRouter chain must use models verified against the account privacy policy. Anonymous search providers remain first; the cited OpenRouter web-search server tool is a bounded last resort and only accepted when URL annotations are present.
- Model controls describe active capabilities rather than obsolete provider IDs; compatibility IDs may remain backend-only, but stale Nemotron/North labels do not belong in the visible route menu.
- Current and recent attachment IDs are resolved under the authenticated owner before vision inference; an expired or cross-owner reference must never become a filesystem path or silent cross-user lookup.
- A transient neural-memory query failure opens a bounded retry circuit and must be reflected by the status dashboard; it cannot permanently disable memory until process restart.
- A protected API `401` clears stale browser identity state and returns to authentication once, instead of leaving a visually signed-in but unusable workspace.
- Supplemental classic scripts are inserted with ordered execution, and the canonical template must not contain controls that JavaScript removes after load; stable DOM identity is required for clicks, accessibility, and element inspection.
- Startup diagnostics distinguish configured credentials from verified provider availability and never print credential fragments. The standalone system audit targets the active OpenRouter, Ollama, draft, and memory pipelines rather than retired Groq assumptions.
- Creative-image routing uses one shared actionable-intent contract across classifier, direct execution, cloud execution, and context assembly. Tool-capability discussion cannot trigger generation, while an unresolved visual request may absorb short creative-latitude follow-ups exactly once.
- Current-turn chat history is sent once. The browser excludes the just-added message, and cloud/local message builders defensively remove one duplicate from legacy clients.
- Vision-capable OpenRouter/Gemma routes receive owner-validated images as native multimodal message parts. Simple local visual questions use cached Moondream perception and return that answer directly; no route should run a long vision description plus an unnecessary second model pass.
- Vision and general tool telemetry must remain argument-free. Log model/tool identifiers, outcomes, byte counts, and durations only; never log raw base64, attachment-owner paths, recipient addresses, or full image-generation URLs.
- Ordinary code generation/debugging is direct model work. CrewAI is reserved for actual external actions or coordinated multi-tool workflows, not as an automatic wrapper around every code-related prompt.
- Mobile composer action targets must remain at least 36 px wide and 44 px high without horizontal overflow at 320 px; the send target remains 44 px square.

This file records the current high-level architectural decisions.

## Current Decisions
- `app.js` is the frontend orchestrator and the only place that should bridge `window.*` exports for the module stack.
- `utils.js` remains a global helper script for legacy markdown and code utilities.
- Generated-image chat rendering is owned by `static/js/utils.js`.
- Neural memory failures must not break chat execution.
- Image-to-email workflows should resolve or generate real image bytes before drafting the email widget.
- The repository should favor markdown source-of-truth docs and code search over vector retrieval for normal development context.
- Repo-local Codex hooks live under `.codex/` and act as lightweight workflow guardrails: load docs-first context, remind before edits, record edit/verification activity, and nudge a narrow verification before final responses after edits.
- Keep local/cloud model inference serialized, but route only proven model-free generation/search actions through a separate bounded tool lane. The route must precompute a forced direct-tool intent; uncertain or potentially model-backed workflows must remain on the inference lane.
- Cloud degradation is represented by a short-lived sanitized reason enum rather than a generic boolean or raw exception. Status UI labels route counts as configured, not available, and Auto continues to recover locally.
- Generated-image enhancement is additive: never publish or log the prompt-bearing source URL from the upscaler, and fall back once to the validated original image when local enhancement fails.
- CrewAI tracing is always noninteractive in the request path.
- Attachment IDs are validated as server-generated hexadecimal identifiers, and resolved metadata is authoritative over client-supplied type/filename fields. This prevents path traversal and prevents document uploads from being misclassified as images.
- Chat persistence strips transient image base64, attachment bytes, and email draft attachment payloads while retaining owner-scoped IDs and metadata. Live previews remain in memory only.
- Email-context cache persistence is event-driven: state subscriptions schedule one debounced local-cache write, with beforeunload and hidden-document flushes as final safeguards. The repair layer follows the same contract and does not poll every second.
- Queue and chat telemetry records bounded job/lane/timing/attachment counts and sanitized state categories only; prompts, responses, paths, recipients, credentials, and provider exception text remain excluded.

## 2026-07-31 Hardening Decisions

- Email drafts use schema version 1. Transient delivery serializers may carry attachment content only inside the request, while prompt-context and persistence serializers carry metadata and owner-scoped IDs only.
- Active frontend collections are mutated through named state APIs (appendMessage, updateChat, replaceAttachedContexts, and related methods) so subscribers and persistence hooks observe every meaningful mutation.
- /chat characterization tests preserve the existing NDJSON order and direct-tool/cloud fallback behavior before any route decomposition; extraction is deferred until the remaining branches have equivalent coverage.
- Queue and agent telemetry records categories, identifiers, counts, and timings only. Raw prompts, responses, recipients, paths, credentials, tool arguments, and exception messages are excluded.

- Email draft compatibility is explicit at the Python and browser boundaries: missing or schema version 0 payloads migrate to v1, malformed versions return `invalid_email_draft_version`, and future versions return `unsupported_email_draft_version`. Fixture-driven tests compare canonical, transient, prompt-context, persistence, and delivery serializers exactly across Python and JavaScript.
- The browser email workflow is covered with the installed Chromium runtime using the active draft contract, state, card-rendering, and prompt-context scripts. The test verifies live field editing, `outerHTML` form-value preservation, attachment metadata retention, context redaction, and removal.
- `/chat` characterization preserves NDJSON ordering, document-versus-visual routing, deterministic tool-lane selection, owner-scoped attachment errors, sanitized provider failures, request-scoped admin authorization, and disconnect cancellation. InferenceQueue tests cover lane isolation, atomic saturation, duplicate IDs, owner cancellation, timeout, worker failure, invalid configuration, and repeated shutdown.
- Frontend parser validity is a responsive-shell prerequisite: the attachment upload promise chain in static/js/app.js must close both its .finally() callback and the outer state setter; a syntax error there prevents all DOM event binding, so node --check and a browser app-bridge smoke check are required after orchestrator edits.
- Responsive composer resizing must restore the measured inline prompt height after temporarily measuring with auto; leaving it at auto when the target height is unchanged collapses multiline input back to the flex row height.

## 2026-08-03 Compound Workflow Decisions

- Known compound email workflows are classified and planned deterministically in `workflow_orchestrator.py` before cloud/local model selection. The typed plan, not CrewAI delegation, enforces dependencies, bounded parallelism, sensitive-node ordering, cancellation, and final result assembly.
- Draft, update, web research, image search/generation, and attachment actions are non-sensitive. Only `DELIVER_EMAIL` may request an Admin Key; the candidate remains request-scoped and never enters model prompts, chat history, persistent state, pending workflow state, or telemetry.
- Pending delivery plans are owner-scoped, TTL-bounded, claimable once, and retain metadata/owner-approved IDs rather than raw attachment bytes. Invalid authorization releases the claim without rebuilding the draft; success completes the plan.
- Existing/reference/real media uses image search, while explicitly generated creative media uses image generation. Image results normalize to bounded URL/ID metadata and are not downloaded until the existing protected delivery boundary.
- `build_email_draft_tool` is the canonical non-delivering tool. `send_email_tool` is a compatibility alias, and actual delivery is centralized in `EmailDeliveryService` for both the HTTP route and workflow executor.
- Draft tool fast-exit remains a single-tool compatibility behavior only. Compound workflows execute pure draft/update/attach nodes after prerequisite actions, preventing a terminal draft from aborting research or image work.
- Independent web and image searches may overlap inside one existing tool-lane job with at most two workers. Dependent updates and attachments wait for completed prerequisites; no action resubmits into the inference queue.
- The browser keeps the live draft transient, emits metadata-only follow-up context, supersedes older cards, redacts masked messages before persistence, and clears active draft state before changing conversations so one chat cannot inherit another chat's draft.
- Delivery results and workflow telemetry are sanitized. They may expose action type, state, duration, failure category, mode, and request ID, but not recipients, keys, prompts, tool arguments, raw provider errors, or attachment content.
## 2026-08-03 Image Attachment Failure Decisions

- Workflow action outcomes are distinct: only `COMPLETED` satisfies required dependencies; `FAILED`, `BLOCKED`, `CANCELLED`, and `PAUSED` are settled outcomes but never successful prerequisites. Optional dependencies may be settled with failure without blocking the dependent action.
- A failed or invalid generated-image action blocks `attach_image`, emits controlled failure prose without a new `EMAIL_DRAFT_PAYLOAD:`, and leaves the existing widget unchanged. The executor never calls the attachment step with a missing image result.
- The Python and browser email contracts discard the exact empty placeholder (`attachment_content=null`, `attachment.bin`, `application/octet-stream`, and no attachment entries) while preserving meaningful ID, URL, MIME, filename, availability, size, and checksum metadata.
- Generated image descriptions are assembled from bounded, sanitized visual direction, topic, draft subject/body/attachment description, and the latest user request. Recipient addresses, keys, binary data, and full tool arguments are excluded from logs and user-facing status.
- Generated attachments retain existing PDFs and images, suppress duplicates, and populate the legacy primary fields with the generated image without reordering the attachment collection.

## 2026-08-03 Composer Context Decisions

- The composer context tray is the single frontend drag/drop owner. Legacy email context and repair modules remain compatibility/cache layers and delegate to window.addComposerContext when the owner is installed.
- Email draft widgets are deliberately non-draggable containers. The handle is a keyboard-labelled button with draggable=true; fields, the preview iframe, actions, and normal text selection remain independent controls.
- Prompt context transfer is normalized to bounded metadata, uses a stable source-aware fingerprint, and updates an existing email chip when the same live card changes. Duplicate drops pulse the existing chip without duplicating state.
- The context tray stays in normal composer flow and advertises all valid targets with copy semantics. Escape, dragend, invalid drops, leave depth, blur, and successful drops clear transient target state.
- Widget presentation is separated into static/css/email_draft.css for responsive, touch-sized, reduced-motion, forced-colors, labelled-field, attachment-chip, and sandbox-preview behavior.

## 2026-08-04 Image, Attachment, And Settings Interaction Decisions

- Native image dragging is handled before the selectable message-text guard. HTTP(S) image sources may carry the internal composer MIME plus `text/uri-list`, `text/plain`, and browser-normalized `DownloadURL`; data, blob, local filesystem, credential, and binary payloads never enter native transfer data.
- Image click actions use the existing single image viewer. The action rail is pointer- and keyboard-accessible, distinguishes click from movement with a small threshold, and exposes only safe download/copy actions.
- Attachment chips are semantic controls. Images preview through the shared viewer, documents attach as typed document context, unavailable owner-scoped IDs are visible but disabled, and generated prompt-like filenames are shortened at the display/normalization boundary.
- The composer normalizer preserves `document` as a first-class context kind. Context cards use trusted inline SVG icons and text status labels rather than single-letter or decorative status dots.
- The attachment download route resolves metadata under the authenticated owner and returns private no-store media. Frontend attachment actions store IDs and bounded metadata, not file bytes or raw base64.
- Settings layout is intentionally visible at normal desktop sizes, becomes a bottom-sheet surface on narrow screens, and gains bounded internal scrolling only for short or zoomed viewports. Escape and explicit close remain available independently of outside-click configuration.
- Browser coverage separates real FastAPI shell geometry from native Playwright drag behavior. The shell test verifies the served route and single viewer/composer surfaces; the interaction test verifies native transfer types and safe URL exclusion.
## 2026-08-06 Interaction Validation Decisions

- Prompt and persistence serializers remove transient attachment URLs as well as bytes; delivery-only serializers retain the resolved URL needed for the protected send boundary.
- Generated and uploaded image actions resolve through one metadata contract after deferred hydration, proxy/upscale replacement, and image load. Blob previews can download when safe but never expose Copy Link.
- Use document in prompt transfers bounded owner-scoped attachment references (id, name, type, size) through the chat request’s attachments collection; it never transfers document bytes or preview URLs.
- The settings modal explicitly overrides the legacy flex .set-row rule with a contained grid, a mobile bottom-sheet layout, and short-viewport scrolling. The served-shell test checks 1280x900, 1024x768, 412x915, 390x844, 360x800, and 390x560.
- The legacy app-level upscale entry point delegates to the authoritative metadata-aware poller; enhancement refreshes image modal/download/context metadata while retaining the wrapper original URL only as fallback.
- The Sign Out control occupies a dedicated full-width final row in the preferences card, preserving the long-tab action hierarchy while keeping the animated treatment intact.

## 2026-08-06 Durable Chat Job Decisions

- Chat execution is server-owned and no longer tied to the lifetime of an NDJSON HTTP stream. POST /chat/jobs creates work, GET /chat/jobs/{id}/events?after=N subscribes with an incremental cursor, and the legacy POST /chat stream remains a compatibility wrapper over the same durable job.
- The documented deployment is local/single-host Uvicorn with a writable filesystem and no configured Redis/Valkey/PostgreSQL service. SQLite WAL is therefore the justified default shared backend. The registry keeps an explicit in-memory backend for isolated tests; selecting Redis/Valkey fails clearly rather than silently reverting to process memory.
- Job rows and events are owner-scoped and TTL-bounded. Event count, event bytes, terminal content, retained terminal jobs, and aggregate storage are bounded. Terminal events are compacted, while the authoritative bounded terminal content is returned through snapshot/recovery and normalized into the final NDJSON event.
- Cancellation is a durable intent. The request worker cancels its local queue immediately when possible; every worker also polls the store and sets its local abort event, so a cancel handled by another worker remains effective. Follow-up prompts cancel only the active job for their chat.
- SQLite finalization uses a write transaction and re-checks the persisted cancellation flag, preventing a cross-worker cancel from being overwritten by a racing completion.
- The browser stores a per-account collection of active jobs keyed by chat, including the last sequence cursor. It migrates the old single helper_active_chat_job_v1 record, uses storage/BroadcastChannel notifications across tabs, and removes a job only after terminal recovery or a confirmed not-found response. Final events replace text rather than append it.
- Rollback is controlled by setting CHAT_JOB_BACKEND=memory for isolated development, or by reverting the route/client protocol as one release unit. Existing POST /chat consumers continue to receive the legacy leading job_id event; new clients use the create-then-events contract.

## 2026-08-07 Chat Job Concurrency And Recovery Decisions

- Every durable execution claims an owner-scoped `execution_id` lease and renews `lease_expires_at`/`heartbeat_at` while model work runs. A process restart cannot resume the old task: the first later snapshot/claim/prune observes an expired lease, writes the exact safe interruption result, clears execution ownership, and never replays the prompt, history, workflow payload, or admin key.
- SQLite state transitions for publish, cancellation intent, completion, and failure use `BEGIN IMMEDIATE` and re-check the persisted row inside the transaction. Terminalization is idempotent; stale execution IDs cannot append events or replace a terminal result. Activity refreshes `updated_at` and `expires_at`, while claimed jobs are not deleted as ordinary expired rows.
- `CHAT_JOB_MAX_EVENT_STORAGE_BYTES` bounds retained per-job event bytes by removing oldest non-final events first; the terminal event is preserved. `CHAT_JOB_MAX_CONTENT_BYTES` bounds terminal content by UTF-8 bytes. Aggregate accounting uses stored UTF-8 byte counters rather than SQLite character length, prunes expired/old terminal rows, and rejects new jobs with a controlled capacity error when active work still fills the budget.
- Browser recovery records use independent `helper_active_chat_job_v3:<account>:<chat>` localStorage keys with bounded TTL, deterministic same-record timestamp/writer conflict resolution, storage/BroadcastChannel notifications, and sign-out cleanup. Follow-up and Stop resolve the active record for the current chat; `state.activeJobId` is not a cross-chat cancellation authority.
- The queue remains a single serialized local inference lane. Durable jobs may exist for multiple chats, but execution ordering is governed by `InferenceQueue`; browser tests must verify isolation and cancellation against job IDs rather than imply parallel GPU execution.
