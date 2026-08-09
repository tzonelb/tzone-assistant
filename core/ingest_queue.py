"""Fast-ack ingestion queue for absorbing inbound-message bursts.

Under a spike (e.g. ~10k messages arriving at nearly the same moment across many
companies), running each message's DB writes inline in the webhook handler makes
every request block on SQLite's single writer — most requests time out, and Meta/
WhatsApp then RETRY, multiplying the load. This queue decouples *accepting* a
message (microseconds) from *persisting* it (~5ms, serialized by the DB): the
webhook enqueues and returns 200 immediately; a small pool of worker threads
drains the queue into the normal processing path at whatever rate the database
sustains (~200/s).

Design choices (deliberately conservative):
- **Bounded queue + synchronous fallback, never drop.** If the queue is full the
  submitter runs the work inline instead (natural backpressure), so memory is
  bounded and no message is ever lost to a full queue.
- **Opt-in.** Off unless `INGEST_ASYNC=true`. When off, `submit()` returns False
  and callers process inline exactly as before — zero behaviour change.
- **At-least-once, in-memory.** A crash loses whatever is still queued (documented
  in docs/LOAD_TEST_REPORT.md). A durable append-log front-end is a future upgrade
  if that risk isn't acceptable.
- **Ordering.** Default `INGEST_WORKERS=1` preserves the order messages are
  persisted in (including two messages from the same customer). Because the
  database is a single writer, more workers do NOT increase throughput — they only
  risk reordering same-customer messages. Raise the worker count only alongside
  per-customer sharding (route a customer's messages to a fixed worker).
- Per-message failures are logged and never crash a worker.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

from config.settings import config

logger = logging.getLogger(__name__)


class IngestQueue:
    def __init__(self) -> None:
        self._queue: "queue.Queue[tuple[Callable[..., Any], dict[str, Any]]] | None" = None
        self._workers: list[threading.Thread] = []
        self._started = False
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(getattr(config, "INGEST_ASYNC", False))

    def start(self) -> None:
        """Start the worker pool. Idempotent; a no-op when disabled."""
        if not self.enabled:
            return
        with self._lock:
            if self._started:
                return
            maxsize = int(getattr(config, "INGEST_QUEUE_MAX", 10000))
            worker_count = max(1, int(getattr(config, "INGEST_WORKERS", 4)))
            self._queue = queue.Queue(maxsize=maxsize)
            for i in range(worker_count):
                t = threading.Thread(target=self._run, name=f"ingest-worker-{i}", daemon=True)
                t.start()
                self._workers.append(t)
            self._started = True
            logger.info("Ingest queue started: %d workers, maxsize=%d", worker_count, maxsize)

    def _run(self) -> None:
        assert self._queue is not None
        while True:
            func, kwargs = self._queue.get()
            try:
                func(**kwargs)
            except Exception:
                logger.exception("Ingest worker failed processing a message")
            finally:
                self._queue.task_done()

    def submit(self, func: Callable[..., Any], **kwargs: Any) -> bool:
        """Enqueue `func(**kwargs)` for background processing.

        Returns True if accepted onto the queue (caller should ack fast and not
        run the work). Returns False when async is disabled OR the queue is full
        — in both cases the caller must run the work inline itself (backpressure).
        """
        if not self.enabled or not self._started or self._queue is None:
            return False
        try:
            self._queue.put_nowait((func, kwargs))
            return True
        except queue.Full:
            return False

    def depth(self) -> int:
        return self._queue.qsize() if self._queue is not None else 0


ingest_queue = IngestQueue()
