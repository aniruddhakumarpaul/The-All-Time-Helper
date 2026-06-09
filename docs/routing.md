# Routing

Routing is designed to prefer the smallest reliable execution path.

## Intent Levels
- `direct`: no tool execution required.
- `single`: one tool or a small deterministic workflow.
- `swarm`: multi-step or multi-tool task requiring the agent hierarchy.

## Important Rules
- Deterministic image-to-email workflows bypass cloud routing.
- Visual follow-ups inherit the unresolved visual task when history shows a continuing image request.
- Email-template edit prompts should update the current draft and return `EMAIL_DRAFT_PAYLOAD:` instead of raw tool-plan JSON.
- One-word answers to an attachment clarification (`image`, `text`, `both`, `summary`) inherit the pending email attachment request and must resolve deterministically instead of going to normal chat.
- Context switches to search, code, or factual questions should not inherit visual state.
