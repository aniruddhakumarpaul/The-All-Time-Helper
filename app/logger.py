import logging
import sys
import os
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

# 1. Console Handler (Force UTF-8 for Emojis on Windows)
import sys
if sys.platform == "win32":
    # On Windows, we need to wrap the stream to handle UTF-8 correctly in some terminals
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_format)
console_handler.setLevel(logging.INFO)

# 2. File Handler (Persistent logging with UTF-8 rotation)
file_handler = RotatingFileHandler(
    "logs/app.log", 
    maxBytes=10*1024*1024, # 10MB
    backupCount=5,
    encoding='utf-8' # CRITICAL: Fix for emojis in file logs
)
file_handler.setFormatter(log_format)
file_handler.setLevel(logging.DEBUG)

# Add handlers
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Utility for tools/agents to get sub-loggers if needed
def get_logger(name):
    return logger.getChild(name)
