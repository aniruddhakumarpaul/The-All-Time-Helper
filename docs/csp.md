# Content Security Policy Candidate

Inline event handlers have been removed from the active template and rendered chat controls. The application does not enable a CSP yet because browser smoke testing is still required and the template retains inline style attributes.

Candidate header:

```text
Content-Security-Policy: default-src 'self'; script-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; img-src 'self' data: blob: https:; connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
```

Before enabling it:

1. Smoke-test authentication, chat streaming, code highlighting, image generation/upscale polling, uploads, settings, and command palette behavior.
2. Confirm CDN availability and decide whether to self-host `marked`, DOMPurify, and Highlight.js.
3. Move inline style attributes into stylesheets before removing `'unsafe-inline'` from `style-src`.
