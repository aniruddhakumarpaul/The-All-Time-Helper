# Image Pipeline

Generated images follow a two-stage path:

1. Pollinations generates the image and returns markdown with a `uid`.
2. The backend upscaler writes a local file under `static/uploads/upscaled_{job_id}.jpg`.

## Frontend Behavior
- Pollinations-generated markdown is rendered through the local upscale polling flow.
- The browser should not hammer `/api/image_proxy` for generated images.
- Once the upscale job is `ready`, the visible card should switch to the local static file.
- Rehydrated chats must re-attach the upscale poller after render so refreshed pages can promote hidden Pollinations cards to the local file.
- Saved chat content should store the local `/static/uploads/upscaled_*.jpg` URL after success.

## Email Attachments
- Generated-image email attachments must use downloaded bytes, not a raw Pollinations URL.
- Attachment download logic validates HTTP success, image bytes, and size before accepting the payload.
- Remote image fetches for `/api/image_proxy` and email attachments validate `http`/`https` URLs, block localhost/private/reserved/link-local/multicast DNS results, re-check redirect targets, and enforce streaming byte caps before accepting bytes.

