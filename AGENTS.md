# The All Time Helper

## Project identity

The All Time Helper is a FastAPI assistant with a modular ES6 frontend, deterministic tool lanes, CrewAI orchestration, Ollama fallback, OpenRouter cloud routes, SQLite persistence, and Chroma-backed memory.

## Repository map

- app/factory.py, app/main.py: ASGI construction, lifespan, static files, CORS, and local startup.
- app/routes/: authentication, chat/NDJSON streaming, attachments, email delivery, jobs, admin, health, and proxy boundaries.
- app/logic/: agent routing, context assembly, cloud/local execution, tools, attachment storage, memory, and safety hardening.
- static/js/: frontend orchestrator (app.js), DOM/UI (ui.js), network adapter (api.js), email draft modules, dialogs, palette, and motion.
- static/css/, templates/: active visual shell and rendered HTML contract.
- app/tests/: repository-level behavioral and source-contract tests.
- app/contracts/: versioned boundary models and serializers.
- docs/: architecture, routing, attachment/image, memory, decisions, improvement, and recovery records.

## Verified commands

- Install: .venv\Scripts\python.exe -m pip install -r requirements.txt
- Start: .venv\Scripts\python.exe -m app.main
- Alternate start: .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload
- Tests: python -B -m pytest -q -p no:cacheprovider --basetemp C:\tmp\tah-pytest

Do not claim lint, type-check, browser, or cloud-provider verification unless the command actually exists and was run.

## Non-negotiable invariants

- User files are owner-scoped, bounded, validated, and represented in chat state by IDs/metadata. Do not place raw base64 in prompts, local chat persistence, DOM dataset state, or logs.
- .project_brain/ is generated Chroma runtime state; keep it ignored and never stage or delete its local database/index files.
- Text, Markdown, and PDF attachments are document context, not visual inputs. Only image MIME types enter vision analysis.
- Chat streaming remains NDJSON with job ID, status, heartbeat, content, final, error, disconnect, cancellation, and tool/inference lane behavior.
- Do not add request-body replay middleware; Starlette body replay can break streaming requests.
- Email drafting is a widget payload contract. Draft creation never sends mail; SMTP requires explicit approval, admin-key validation, owner-scoped attachments, and idempotency.
- Preserve the blue/red logo assets, active brand gradient, keyboard/Escape dismissal, text selection, reduced-motion behavior, and stable accessible control semantics.
- Never expose secrets, raw provider errors, attachment paths, recipient addresses, or raw tool arguments in user-facing status or telemetry.

## Required workflow

Use docs/ as the first source of truth and update the relevant document when behavior changes. The required orchestrator skill is installed at C:\Users\aniruddha.paul\.codex\skills\ai-builder-orchestrator\SKILL.md; use its smallest sufficient module set, with software engineering as the implementation owner and security/reliability overlays for trust-boundary changes. The fallback lifecycle reference is C:\Users\aniruddha.paul\.codex\skills\software-engineering-lifecycle\SKILL.md.

Before edits, inspect git state, relevant docs, exact code paths, and the narrowest useful tests. Make small coherent changes, preserve unrelated worktree changes, update tests/docs with behavior changes, run targeted checks, then run the full suite and review the final diff for secrets, ownership leaks, regressions, and unbounded work.