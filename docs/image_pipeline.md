# Image Pipeline

Generated images follow a two-stage path:

1. Pollinations generates the image and returns markdown with a `uid`.
2. The backend upscaler writes a local file under `static/uploads/upscaled_{job_id}.jpg`.

## Frontend Behavior
- Pollinations-generated markdown is rendered through the local upscale polling flow.
- The browser should not hammer `/api/image_proxy` for generated images.
- Once the upscale job is `ready`, the visible card should switch to the local static file.
- Saved chat content should store the local `/static/uploads/upscaled_*.jpg` URL after success.

## Email Attachments
- Generated-image email attachments must use downloaded bytes, not a raw Pollinations URL.
- Attachment download logic validates HTTP success, image bytes, and size before accepting the payload.

