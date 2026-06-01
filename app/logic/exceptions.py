class AgentFastExit(BaseException):
    """Custom signal to force-terminate an agentic loop upon successful tool execution."""
    def __init__(self, result):
        self.result = result
