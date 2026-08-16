"""Hard ceilings on inbound webhook traffic.

A signature proves who sent a delivery, not that the delivery is reasonable. A
correctly signed body was previously free to be as large as the network allowed,
carry as many events as it liked, and hold the HTTP response open — inside
Starlette's shared worker threadpool — for as long as processing every one of
those events took. That pool also serves every ``def`` route in the application,
so a flood on the webhook froze the dashboard for everybody.

Three limits live here, and none of them is a commercial quota: they protect the
process, so no plan may raise them.

* :func:`read_capped_body` refuses a body over ``WEBHOOK_MAX_BODY_BYTES``
  before it is buffered. nginx caps at 25 MB, but a deployment that never sees
  nginx had no cap at all.
* :func:`event_limit` and :func:`log_dropped_events` bound how many events one
  delivery may produce. Truncation is always logged with a count, because a
  silent truncation reads exactly like "we handled everything".
* :func:`dispatch` acknowledges the delivery and runs the work as a background
  task, in the shape ``main.py`` already uses for its sweeps: a task on the
  loop, one unit of work per ``asyncio.to_thread`` call, failures logged rather
  than swallowed. The backlog behind those acknowledgements is itself bounded —
  past the ceiling a delivery is refused with 503 so the provider retries it,
  which is the one way to shed load without losing the events.

Every refusal is cheap and logged with enough detail to recognise an attack.

One consequence of acknowledging first is worth stating plainly, because it is a
trade rather than a free win: between the acknowledgement and the end of
processing, the events live only in this process. A graceful shutdown waits for
them (:func:`drain`, called from ``main.py``'s lifespan) and a failure inside the
work is logged, but a hard kill in that window loses them, where holding the
response open would have left the provider to redeliver. Closing that window for
good needs a durable inbound queue table, which this module deliberately does not
invent. The exchange is worth making anyway: a delivery held long enough to
matter is a delivery Meta has already timed out, retried, and — often enough —
unsubscribed the app over.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Sequence

from fastapi import HTTPException, Request, status

from channels.meta.logger import log_meta_event
from config.settings import config


logger = logging.getLogger(__name__)


# How much accepted-but-unfinished work may pile up behind the acknowledgements,
# as a multiple of one delivery's event cap. A handful of deliveries may be in
# flight at once; a sustained flood is refused with 503 and retried by the
# provider rather than quietly accumulating in memory.
IN_FLIGHT_DELIVERY_ALLOWANCE = 20

# How long shutdown waits for accepted work to finish before giving up on it.
SHUTDOWN_DRAIN_SECONDS = 30.0


# ----------------------------------------------------------------------
# Body size
# ----------------------------------------------------------------------


def body_limit() -> int:
    return max(0, int(config.WEBHOOK_MAX_BODY_BYTES))


def _declared_length(request: Request) -> int | None:
    """The Content-Length the caller claims, when it claims a usable one."""
    raw = request.headers.get("content-length")

    if raw is None:
        return None

    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _refuse_body(
    *,
    source: str,
    request: Request,
    limit: int,
    declared: int | None,
    received: int | None,
) -> None:
    client = request.client.host if request.client else None

    logger.warning(
        "Refused an oversized %s webhook body: limit=%s declared=%s received=%s "
        "client=%s",
        source,
        limit,
        declared,
        received,
        client,
    )
    log_meta_event(
        "webhook_body_too_large",
        {
            "source": source,
            "limit_bytes": limit,
            "declared_bytes": declared,
            "received_bytes": received,
            "client": client,
        },
    )

    raise HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail="Webhook body is too large.",
    )


async def read_capped_body(request: Request, *, source: str) -> bytes:
    """Read the request body, refusing anything over the configured cap.

    The declared Content-Length is checked first because it is the cheapest
    possible refusal, but it is only a claim: the real check is the number of
    bytes actually received, counted as they arrive so an oversized body is
    abandoned part-way instead of being buffered in full.
    """
    limit = body_limit()
    declared = _declared_length(request)

    if declared is not None and declared > limit:
        _refuse_body(
            source=source,
            request=request,
            limit=limit,
            declared=declared,
            received=None,
        )

    chunks: list[bytes] = []
    received = 0

    async for chunk in request.stream():
        received += len(chunk)

        if received > limit:
            _refuse_body(
                source=source,
                request=request,
                limit=limit,
                declared=declared,
                received=received,
            )

        if chunk:
            chunks.append(chunk)

    body = b"".join(chunks)

    # Starlette caches the body under this name and serves `request.body()` from
    # it. Setting it keeps the read idempotent: anything downstream that asks
    # for the body again gets these bytes instead of "Stream consumed".
    request._body = body  # noqa: SLF001

    return body


# ----------------------------------------------------------------------
# Event count
# ----------------------------------------------------------------------


def event_limit() -> int:
    """How many events one delivery may produce. Read per call so the ceiling
    can be lowered in a test without reimporting the parsers."""
    return max(1, int(config.WEBHOOK_MAX_EVENTS))


def log_dropped_events(*, source: str, kept: int, dropped: int) -> None:
    """Record a truncation with its count. Never silent."""
    if dropped <= 0:
        return

    logger.warning(
        "%s webhook delivery exceeded the event cap: processed %s, dropped %s "
        "(cap=%s)",
        source,
        kept,
        dropped,
        event_limit(),
    )
    log_meta_event(
        "webhook_events_truncated",
        {
            "source": source,
            "processed": kept,
            "dropped": dropped,
            "cap": event_limit(),
        },
    )


# ----------------------------------------------------------------------
# Handing the work off the request
# ----------------------------------------------------------------------


_IN_FLIGHT: set[asyncio.Task] = set()
_IN_FLIGHT_EVENTS = 0


def in_flight_events() -> int:
    """Events accepted whose processing has not finished yet."""
    return _IN_FLIGHT_EVENTS


def backlog_ceiling() -> int:
    return event_limit() * IN_FLIGHT_DELIVERY_ALLOWANCE


def _refuse_backlog(*, source: str, incoming: int) -> None:
    logger.warning(
        "Refused a %s webhook delivery of %s event(s): %s already in flight, "
        "ceiling %s",
        source,
        incoming,
        _IN_FLIGHT_EVENTS,
        backlog_ceiling(),
    )
    log_meta_event(
        "webhook_backlog_full",
        {
            "source": source,
            "incoming": incoming,
            "in_flight": _IN_FLIGHT_EVENTS,
            "ceiling": backlog_ceiling(),
        },
    )

    # 503 rather than 200: the delivery has not been handled, and the provider
    # redelivering it later is what keeps the events from being lost.
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Webhook backlog is full. Please redeliver.",
        headers={"Retry-After": "30"},
    )


async def _run(
    work: Callable[[Sequence[Any]], Any],
    events: Sequence[Any],
    *,
    source: str,
) -> Any:
    global _IN_FLIGHT_EVENTS

    try:
        # `asyncio.to_thread`, like the sweeps in `main.py`, rather than
        # Starlette's `run_in_threadpool`: that pool is shared with every `def`
        # route, and holding it is how a webhook flood froze the dashboard.
        return await asyncio.to_thread(work, events)
    except asyncio.CancelledError:
        logger.warning(
            "%s webhook processing was cancelled with %s event(s) unfinished",
            source,
            len(events),
        )
        log_meta_event(
            "webhook_work_cancelled",
            {"source": source, "events": len(events)},
        )
        raise
    except Exception:  # noqa: BLE001
        # The per-event handlers already isolate their own failures; this is the
        # backstop that stops a background task from failing invisibly.
        logger.exception(
            "%s webhook processing failed for a batch of %s event(s)",
            source,
            len(events),
        )
        log_meta_event(
            "webhook_work_failed",
            {"source": source, "events": len(events)},
        )
        return None
    finally:
        _IN_FLIGHT_EVENTS -= len(events)


def dispatch(
    work: Callable[[Sequence[Any]], Any],
    events: Sequence[Any],
    *,
    source: str,
) -> asyncio.Task | None:
    """Accept the events now and process them after the response has gone.

    Raises 503 when the backlog is already at its ceiling, so load is shed by
    asking the provider to redeliver rather than by dropping events.
    """
    global _IN_FLIGHT_EVENTS

    if not events:
        return None

    if _IN_FLIGHT_EVENTS + len(events) > backlog_ceiling():
        _refuse_backlog(source=source, incoming=len(events))

    _IN_FLIGHT_EVENTS += len(events)

    try:
        task = asyncio.create_task(_run(work, events, source=source))
    except RuntimeError:
        # No running loop to hand the work to. Nothing was accepted, so the
        # count has to come back down or the backlog looks permanently full.
        _IN_FLIGHT_EVENTS -= len(events)
        raise

    # Held in a set because the loop only keeps a weak reference: an untracked
    # task can be garbage-collected mid-flight, which loses the events silently.
    _IN_FLIGHT.add(task)
    task.add_done_callback(_IN_FLIGHT.discard)

    return task


async def drain(timeout: float = SHUTDOWN_DRAIN_SECONDS) -> int:
    """Wait for accepted work to finish. Returns how many batches completed.

    Called on shutdown: the delivery was already acknowledged, so anything still
    running is work nobody will send again.
    """
    pending = set(_IN_FLIGHT)

    if not pending:
        return 0

    done, still_running = await asyncio.wait(pending, timeout=timeout)

    if still_running:
        logger.warning(
            "Shutting down with %s webhook batch(es) still processing after %ss",
            len(still_running),
            timeout,
        )
        log_meta_event(
            "webhook_drain_incomplete",
            {"batches": len(still_running), "timeout_seconds": timeout},
        )

    return len(done)
