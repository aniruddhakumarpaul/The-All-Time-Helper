# Local Recovery Runbook

## Safe baseline

- Stop the local process before changing runtime state.
- Keep `.env` and provider credentials local; never paste them into issues or chat.
- Run `python -B -m pytest -q -p no:cacheprovider --basetemp C:\tmp\tah-pytest` before and after recovery.
- Start locally with `python -B -m app.main`; Ngrok is opt-in through the launcher and is not required for local checks.

## Runtime state

- `.project_brain/` is generated Chroma state. Preserve it first; if it is corrupted, stop the app, move the directory to a timestamped backup outside Git, then restart so memory can rebuild. Do not delete it as a first step.
- Expired attachment IDs are expected to fail owner-scoped resolution. Re-upload the file rather than editing IDs or filesystem paths in browser storage.
- Queue saturation is backpressure, not data loss. Stop duplicate local processes, wait for active jobs to drain, then retry. Use the status endpoint and `JobTrace` categories to distinguish queued, timed out, cancelled, and failed work.

## Provider diagnosis

- A configured cloud key does not prove provider availability. Compare the sanitized status route with a standalone provider request and record only status/error categories, never credentials or raw provider responses.
- Ollama failures should be checked separately with the local model service and a known small model. Do not change cloud routing based only on a model-list response.

## Email safety

- Email drafting produces a widget and does not send mail.
- Use the widget's explicit send action only after checking recipient, subject, body, and attachments. Delivery still requires the request-scoped admin key and owner-scoped attachment resolution.
- If a draft is malformed, resend the prompt or reload the page; do not manually add attachment bytes to persisted chat state.

## Verification without providers

- The repository test suite uses controlled fakes for queues, attachments, email delivery, and `/chat` streaming. Provider keys, SMTP, Ollama, and Ngrok are not required for deterministic verification.
- Browser automation is not configured in this repository. Use the Node syntax checks and the Python-executed frontend contract tests until a browser harness is added.
