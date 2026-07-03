# Codex Handoff

This handoff intentionally covers only work that needs local execution, browser/runtime verification, or larger multi-file refactoring. The direct repository hardening already applied on branch `hardening/xss-auth-proxy-sync` should be reviewed first.

## Prompt for Codex 5.4 mini — local verification and narrow frontend completion

You are working on `aniruddhakumarpaul/The-All-Time-Helper`, branch `hardening/xss-auth-proxy-sync`.

Scope: local verification and small, low-risk follow-up edits only. Do not start broad architecture refactors.

Tasks:

1. Install the project in a clean virtual environment from `requirements.txt`. If dependencies fail because of missing system packages, document the exact failure and continue with syntax/static checks that do not require those packages.
2. Run syntax checks on the changed Python files:
   - `python -m py_compile app/security.py app/routes/auth.py app/database.py app/repository.py app/routes/chat.py app/logic/tools.py app/main.py app/diagnostics.py`
3. Run JavaScript syntax checks on changed frontend files:
   - `node --check static/js/ui.js`
   - Also check `static/js/app.js` and `static/js/utils.js` if Node is available.
4. Run the existing tests with `python -m pytest` or `python -m unittest discover`. If tests fail because they are stale, identify which test expectations are stale versus which failures are caused by the new branch.
5. Complete the narrow frontend sanitizer follow-up in `templates/index.html`: load DOMPurify 3.2.7 after `marked` and before `/static/js/utils.js`, using this exact CDN and SRI:
   - URL: `https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.2.7/purify.min.js`
   - SRI: `sha512-78KH17QLT5e55GJqP76vutp1D2iAoy06WcYBXB6iBCsmO6wWzx0Qdg8EDpm8mKXv68BcvHOyeeP4wxAL0twJGQ==`
   - Add `crossorigin="anonymous"` and `referrerpolicy="no-referrer"`.
6. Browser-smoke the app locally: sign-up/login page render, chat page render with a user name containing `<img src=x onerror=alert(1)>`, chat title rename with HTML-looking text, neural context display with HTML-looking text, and image proxy attempts to localhost/private addresses.
7. Commit only minimal fixes needed to make the branch pass those checks. Do not change behavior beyond the narrow verification fixes.

Expected output: a concise commit summary plus the exact commands run and their pass/fail output.

## Prompt for Codex 5.5 — larger architecture and test repair

You are working on `aniruddhakumarpaul/The-All-Time-Helper` after the `hardening/xss-auth-proxy-sync` branch is reviewed. Your scope is larger refactoring and test repair, not quick patching.

Goals:

1. Replace the remaining monolithic startup shape with an app factory:
   - Move app construction into `app/factory.py` or equivalent.
   - Keep `app/main.py` as the thin import/run entry point.
   - Move the image proxy route to `app/routes/proxy.py`.
   - Move health/status routes to `app/routes/health.py`.
   - Move Ngrok startup to an optional service module so production startup does not implicitly mutate tunnels.
2. Replace ad-hoc schema mutation with explicit migrations:
   - Introduce a minimal migration/version table if Alembic is too heavy for this project.
   - Stop relying on swallowed `ALTER TABLE` exceptions as schema management.
   - Plan removal or deprecation of the legacy `users.admin_authorized` column. The runtime must not trust it.
3. Repair the stale test suite:
   - `InferenceJob(owner=...)` and `InferenceQueue.cancel(...)` expectations are stale against the current queue implementation. Either restore a real cancel API and owner tracking, or update tests to match supported behavior.
   - Tests that expect helpers missing from runtime should be reconciled with actual implementation. If `send_or_simulate_email` remains, add real tests around it.
   - Add tests for admin-key validation, no persistent admin authorization fallback, UUID job IDs, merge-based chat sync, and image proxy private-address blocking.
4. Continue frontend de-inline work:
   - Remove inline `onclick`, `onkeydown`, `oninput`, `ondragstart`, and `ondragend` handlers from `templates/index.html` and rendered message HTML.
   - Bind events centrally from `static/js/app.js` and module code.
   - After this, add a practical Content Security Policy candidate. Do not enable it until the app is verified without inline handlers.
5. Split `app/logic/agents.py` into smaller modules:
   - model registry and key selection
   - intent classification
   - context assembly
   - cloud execution
   - local execution
   - result hardening/postprocessing
   Preserve behavior while reducing the god-module shape.

Constraints:

- Keep the hardening semantics from `hardening/xss-auth-proxy-sync` intact.
- Do not reintroduce persistent admin authorization.
- Do not let the LLM perform irreversible email sending without deterministic validation and idempotency.
- Prefer small commits grouped by subsystem.
- Run tests after each subsystem refactor.

Expected output: a PR with subsystem commits, updated tests, and a migration note explaining existing local database impact.
