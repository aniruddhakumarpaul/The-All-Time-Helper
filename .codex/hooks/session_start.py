from common import emit, hook_context


emit(
    hook_context(
        "SessionStart",
        (
            "Project workflow: use docs/ as the first source of truth. "
            "Read the smallest relevant doc, then use rg and exact file reads. "
            "If behavior changes, update the relevant docs file or docs/decisions.md. "
            "Verify with the narrowest useful test or syntax check."
        ),
    )
)
