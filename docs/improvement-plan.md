# Improvement Plan

## Baseline

- Repository baseline before the compound-workflow iteration was 264 passed under `python -B -m pytest -q -p no:cacheprovider`.
- The current verification target is the same command with a fresh temporary directory.
- .project_brain/ is generated Chroma runtime state and is not part of source changes; preserve it during recovery.

## Current strengths

- Owner-scoped attachment IDs, size/type validation, bounded PDF/text extraction, and metadata-only frontend persistence.
- NDJSON streaming with job IDs, status updates, heartbeats, cancellation, disconnect handling, and separate inference/tool lanes.
- Deterministic typed email/image workflow planning, explicit approval-only delivery, request-scoped admin-key verification, owner-scoped pending state, and delivery idempotency.
- Durable owner-scoped workflow/action/approval state with cross-worker claims, restart-safe interruption handling, bounded storage, and no automatic side-effect replay.
- A frozen, fail-closed capability policy gateway shared by agent, direct-tool, workflow, HTTP, memory, attachment, image, and email-delivery execution paths.
- Centralized frontend API errors, dialog focus isolation, explicit context drag handles, and reduced-motion support.

## Prioritized work

1. Protect attachment type integrity and make documents follow a document-context path while images follow vision.
2. Keep chat persistence and streaming resilient under quota, reconnect, cancellation, and large-content conditions.
3. Reduce route/facade complexity only after characterization tests cover the existing direct-tool and cloud/local fallback behavior.
4. Add operational evidence: structured job/provider timing, queue saturation visibility, dependency failure tests, and a local recovery runbook.
5. Consolidate legacy frontend modules only after browser-level ownership and cache-busting coverage are established.

## Explicitly deferred

- Provider/network remediation is not inferred from model-list availability; cloud diagnosis requires a separately reproducible provider request failure.
- No deployment, push, merge, release, real email, external account mutation, or destructive data cleanup is included.
## Previously Completed In This Workstream

- Replaced email-context and email-draft-repair one-second persistence polling with state-driven debounced saves and lifecycle flushes.
- Added queue/chat job and attachment-count telemetry with sanitized categories and timings.
- Confirmed .project_brain/ is generated Chroma runtime state and moved it out of Git tracking without deleting local files.

## Completed In This Iteration

- Added a versioned email-draft contract with explicit transient, prompt-context, persistable, and delivery serializers.
- Added multi-attachment and generated-image contract coverage, including byte-free persistence assertions.
- Added controlled /chat characterization tests for NDJSON ordering, document versus visual routing, sanitized provider failure, and owner-scoped attachment rejection.
- Added frontend state mutation APIs and migrated active chat/context/image call sites away from direct collection mutation.
- Added redaction tests for agent and queue telemetry, plus bounded exception categories in provider/tool failure logs.
- Added active frontend Node syntax checks to CI and a local recovery runbook.
- Added a dependency-aware compound workflow above cloud/local routing, including bounded parallel research/image lookup, sequential generated-image attachment, partial failure handling, cancellation, and owner-scoped TTL approval resume.
- Renamed the agent-facing draft tool to `build_email_draft_tool`, retained a compatibility alias, and extracted the shared protected delivery service used by both HTTP and workflow execution.
- Added installed-Chromium browser coverage for editable/superseded email cards, live metadata-only follow-up context, and masked persistence redaction.

## Remaining Concrete Backlog

- A remote CI run has not been observed from this workspace.

- Live OpenRouter, Ollama, SMTP, and Ngrok behavior remains intentionally unexercised by deterministic tests.
- `/chat` route extraction remains deferred; known compound workflows are isolated behind the planner/executor, but legacy direct-tool and fallback branches still share the route facade.
- Browser automation currently covers the email workflow surface, not a complete responsive visual regression matrix.
- Browser-level workflow restart/resume remains deferred until the UI exposes a stable workflow ID; authenticated HTTP recovery is covered in Phase 1.

## Dependencies and Risks

- Provider availability remains an external dependency and must be diagnosed with a reproducible request, not inferred from model metadata.
- The remote Git URL must not contain credential material; rotate and replace any credential-bearing remote outside this implementation pass.

## Reliability Corrections In This Iteration

- Added a pre-direct-tool compound email-media guard, authoritative live-draft context resolution, route diagnostics, and controlled missing/malformed/unsupported draft behavior.
- Hardened independent new-draft image generation and draft construction, explicit attachment-stage failure handling, and no-replacement semantics for existing widgets.
- Added rollback/retry classification for chat synchronization and a non-destructive SQLite health CLI. Live provider, SMTP, and production database behavior remain unverified by design.
## Completed In This Iteration: Composer Context UX

- Replaced whole-card email dragging and competing legacy drop listeners with a single central composer owner and explicit handle/fallback interaction.
- Added source-aware context fingerprints, bounded metadata-only transfer, duplicate pulse, material email-draft updates, invalid/leave/dragend/Escape cleanup, and all composer drop surfaces.
- Added a dedicated responsive email widget stylesheet with attachment chips, accessible labels, preview presentation, mobile touch targets, reduced-motion, and forced-colors support.
- Expanded installed-Chromium coverage to 14 browser tests for handle routing, duplicate/update behavior, non-draggable controls/iframe, metadata redaction, cleanup, and mobile fallback.

## Completed In Phase 1: Durable Workflow State

- Replaced process-local pending approval authority with a dedicated schema-versioned SQLite-WAL workflow store and compatibility facade.
- Added durable run, action, approval, and bounded event records; owner-scoped workflow/action leases; exactly-once action claims; stale-worker rejection; durable cancellation; and safe interruption/unknown-delivery outcomes.
- Added explicit reviewed workflow serialization using the metadata-only email draft contract, request-scoped pre-claim authorization, bounded logical storage, startup-only pruning, and low-cardinality `[WorkflowTrace]` logs.
- Added authenticated private/no-store workflow list/detail/cancel routes without exposing plans, arguments, outputs, recipients, or credentials.
- Added deterministic multi-instance, restart, race, cancellation, expiry, capacity, secret-persistence, and served HTTP recovery tests without real SMTP or provider mutations.

## Completed In Phase 2: Capability Policy Gateway

- Added policy version 1 with immutable capability specifications, structured allow/deny/approval decisions, explicit source/owner rules, immutable reviewed handler bindings, and argument-free telemetry.
- Classified active search, image generation/search/upscale/proxy, memory, attachment, email draft/update/attachment, delivery, and internal workflow response behavior. Active CrewAI tool and workflow action inventories are exhaustively tested.
- Replaced planner `sensitive` authority with registry-derived workflow policy before atomic claims. Fake model approval flags have no effect, and durable cancellation blocks a new delivery after approval without invoking the sender.
- Enforced the same policy at direct-tool and CrewAI handler execution and at HTTP/workflow email delivery while retaining Admin Key verification, owner isolation, SSRF controls, idempotency, leases, and bounded persistence.

## Completed In Phase 3: Observability And Usage Accounting

- Added opt-in OpenTelemetry trace/metric providers with in-memory test injection, bounded shutdown, application context propagation, no console exporter, and sanitized readiness.
- Connected chat, queue, workflow/action, capability, memory, fallback, durable job, LiteLLM, and Ollama boundaries without exporting user content or high-cardinality metric labels.
- Added a dedicated bounded SQLite-WAL usage ledger with pseudonymous owners, per-attempt deduplication, reported token/cost semantics, concurrency coverage, and locked-database fail-open behavior.
- Added authenticated aggregate-only usage summaries for fixed windows. A frontend dashboard, raw-event API, remote telemetry backend, and multi-host ledger remain explicitly deferred.
