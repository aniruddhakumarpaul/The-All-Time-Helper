# Improvement Plan

## Baseline

- Repository baseline before this iteration was 236 passed under python -B -m pytest -q -p no:cacheprovider --basetemp C:\tmp\tah-pytest.
- The current verification target is the same command with a fresh temporary directory.
- .project_brain/ is generated Chroma runtime state and is not part of source changes; preserve it during recovery.

## Current strengths

- Owner-scoped attachment IDs, size/type validation, bounded PDF/text extraction, and metadata-only frontend persistence.
- NDJSON streaming with job IDs, status updates, heartbeats, cancellation, disconnect handling, and separate inference/tool lanes.
- Deterministic email drafting, explicit approval delivery, admin-key verification, and delivery idempotency.
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

## Remaining Concrete Backlog

- A remote CI run has not been observed from this workspace.
- Browser automation is not configured; current frontend coverage is source/runtime checks and Node syntax validation.
- Live OpenRouter, Ollama, SMTP, and Ngrok behavior remains intentionally unexercised by deterministic tests.
- /chat route extraction remains deferred until characterization coverage is expanded around all legacy direct-tool and cloud/local fallback branches.

## Dependencies and Risks

- Provider availability remains an external dependency and must be diagnosed with a reproducible request, not inferred from model metadata.
- The remote Git URL must not contain credential material; rotate and replace any credential-bearing remote outside this implementation pass.
