from common import command_text, is_verification_command, load_state, read_event, save_state


event = read_event()
state = load_state(event)
tool_name = str(event.get("tool_name") or "")
command = command_text(event)

if tool_name == "apply_patch":
    state["edited"] = True
    state["docs_changed"] = bool(state.get("docs_changed")) or "docs/" in command.replace("\\", "/")
    state.setdefault("edit_tools", [])
    state["edit_tools"].append(tool_name)

if tool_name == "Bash" and is_verification_command(command):
    state["verified"] = True
    state.setdefault("verification_commands", [])
    state["verification_commands"].append(command)

if state:
    save_state(event, state)
