from common import emit, load_state, read_event


event = read_event()
state = load_state(event)

if event.get("stop_hook_active"):
    emit({"continue": True})
elif state.get("edited") and not state.get("verified"):
    emit(
        {
            "decision": "block",
            "reason": (
                "Files were edited in this turn, but no verification command was recorded. "
                "Run the narrowest useful test or syntax check, then finalize with what was checked. "
                "If behavior changed, also update the relevant docs file or docs/decisions.md."
            ),
        }
    )
else:
    emit({"continue": True})
