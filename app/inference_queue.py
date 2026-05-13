"""
inference_queue.py — Dedicated Inference Queue
Replaces raw asyncio.to_thread with backpressure-aware worker pool.

Features:
- Configurable concurrency (default: 1 worker for weak GPU)
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
    fn: Callable
    abort_event: threading.Event
    result_future: asyncio.Future = field(default=None)
    created_at: float = field(default_factory=time.time)
    timeout: float = DEFAULT_INFERENCE_TIMEOUT


class InferenceQueue:
    """
    Async-native inference queue with:
    - Configurable concurrency (1 worker = sequential GPU jobs)
    - Backpressure (max_queue_depth before rejecting new requests)
    - Per-job timeout and cancellation via abort_event
    """
    def __init__(self, max_workers: int = 1, max_queue_depth: int = 8):
        self._queue: Optional[asyncio.Queue] = None  # Initialized lazily in async context
        self._max_workers = max_workers
        self._max_queue_depth = max_queue_depth
        self._workers: list = []
        self._started = False

    async def _ensure_started(self):
        """Lazily initialize the queue and workers on first use (must be called from async context)."""
        if self._started:
            return
        self._queue = asyncio.Queue(maxsize=self._max_queue_depth)
        for i in range(self._max_workers):
            task = asyncio.create_task(self._worker(f"inference-worker-{i}"))
            self._workers.append(task)
        self._started = True
        logger.info(f"[InferenceQueue] Started {self._max_workers} worker(s) (max depth: {self._max_queue_depth})")

    async def _worker(self, name: str):
        """Worker loop — pulls jobs from queue and executes in a thread with timeout."""
        from app.logic.bus import job_id_context
        while True:
            job = await self._queue.get()
            if job is None:
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
                self._queue.task_done()

    async def submit(self, job_id: str, fn: Callable, abort_event: threading.Event, timeout: float = DEFAULT_INFERENCE_TIMEOUT) -> Any:
        """
        Submit an inference job and await its result.
        
        Args:
            job_id: Unique identifier for logging/debugging
            fn: The blocking callable to run (will be wrapped in asyncio.to_thread)
                NOTE: The callable MUST check the abort_event manually to cancel early!
            abort_event: threading.Event that signals cancellation
            timeout: Maximum seconds before the job is killed
            
        Returns:
            The result of fn()
            
        Raises:
            RuntimeError: If the queue is full (backpressure)
        """
        await self._ensure_started()
        
        if self._queue.full():
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
            fn=context_wrapper,
            abort_event=abort_event,
            result_future=future,
            timeout=timeout
        )
        await self._queue.put(job)
        
        logger.info(f"[InferenceQueue] Job {job_id} queued (depth: {self._queue.qsize()}/{self._max_queue_depth})")
        return await future

    @property
    def queue_depth(self) -> int:
        """Current number of jobs waiting in the queue."""
        return self._queue.qsize() if self._queue else 0

    async def shutdown(self):
        """Gracefully shut down all workers."""
        if not self._started:
            return
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)


# Singleton — 1 worker for weak GPU, max 8 queued requests
inference_queue = InferenceQueue(max_workers=1, max_queue_depth=8)
