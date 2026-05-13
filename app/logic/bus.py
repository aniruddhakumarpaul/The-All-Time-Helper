import threading
from typing import Dict, Any

class ToolResultBus:
    """
    A thread-safe shared bus for communicating tool results across 
    asynchronous job boundaries. 
    
    Used to resolve 'Ghost Success' where a tool succeeds just as 
    the inference job times out.
    """
    def __init__(self):
        self._results: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def set_result(self, job_id: str, result: Any):
        if not job_id: return
        with self._lock:
            self._results[job_id] = result

    def get_result(self, job_id: str) -> Any:
        if not job_id: return None
        with self._lock:
            return self._results.get(job_id)

    def pop_result(self, job_id: str) -> Any:
        """Atomically get and remove a result from the bus."""
        if not job_id: return None
        with self._lock:
            return self._results.pop(job_id, None)

    def clear(self, job_id: str):
        with self._lock:
            if job_id in self._results:
                del self._results[job_id]

# Singleton instance
tool_result_bus = ToolResultBus()

# ContextVar for job ID tracking
from contextvars import ContextVar
job_id_context: ContextVar[str] = ContextVar("job_id_context", default="")
