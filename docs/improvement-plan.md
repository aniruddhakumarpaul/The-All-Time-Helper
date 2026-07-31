# Improvement Plan

## Baseline

- Repository baseline was 229 passed, 1 failed under python -B -m pytest -q -p no:cacheprovider --basetemp C:\tmp\tah-baseline.
- The existing failure is app/tests/test_interaction_integrity.py::InteractionIntegrityTests::test_mobile_composer_preserves_touch_targets: the active CSS uses semantically equivalent .pill-bar>.action-btn while the source-contract test expects .pill-bar > .action-btn.
- .project_brain has local runtime/index changes in the worktree; those files are not part of this improvement work and must not be discarded.

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
## Completed In This Iteration

- Replaced email-context and email-draft-repair one-second persistence polling with state-driven debounced saves and lifecycle flushes.
- Added queue/chat job and attachment-count telemetry with sanitized categories and timings.
- Confirmed .project_brain/ is generated Chroma runtime state and moved it out of Git tracking without deleting local files.
