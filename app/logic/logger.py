import logging
import datetime
import os

# Configure the Action Logger
LOG_FILE = "agent_actions.log"

# Standard Python logger
logger = logging.getLogger("AgentLogger")
logger.setLevel(logging.INFO)

# File Handler
fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

def log_agent_step(step_output):
    """Callback triggered after each agent step in the swarm."""
    try:
        # CrewAI's step_output is an AgentAction or similar object
        # We extract the agent name and the action taken
        agent_name = getattr(step_output, "agent", "Unknown Agent")
        tool_used = getattr(step_output, "tool", "No Tool")
        thought = getattr(step_output, "thought", "Processing...")
        
        log_msg = f"[AGENT: {agent_name}] | THOUGHT: {thought} | ACTION: {tool_used}"
        logger.info(log_msg)
        
        # Also print to console for dev visibility
        print(f"\n📢 [WORKFLOW LOG]: {log_msg}")
        
    except Exception as e:
        logger.error(f"Failed to log step: {e}")

def log_system_error(error_msg: str):
    logger.error(f"[SYSTEM ERROR]: {error_msg}")
