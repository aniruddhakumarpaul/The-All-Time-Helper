import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# Configure format
log_format = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Root Logger Setup
logger = logging.getLogger("AllTimeHelper")
logger.setLevel(logging.DEBUG)
logger.propagate = False

# Console Handler (Force UTF-8 for Emojis on Windows)
if sys.platform == "win32" and hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "").lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _has_handler(handler_type, *, filename: str | None = None) -> bool:
    for handler in logger.handlers:
        if not isinstance(handler, handler_type):
            continue
        if filename and getattr(handler, "baseFilename", None) != os.path.abspath(filename):
            continue
        return True
    return False


if not _has_handler(logging.StreamHandler):
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_format)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

log_file = "logs/app.log"
if not _has_handler(RotatingFileHandler, filename=log_file):
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(log_format)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)


# Utility for tools/agents to get sub-loggers if needed
def get_logger(name):
    return logger.getChild(name)


def _safe_log_label(value, default="unknown"):
    """Keep agent telemetry low-cardinality and free of model/user content."""
    candidate = getattr(value, "name", None) or getattr(value, "role", None)
    if not isinstance(candidate, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", candidate):
        return default
    return candidate


def log_agent_step(step_output):
    """Callback triggered after each agent step without logging model content."""
    try:
        agent_label = _safe_log_label(getattr(step_output, "agent", None))
        tool_label = _safe_log_label(getattr(step_output, "tool", None))
        logger.info("[AgentTrace] state=step agent=%s tool=%s", agent_label, tool_label)
    except Exception:
        logger.error("[AgentTrace] state=log_failure")


def log_system_error(error_msg: str):
    logger.error("[SYSTEM ERROR] category=%s", type(error_msg).__name__)
