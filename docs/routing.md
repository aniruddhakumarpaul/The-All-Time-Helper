# Routing

Routing is designed to prefer the smallest reliable execution path.

## Helper Auto
- `helper-auto` is the default frontend and API route.
- It resolves to `agentic-pro` only when valid OpenRouter credentials are configured and the provider circuit is healthy; otherwise it resolves to `gemma4:e2b`.
- The default free OpenRouter chain uses privacy-compatible Gemma 4 models (`26b` primary, `31b` fallback). Provider-policy or capacity failures advance through the chain before Auto falls back locally.
- If an Auto cloud request fails before emitting content, the request retries once through an appropriate local model and opens a short provider circuit so following Auto requests avoid repeated cloud timeouts.
- The local recovery path cannot bounce back to the same failing cloud route. Explicit model choices bypass Auto recovery and preserve the user's selected route.
- `/status` reports local availability, cloud configuration, current cloud availability/degradation, a sanitized state (`rate_limited`, `network_unavailable`, `authentication_failed`, `timed_out`, or `provider_unavailable`), retry timing, the resulting runtime mode, and stable capability names. It must not expose credential values or raw provider errors.

## Intent Levels
- `direct`: no tool execution required.
- `single`: one tool or a small deterministic workflow.
- `swarm`: multi-step or multi-tool task requiring the agent hierarchy.
- Deterministic heuristics run before LLM classification for exact-output replies and explicit workflow-sensitive intents: `DRAFT_EMAIL`, `UPDATE_EMAIL_DRAFT`, `SEARCH_WEB`, `SEARCH_IMAGE`, `GENERATE_IMAGE`, `ATTACH_TO_DRAFT`, `REQUEST_EMAIL_APPROVAL`, `DELIVER_EMAIL`, and `GENERAL_RESPONSE`. Ambiguous safe email language defaults to drafting, never delivery.
- Explicit search and image actions execute their deterministic tool directly on local and cloud routes; CrewAI remains reserved for workflows that need agent reasoning or coordination.
- Visual generation intent is conversation-aware: creative-latitude replies such as `anything you like` and `produce one` inherit the most recent unresolved visual request and execute exactly one generation call.
- Questions or debugging statements about tool availability are direct conversation, not tool actions. An explicit artifact request can still name the tool it wants used.
- Code writing, debugging, refactoring, and explanation are direct model work because no code-execution/filesystem tool exists in the product runtime; external research remains a search action.
- The browser omits the just-submitted message from `history`, and backend message builders also remove one matching current turn for older clients.
- Every decorated tool emits low-cardinality `ToolTrace` outcome and duration telemetry without logging tool arguments.
- Proven model-free image generation, visual continuation, explicit web/image search, and typed known compound email workflows use the bounded tool lane, so they do not wait behind serialized GPU inference. Unclassified email, attached-image analysis, persona, capability-discussion, and ambiguous requests stay on the inference lane. A compound workflow executes inside one lane job and never submits nested queue work.

## Important Rules
- Deterministic image-to-email workflows are planned before cloud/local selection, so Helper Auto, explicit cloud, and local routes preserve the same intent, action ordering, approval boundary, draft contract, and attachment behavior.
- A plan declares action IDs, typed action kinds, arguments, dependencies, parallel eligibility, sensitivity, terminal state, active draft, owner, approval state, and TTL. Web and image search may overlap; draft update waits for both, while generation must complete before attachment.
- Active workflow context resolves the current structured draft marker first and then the latest valid historical marker. It preserves live frontend edits and existing attachments, and returns controlled errors for malformed or unsupported draft versions instead of parsing arbitrary prose.
- Reference, real, actual, and existing-image requests use image search. Explicit generate/create/draw/paint/render image requests use generation; a request to create an email with a reference image still uses image search.
- Delivery without a request-scoped key pauses only the sensitive node in an owner-scoped TTL store. A masked key resumes that same plan exactly once; invalid keys leave it resumable, and keys never enter history, model prompts, or persistent state.
- Cancellation stops new action scheduling and preserves the NDJSON cancellation contract. Independent action failure is isolated: valid research or image output may still update the draft safely.
- Visual follow-ups inherit the unresolved visual task when history shows a continuing image request.
- Email-template edit prompts should update the current draft and return `EMAIL_DRAFT_PAYLOAD:` instead of raw tool-plan JSON.
- Hardened results recover validated unmarked email JSON from cloud responses into `EMAIL_DRAFT_PAYLOAD:` before the cloud guard; unrelated cloud JSON/tool plans still pass through unchanged.
- One-word answers to an attachment clarification (`image`, `text`, `both`, `summary`) inherit the pending email attachment request and must resolve deterministically instead of going to normal chat.
- Context switches to search, code, or factual questions should not inherit visual state.
- Plain-text requests that provide a recipient, body, and optional subject use the deterministic email draft path, including natural separators such as `subject ... and body ...`; they do not initialize the agent swarm.
- Explicit email/mail requests without a detected recipient return a blank editable `EMAIL_DRAFT_PAYLOAD:` widget; recipient validation remains in the send/delivery path.
- CrewAI runtime storage is rooted at `.runtime/crewai` by default so local agents never depend on a user-profile database path; an explicit `CREWAI_STORAGE_DIR` override remains authoritative.
- CrewAI tracing is explicitly disabled in environment setup and every Crew constructor so request workers never pause for an interactive trace-consent prompt.
- `build_email_draft_tool` creates a validated `EMAIL_DRAFT_PAYLOAD:` and never performs SMTP delivery. `send_email_tool` is a deprecated compatibility alias to the draft builder. Approved delivery uses `EmailDeliveryService` with request-scoped key verification, recipient/attachment validation, owner-scoped attachment resolution, sanitized results, and request/workflow idempotency.
- A CrewAI draft fast-exit is valid only when draft construction is the final node. Compound workflows call pure draft/update functions after required search or generation dependencies finish.
- Direct and agentic routes share `response_policy.py`. Response shape may be `adaptive`, `concise`, `deep`, or `creative`; English-only, supplied-context personalization, and exact one-word constraints are layered on top.
- The assistant must never claim an external action or tool result succeeded without confirmation. Missing information should trigger one focused question only when it materially changes the result.
## Product Boundary
- `static/js/api.js` converts HTTP failures, FastAPI `detail` payloads, malformed JSON, and network failures into one frontend error shape. Active controls should not parse transport errors independently unless they stream.
- Invalid uploads and missing/cross-owner task cancellations return real `4xx` responses. Unexpected sync, memory, and chat-start failures return generic `5xx` responses while detailed exceptions remain server-side.
- Chat requests are bounded to 100,000 prompt characters, 200 history items, and 6 attachments. Related-context requests accept 1-100,000 characters and 1-10 results.
- `/admin/status` remains the compatibility route for the authenticated System Status panel. It exposes user-relevant readiness booleans and counts only; filesystem paths, provider URLs, tunnel URLs, environment names, raw exceptions, configured model IDs, and security settings are forbidden.
- Uploaded TXT, Markdown, and PDF files remain on the document-context path. The frontend displays them as file cards and does not force a vision model; only image MIME types use visual analysis.

## Compound Email-Media Guard
- A request containing generation language, an image, an attach/include action, and an email/draft surface is a compound email-media workflow. It must not enter `_try_direct_tool_execution` or produce a standalone image when no draft can be resolved.
- Active draft resolution is authoritative in this order: live visible `.email-draft-card` state, draft registry, global active draft, then explicit attached prompt contexts. The browser injects only bounded metadata; the backend validates the marker and owner context before planning.
- Valid prompt markers, historical markers, and frontend-injected active context all route to one `GENERATE_IMAGE` plus `ATTACH_IMAGE` workflow. Missing, malformed, and unsupported contexts use controlled clarification/general-response plans and do not call image generation.
- For a new draft, draft construction and image generation run independently, then attachment waits for both. For an existing draft, failed media never emits a replacement draft marker.

## Chat Sync Diagnostics
- `/sync_chats` rolls back on all failures and retries only `database_locked`. It returns generic user-facing errors while logs retain only bounded categories such as `database_corrupt`, `database_schema_error`, `database_disk_full`, and `database_unknown`.
## Composer Context Routing

- Composer context drag/drop is a frontend routing boundary, not a second chat request: composer_context_tray.js owns dragstart, dragenter/over/leave, drop, dragend, invalid-payload cleanup, and Escape cancellation.
- Email drafts enter the composer only from the explicit context-drag-handle or the Use in prompt fallback. Whole-card dragging, field dragging, preview dragging, and interactive control drags are rejected.
- Transfers use the application/x-helper-composer-context MIME with bounded metadata and text/plain compatibility text. Attachment content, bytes, data URIs, and internal admin fields are removed before persistence or transfer.
- Context fingerprints suppress duplicate drops. An email card identity updates its existing chip when its subject/body/tone or attachment metadata changes rather than creating a second chip.
- Valid drop surfaces are the prompt, pill bar, input wrapper, and context tray. All surfaces use copy semantics, stable drag-depth cleanup, prompt focus after acceptance, and no state mutation for invalid payloads.
