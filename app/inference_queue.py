"""
inference_queue.py — Dedicated Inference Queue
Replaces raw asyncio.to_thread with backpressure-aware worker pool.

Features:
- Serialized model inference (default: 1 worker for weak GPU)
- Separate bounded workers for proven deterministic tools
- Backpressure (max queue depth before rejecting)
- Per-job timeout and cancellation
- Graceful abort propagation
"""
import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from app.logger import logger


DEFAULT_INFERENCE_TIMEOUT = 180.0

@dataclass
class InferenceJob:
    """A single inference request with its callbacks and abort signal."""
    id: str
    owner: str
    fn: Callable
    abort_event: threading.Event
    result_future: asyncio.Future = field(default=None)
    created_at: float = field(default_factory=time.time)
    timeout: float = DEFAULT_INFERENCE_TIMEOUT
    lane: str = "inference"


class InferenceQueue:
    """
    Async-native execution queue with:
    - Serialized model inference (1 worker = sequential GPU jobs)
    - A separate lane for model-free deterministic tools
    - Backpressure before rejecting new requests
    - Per-job timeout and cancellation via abort_event
    """
    def __init__(
        self,
        max_workers: int = 1,
        max_queue_depth: int = 8,
        fast_workers: int = 2,
        fast_queue_depth: int = 8,
    ):
        self._queue: Optional[asyncio.Queue] = None  # Initialized lazily in async context
        self._fast_queue: Optional[asyncio.Queue] = None
        self._max_workers = max_workers
        self._max_queue_depth = max_queue_depth
        self._max_fast_workers = fast_workers
        self._max_fast_queue_depth = fast_queue_depth
        self._workers: list = []
        self._fast_workers: list = []
        self._started = False
        self._start_lock: Optional[asyncio.Lock] = None
        self._active_jobs: dict[str, InferenceJob] = {}

    async def _ensure_started(self):
        """Lazily initialize both execution lanes on first use."""
        if self._started:
            return
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._started:
                return
            self._queue = asyncio.Queue(maxsize=self._max_queue_depth)
            self._fast_queue = asyncio.Queue(maxsize=self._max_fast_queue_depth)
            for i in range(self._max_workers):
                task = asyncio.create_task(self._worker(f"inference-worker-{i}", self._queue))
                self._workers.append(task)
            for i in range(self._max_fast_workers):
                task = asyncio.create_task(self._worker(f"tool-worker-{i}", self._fast_queue))
                self._fast_workers.append(task)
            self._started = True
            logger.info(
                "[InferenceQueue] Started %d inference worker(s) and %d tool worker(s)",
                self._max_workers,
                self._max_fast_workers,
            )

    async def _worker(self, name: str, work_queue: asyncio.Queue):
        """Worker loop — pulls jobs from queue and executes in a thread with timeout."""
        from app.logic.bus import job_id_context
        while True:
            job = await work_queue.get()
            if job is None:
                work_queue.task_done()
                break
            try:
                # Skip if already cancelled before we even start
                if getattr(job, 'abort_event', None) and job.abort_event.is_set():
                    if not job.result_future.done():
                        job.result_future.set_result("Operation cancelled.")
                    continue
                
                elapsed = time.time() - job.created_at
                logger.debug(f"[{name}] Processing job {job.id} (waited {elapsed:.1f}s in queue)")
                
                # Execute the blocking function in a thread with timeout
                token = job_id_context.set(job.id)
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(job.fn),
                        timeout=job.timeout
                    )
                finally:
                    job_id_context.reset(token)
                
                if not job.result_future.done():
                    job.result_future.set_result(result)
                    
            except asyncio.TimeoutError:
                job.abort_event.set()  # Signal the blocking thread to stop
                logger.error(f"[{name}] Job {job.id} timed out after {job.timeout}s")
                
                # FLAW 1 FIX: Check ToolResultBus for 'Ghost Success'
                from app.logic.bus import tool_result_bus
                bus_result = tool_result_bus.pop_result(job.id)
                
                if not job.result_future.done():
                    if bus_result:
                        logger.info(f"[{name}] Recovered 'Ghost Success' for job {job.id} from bus.")
                        job.result_future.set_result(bus_result)
                    else:
                        job.result_future.set_result(
                            "⚠️ **Inference Timeout.** The model took too long to respond. "
                            "Please try again or switch to a lighter model."
                        )
            except asyncio.CancelledError:
                logger.warning(f"[{name}] Job {job.id} was cancelled")
                if not job.result_future.done():
                    job.result_future.set_result("Operation cancelled.")
            except Exception as e:
                logger.error(f"[{name}] Job {job.id} failed: {e}", exc_info=True)
                if not job.result_future.done():
                    job.result_future.set_exception(e)
            finally:
                if self._active_jobs.get(job.id) is job:
                    self._active_jobs.pop(job.id, None)
                work_queue.task_done()

    async def submit(
        self,
        job_id: str,
        fn: Callable,
        abort_event: threading.Event,
        timeout: float = DEFAULT_INFERENCE_TIMEOUT,
        owner: str = "",
        lane: str = "inference",
    ) -> Any:
        """
        Submit an inference job and await its result.
        
        Args:
            job_id: Unique identifier for logging/debugging
            fn: The blocking callable to run (will be wrapped in asyncio.to_thread)
                NOTE: The callable MUST check the abort_event manually to cancel early!
            abort_event: threading.Event that signals cancellation
            timeout: Maximum seconds before the job is killed
            lane: "inference" for serialized model work or "tool" for deterministic actions

        Returns:
            The result of fn()
            
        Raises:
            RuntimeError: If the queue is full (backpressure)
        """
        await self._ensure_started()

        if lane not in {"inference", "tool"}:
            raise ValueError(f"Unknown execution lane: {lane}")
        work_queue = self._fast_queue if lane == "tool" else self._queue
        max_depth = self._max_fast_queue_depth if lane == "tool" else self._max_queue_depth

        if work_queue.full():
            raise RuntimeError(
                "⚠️ **Server Busy.** The inference queue is full. "
                "Please wait a moment and try again."
            )
        
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        
        import contextvars
        ctx = contextvars.copy_context()
        def context_wrapper():
            return ctx.run(fn)
        
        job = InferenceJob(
            id=job_id,
            owner=owner,
            fn=context_wrapper,
            abort_event=abort_event,
            result_future=future,
            timeout=timeout,
            lane=lane,
        )
        self._active_jobs[job_id] = job
        await work_queue.put(job)

        logger.info(
            "[InferenceQueue] Job %s queued on %s lane (depth: %d/%d)",
            job_id,
            lane,
            work_queue.qsize(),
            max_depth,
        )
        return await future

    def cancel(self, job_id: str, owner: str) -> bool:
        """Cancel a queued or active job only when it belongs to the caller."""
        job = self._active_jobs.get(job_id)
        if not job or not owner or job.owner != owner:
            return False
        job.abort_event.set()
        if job.result_future and not job.result_future.done():
            job.result_future.set_result("Operation cancelled.")
        return True

    @property
    def queue_depth(self) -> int:
        """Current number of jobs waiting across both lanes."""
        return self.inference_queue_depth + self.tool_queue_depth

    @property
    def inference_queue_depth(self) -> int:
        return self._queue.qsize() if self._queue else 0

    @property
    def tool_queue_depth(self) -> int:
        return self._fast_queue.qsize() if self._fast_queue else 0

    async def shutdown(self):
        """Gracefully shut down all workers."""
        if not self._started:
            return
        for _ in self._workers:
            await self._queue.put(None)
        for _ in self._fast_workers:
            await self._fast_queue.put(None)
        await asyncio.gather(*self._workers, *self._fast_workers, return_exceptions=True)
        self._workers.clear()
        self._fast_workers.clear()
        self._active_jobs.clear()
        self._queue = None
        self._fast_queue = None
        self._start_lock = None
        self._started = False


# Serialize GPU work; deterministic tools have two bounded non-model workers.
inference_queue = InferenceQueue(max_workers=1, max_queue_depth=8, fast_workers=2, fast_queue_depth=8)
