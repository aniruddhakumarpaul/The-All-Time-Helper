# Feature Implementation Roadmap

This roadmap keeps new work incremental and reviewable. Do not land a broad feature bundle unless every item has isolated tests or a manual verification path.

## Ground rules

1. Ship one high-risk feature per PR.
2. Prefer additive routes, modules, and UI controls before replacing working behavior.
3. Replace existing code only when the new path is simpler, safer, and covered by tests.
4. Keep `state.js`, `api.js`, `ui.js`, and `app.js` responsibilities separated.
5. Add a rollback note and manual verification steps to every PR.
6. Keep sensitive actions deterministic and user-approved.

## Phase 1 — User-approved actions

### 1. Email draft approval and delivery

Status: first implementation PR.

Goal: backend agents create drafts only; users explicitly approve final delivery through a deterministic route.

Safety requirements:
- Require a valid admin key.
- Use the existing `send_or_simulate_email` helper.
- Preserve `EMAIL_MODE=SIMULATE` behavior.
- Never store admin keys in localStorage/sessionStorage.
- Use idempotency context for approved sends.

Manual verification:
- Generate an email draft.
- Confirm the draft card renders.
- Click Approve & Send.
- Enter wrong admin key and confirm rejection.
- Enter correct admin key and confirm simulated or live success.
- Confirm `simulated_emails.log` is written only in simulate mode.

## Phase 2 — Observability and task state

### 2. Running task panel

Goal: show active inference job ID, model, status, elapsed time, and cancel action.

Scope:
- Frontend-only panel first.
- Backend queue metadata endpoint only if needed.
- No change to inference execution semantics.

### 3. Model health dashboard

Goal: show Ollama availability, configured cloud providers, selected model, and fallback route status.

Scope:
- Add read-only `/api/models/status` endpoint.
- Do not expose raw API keys.

## Phase 3 — Frontend reliability

### 4. NDJSON stream parser extraction

Goal: move streaming parsing out of `app.js` into a small reusable helper.

Scope:
- Preserve current stream protocol.
- Add unit/static tests for status, chunk, heartbeat, and final message handling.

### 5. Browser smoke tests

Goal: Playwright tests for auth screen, chat send, stop, settings, image upload, and email draft rendering.

Scope:
- Start with local mocked/minimal server behavior.
- Do not require external model providers.

## Phase 4 — Memory and attachments

### 6. Memory manager UI

Goal: search, inspect, pin, and delete neural memories.

Scope:
- Add read-only memory search first.
- Add delete/pin only after authorization is clear.

### 7. Attachment library

Goal: reuse, preview, and remove uploaded files.

Scope:
- List recent owner-scoped attachments.
- Reattach previous file to a chat or email draft.
- Keep file deletion explicit and confirmed.

## Phase 5 — Security hardening

### 8. CSP enforcement

Goal: move from documented CSP candidate to active CSP header.

Prerequisites:
- Move inline styles into CSS where practical.
- Smoke-test auth, chat streaming, markdown rendering, image generation, uploads, settings, and command palette.
- Decide whether to self-host third-party browser libraries.

## Non-goals for now

- No full UI redesign.
- No new agent framework rewrite.
- No background autonomous sending.
- No persistent admin authorization.
- No raw credential display in the UI.
