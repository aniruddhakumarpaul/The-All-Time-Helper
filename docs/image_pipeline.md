# Image Pipeline

Generated images follow a two-stage path:

1. Pollinations generates the image and returns markdown with a `uid`.
2. The backend upscaler writes a local file under `static/uploads/upscaled_{job_id}.jpg`.

## Frontend Behavior
- Pollinations-generated markdown is rendered through the local upscale polling flow.
- The browser should not hammer `/api/image_proxy` for generated images.
- Once the upscale job is `ready`, the visible card should switch to the local static file.
- If enhancement fails, the card tries the already-validated original provider URL once instead of hiding a usable result or entering a long proxy retry loop; a terminal error with a safe direct link appears only if that fallback also fails.
- Pending upscale state and logs never expose the prompt-bearing source URL. Runtime telemetry records the source host, job ID, result class, and output size only.
- Rehydrated chats must re-attach the upscale poller after render so refreshed pages can promote hidden Pollinations cards to the local file.
- Saved chat content should store the local `/static/uploads/upscaled_*.jpg` URL after success.

## Uploaded Image Analysis
- `/attachments` returns owner-scoped file IDs; `/chat` resolves those IDs to bytes only for the authenticated owner before inference.
- The route hydrates a bounded set of recent history attachment IDs so explicit visual follow-ups can reuse a still-valid upload.
- The local vision decoder accepts validated upload bytes/base64, restricts local paths to upload roots, and routes remote images through the size-bounded SSRF-resistant fetcher.
- Raw base64 is never copied into the assembled model prompt as an image reference.
- Vision-capable OpenRouter/Gemma routes receive validated images as structured multimodal message parts, so ordinary image questions use one model request instead of a perception pass followed by a second chat pass.
- Simple local perception questions use deterministic pixel metadata first for dominant color and dimensions, then the installed Moondream specialist for semantic descriptions; results are cached by validated image hash plus normalized request, while complex local visual reasoning stays on the selected Gemma 4 route.
- Empty output and known garbage sentinels are rejected before caching or returning. Bounded fallback to Gemma 4 is allowed only where latency policy permits it.
- Vision logs contain only source class, validated byte count, model, outcome, and duration. They never contain image bytes, base64, local owner paths, or full remote URLs.
- Attachment inference no longer writes a second copy into `static/uploads`; owner-scoped attachment storage remains the canonical uploaded file source.
- /attachments accepts PNG, JPEG, GIF, WEBP, TXT, Markdown, and PDF files. Text and Markdown are decoded with a 30,000-character cap; PDFs use the installed itz extractor when available, and document bytes are never routed as vision images.
## Email Attachments
- Generated-image email attachments must use downloaded bytes, not a raw Pollinations URL.
- Attachment download logic validates HTTP success, image bytes, and size before accepting the payload.
- Remote image fetches for `/api/image_proxy` and email attachments validate `http`/`https` URLs, block localhost/private/reserved/link-local/multicast DNS results, re-check redirect targets, and enforce streaming byte caps before accepting bytes.

