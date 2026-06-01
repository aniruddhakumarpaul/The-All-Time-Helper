from common import emit, hook_context


emit(
    hook_context(
        "PreToolUse",
        (
            "Before editing this repo, confirm the relevant docs/code path. "
            "Keep the change scoped. If this edit changes behavior, update the "
            "relevant docs file or docs/decisions.md and run a narrow verification."
        ),
    )
)
