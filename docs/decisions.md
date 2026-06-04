# Decisions

## Chat Sync
- Browser chat state is local-first and uses newest-wins timestamps (`updatedAt`/`updated_at`) when merging with cloud state.
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
- Pasted code/logs that ask for explanation, syntax breakdown, summary, or description are direct chat requests; they must bypass tool/email routing unless the user explicitly asks to send, attach, edit files, run code, or execute another action.
- Runtime theme changes must keep `data-theme` synchronized on both `<html>` and `<body>` because CSS uses ancestor theme selectors and JS visual effects observe the body attribute.
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

This file records the current high-level architectural decisions.

## Current Decisions
- `app.js` is the frontend orchestrator and the only place that should bridge `window.*` exports for the module stack.
- `utils.js` remains a global helper script for legacy markdown and code utilities.
- Generated-image chat rendering is owned by `static/js/utils.js`.
- Neural memory failures must not break chat execution.
- Image-to-email workflows should resolve or generate real image bytes before drafting the email widget.
- The repository should favor markdown source-of-truth docs and code search over vector retrieval for normal development context.
- Repo-local Codex hooks live under `.codex/` and act as lightweight workflow guardrails: load docs-first context, remind before edits, record edit/verification activity, and nudge a narrow verification before final responses after edits.
