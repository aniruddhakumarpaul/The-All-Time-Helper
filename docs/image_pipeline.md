# Image Pipeline

Generated images follow a two-stage path:

1. Pollinations generates the image and returns markdown with a `uid`.
2. The backend upscaler writes a local file under `static/uploads/upscaled_{job_id}.jpg`.

## Frontend Behavior
- Pollinations-generated markdown is rendered through the local upscale polling flow.
- The browser should not hammer `/api/image_proxy` for generated images.
- Raw external Pollinations markdown URLs are normalized before rendering; `model=turbo` is rewritten to `model=flux`.
- Image load retries stop immediately when `/api/image_proxy` reports permanent upstream failures such as `401`, `402`, `403`, or `404`.
- Once the upscale job is `ready`, the visible card should switch to the local static file.
- Saved chat content should store the local `/static/uploads/upscaled_*.jpg` URL after success.

## Proxy Behavior
- `/api/image_proxy` normalizes Pollinations URLs before fetching, including rewriting `model=turbo` to `model=flux`.
- Pollinations `401`, `402`, and `403` responses return a clear text error instead of an empty response body.

## Email Attachments
- Generated-image email attachments must use downloaded bytes, not a raw Pollinations URL.
- Attachment download logic validates HTTP success, image bytes, and size before accepting the payload.
