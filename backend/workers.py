"""The seven jobs that run on a timer, with nothing about HTTP in them.

Split out of `main.py`, which had grown to hold three unrelated things at once:
what the application *is* (routers, middleware, gates), what it *does at boot*
(the lifespan), and what it *does forever after* (these). Reading any one of
them meant scrolling past the other two, and the workers are the part most
likely to be changed by somebody who has no business touching the router
registration two hundred lines below.

Nothing here imports FastAPI. A worker is a coroutine that sleeps, does some
database work for every company, logs what it did, and never raises — which
makes each one testable by calling it, and makes this file readable without
knowing anything about the web layer.

Two rules hold in every worker below, and both were learned the hard way:

* **Sleep first, then work.** Every loop waits before its first pass so that a
  restart does not put seven sweeps and the boot sequence on the disk at the
  same instant.
* **One company's failure is that company's failure.** Every sweep isolates per
  company and logs against the company it came from, because a single
  unreadable database must not stop the other nine hundred and ninety-nine
  from being served.
"""

from __future__ import annotations

import asyncio
import logging

from backend.services.activity_service import activity_service
from backend.services.auth_service import auth_service
from backend.services.conversation_control_service import (
    conversation_control_service,
)
from backend.services.diagnostics_service import diagnostics_service
from backend.services.health_service import health_service
from backend.services.notification_service import notification_service
from backend.services.work_index_service import (
    KIND_PENDING_REPLY,
    KIND_SCHEDULED_POST,
    KIND_TAKEOVER,
    work_index_service,
)
from channels.meta.smart_reply import process_due_replies
from channels.post_publisher import publish_due_posts
from config.settings import config
from database.manager import database_manager


logger = logging.getLogger("tzone.workers")


TAKEOVER_SWEEP_SECONDS = 10
PENDING_REPLY_SWEEP_SECONDS = 2
SCHEDULED_POST_SWEEP_SECONDS = 30
ATTEMPT_PRUNE_SECONDS = 3600

# How often the platform checks itself. Fifteen minutes is often enough that a
# corrupt database is found the same hour it happens, and rare enough that the
# deep check — which reads every page of every company file — is not competing
# with live traffic for the disk.
SELF_CHECK_SECONDS = 900


def _sweep_concurrency() -> int:
    """How many companies one sweep may work on at once.

    Clamped rather than trusted: a zero or negative value would stop the sweeps
    dead, and a very large one would hand the whole thread pool — the same pool
    serving live requests — to background work. See the reasoning next to
    ``SWEEP_MAX_CONCURRENT_COMPANIES`` in ``config/settings.py``.
    """
    return max(1, min(64, int(config.SWEEP_MAX_CONCURRENT_COMPANIES)))


async def _run_for_companies(label: str, company_ids: list[int], work) -> int:
    """Run one unit of work per company, several at a time, isolating failures.

    A company whose database cannot be opened must not stop the others from
    being served — which is why every company is awaited even when one raises,
    and why the exception is logged against the company it came from.

    Concurrency is bounded by a semaphore rather than by chunking the list: a
    single slow company would otherwise hold up everyone in its chunk, and the
    slow ones are exactly the ones waiting on a model call.
    """
    if not company_ids:
        return 0

    semaphore = asyncio.Semaphore(_sweep_concurrency())
    done = 0

    async def run_one(company_id: int) -> bool:
        async with semaphore:
            try:
                await asyncio.to_thread(work, company_id)
                return True
            except Exception:
                logger.exception("%s failed for company %s", label, company_id)
                return False

    for outcome in await asyncio.gather(
        *(run_one(company_id) for company_id in company_ids)
    ):
        done += 1 if outcome else 0

    return done


async def _sweep(label: str, kind: str, work) -> None:
    """Serve only the companies the control plane says have work of this kind.

    The list comes from ``company_work_index``, which costs one control-database
    read however many companies the platform serves. Everything else in this
    function is the same work as before; what changed is that a company with an
    empty queue is no longer opened to discover that.

    After the work, the company's entry is rewritten from its own tables. That
    is what deletes a stale entry — including one left by a batch that was
    completed, dropped, or rolled back — so a company can only ever be swept
    once for work it no longer has.
    """
    try:
        company_ids = await asyncio.to_thread(work_index_service.due_companies, kind)
    except Exception:
        logger.exception("%s: could not read the work index", label)
        return

    if not company_ids:
        return

    def run(company_id: int) -> None:
        try:
            work(company_id)
        finally:
            # In a `finally` on purpose: a company whose work raised still has
            # to have its entry re-derived, or a permanently failing company
            # would be swept for ever on a deadline nothing can clear.
            work_index_service.refresh(company_id, (kind,))

    await _run_for_companies(label, company_ids, run)


async def takeover_timeout_worker() -> None:
    """Return conversations to the assistant when a takeover lapses.

    A takeover expiry looks time-based, and that is why it was a full sweep, but
    it is not: an expiry only exists because an employee took a conversation
    over, and that action writes an exact deadline. It is the same shape as a
    reply or a post — a known time, written by a known event — so it uses the
    same index, and a platform where nobody has taken anything over does no work
    here at all.

    What is genuinely different is who writes the deadline. A reply or a post is
    registered by the queue that owns it, and may refuse to queue work it could
    not register. A takeover is registered by a human action that has already
    succeeded, so its registration is best-effort and leans on the hourly
    reconcile as a backstop. The consequences match: an unregistered reply is a
    customer who is never answered, while an unregistered takeover expiry is a
    conversation that returns to the assistant late.
    """
    while True:
        await _sweep(
            "takeover sweep",
            KIND_TAKEOVER,
            conversation_control_service.expire_overdue_takeovers,
        )
        await asyncio.sleep(TAKEOVER_SWEEP_SECONDS)


async def pending_reply_worker() -> None:
    """Deliver assistant replies whose collection window has closed."""
    while True:
        await _sweep("assistant reply sweep", KIND_PENDING_REPLY, process_due_replies)
        await asyncio.sleep(PENDING_REPLY_SWEEP_SECONDS)


async def scheduled_post_worker() -> None:
    """Publish approved posts whose scheduled time has arrived.

    Swept less often than replies: a post is scheduled to the minute, not the
    second, so there is nothing to gain from looking more often.
    """
    while True:
        await _sweep("scheduled post sweep", KIND_SCHEDULED_POST, publish_due_posts)
        await asyncio.sleep(SCHEDULED_POST_SWEEP_SECONDS)


async def self_check_worker() -> None:
    """Verify the platform can serve, on a timer rather than on a click.

    The distinction is the whole point: a corrupt company database discovered
    when a customer writes in is an incident, and the same corruption found by a
    sweep at three in the morning is a restore.

    The first pass runs immediately so a broken deployment is visible in the log
    within seconds of boot rather than a quarter of an hour later.
    """
    while True:
        try:
            report = await asyncio.to_thread(health_service.report, deep=True)

            if report["status"] != "ok":
                # Logged at error, with the failing companies named. A warning
                # here would sit in a log nobody greps.
                logger.error(
                    "Self-check reported %s: %s",
                    report["status"],
                    {
                        name: check.get("detail")
                        for name, check in report["checks"].items()
                        if check.get("status") != "ok"
                    },
                )
            else:
                logger.info(
                    "Self-check passed in %sms across %s company database(s)",
                    report["duration_ms"],
                    report["checks"]["companies"]["checked"],
                )
        except Exception:
            logger.exception("Self-check failed to run")

        await asyncio.sleep(SELF_CHECK_SECONDS)


async def maintenance_worker() -> None:
    """Housekeeping that would otherwise grow without bound."""
    while True:
        await asyncio.sleep(ATTEMPT_PRUNE_SECONDS)
        try:
            removed = await asyncio.to_thread(auth_service.prune_login_attempts)
            if removed:
                logger.info("Pruned %s expired login attempts", removed)
        except Exception:
            logger.exception("Login attempt pruning failed")

        # Sessions expire but their rows never went anywhere, so the table grew
        # for the lifetime of the installation.
        try:
            removed = await asyncio.to_thread(auth_service.prune_expired_sessions)
            if removed:
                logger.info("Pruned %s expired sessions", removed)
        except Exception:
            logger.exception("Session pruning failed")

        try:
            removed = await asyncio.to_thread(auth_service.prune_password_resets)
            if removed:
                logger.info("Pruned %s spent password reset tokens", removed)
        except Exception:
            logger.exception("Password reset pruning failed")

        # The one place that still opens every company's database on a timer,
        # and the reason the sweeps can stop doing it. It rebuilds the work
        # index from the tenant queues, so an entry lost to a crash between a
        # registration and its commit — or one never written because a company
        # was suspended while it had work outstanding — is bounded by an hour
        # instead of by nothing.
        try:
            summary = await asyncio.to_thread(work_index_service.reconcile_all)
            logger.info(
                "Reconciled the work index: %s of %s company database(s) have "
                "outstanding work, %s could not be read",
                summary["with_work"],
                summary["companies"],
                summary["failed"],
            )
        except Exception:
            logger.exception("Work index reconciliation failed")

        # The activity log has three retentions — a change is kept, a read
        # expires sooner because it is by far the highest volume, and a
        # security event is kept longest because an investigation starts after
        # the damage. Without this sweep the read entries would bury the change
        # entries within a year of ordinary use.
        #
        # Reuses the reconcile's own company list rather than opening every
        # database a second time: it already opened them all in the step above.
        try:
            pruned = await asyncio.to_thread(_prune_activity_logs)
            if pruned:
                logger.info("Pruned %s expired activity log entries", pruned)
        except Exception:
            logger.exception("Activity log pruning failed")


def _prune_activity_logs() -> int:
    """Apply each kind's retention across every company. Returns rows removed.

    A company whose database will not open is skipped rather than aborting the
    sweep: one unreadable file must not stop every other company's log from
    being kept to its retention.

    Diagnostics are swept here too. `DiagnosticsService.RETENTION_DAYS` has
    declared fourteen days since the service was written, and the only thing
    that applied it was a button in the developer console — so for every
    company nobody pressed it for, the retention was a comment. That table is
    the fastest-growing one on the platform: nine `record` calls sit on the
    path of a single inbound message, seven in `smart_reply` and two in
    `inbound`, so a company handling a thousand messages a month accumulates
    tens of thousands of rows a month inside its own encrypted database, for
    ever, from ordinary use rather than from any attack.

    Enforcing the declared window rather than choosing one: the number was
    already decided and written down, and this only makes it true.
    """
    total = 0

    # `list_all_company_ids`, not `list_company_ids`. The active-only list is
    # right for the sweeps that *serve* — a suspended company must not have its
    # replies delivered or its posts published. This sweep does not serve; it is
    # housekeeping, the same category as the health check, which uses the full
    # list for the same reason.
    #
    # Using the active-only list here turned a declared window into "for ever"
    # for any company an operator switched off: fourteen days is a promise about
    # how long data is kept, and suspension is not a reason to stop keeping it.
    # A suspended company still writes diagnostics on every inbound message —
    # including the `ai_reply_skipped` row the suspension gate itself records —
    # so the table went on growing inside a database nobody was pruning.
    for company_id in database_manager.list_all_company_ids():
        removed = activity_service.prune(company_id)
        total += sum(removed.values())

        # Separately guarded: a sweep that fails must not cost the remaining
        # companies their retention, in either direction.
        try:
            total += diagnostics_service.cleanup(company_id=company_id)
        except Exception:
            logger.exception(
                "Diagnostics pruning failed for company %s", company_id
            )

        # One row per customer message, and until now nothing removed any of
        # them. See `NotificationService.RETENTION_DAYS` for why deleting these
        # loses nothing: they point at messages that stay.
        total += notification_service.prune(company_id)

    return total
