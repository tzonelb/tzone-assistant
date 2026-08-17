"""T-ZONE Platform API.

Boot order matters here. The master key is checked before anything else,
because every database in the platform is encrypted and a missing key means the
server cannot read its own data — better to refuse to start with a clear message
than to come up half-working.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import (
    ai_teaching,
    analytics,
    appointments,
    auth,
    catalogue,
    channels,
    comments,
    company_settings,
    conversation_tags,
    conversations,
    customers,
    dashboard,
    developer_center,
    health,
    knowledge,
    manual_messages,
    notifications,
    platform,
    platform_ui,
    roles,
    scheduler,
    team_chat,
    tickets,
)
from backend.api.middleware import SecurityHeadersMiddleware
from backend.security.keyring import KeyringError
from backend.services.auth_service import auth_service
from backend.services.module_access import require_module
from backend.services.conversation_control_service import conversation_control_service
from backend.services.work_index_service import (
    KIND_PENDING_REPLY,
    KIND_SCHEDULED_POST,
    KIND_TAKEOVER,
    work_index_service,
)
from channels.meta import webhook as meta_webhook
from channels.meta.smart_reply import process_due_replies
from channels.post_publisher import publish_due_posts
from channels.webhook_limits import drain as drain_webhook_work
from channels.whatsapp import webhook as whatsapp_webhook
from config.settings import config
from database.manager import database_manager


logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

logger = logging.getLogger("tzone")


TAKEOVER_SWEEP_SECONDS = 10
PENDING_REPLY_SWEEP_SECONDS = 2
SCHEDULED_POST_SWEEP_SECONDS = 30
ATTEMPT_PRUNE_SECONDS = 3600


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        database_manager.master_key()
    except KeyringError as exc:
        logger.error("Refusing to start: %s", exc)
        raise

    # Building the control schema here surfaces a broken database at boot
    # instead of on a customer's first message.
    await asyncio.to_thread(database_manager._ensure_control_schema)

    company_ids = await asyncio.to_thread(database_manager.list_company_ids)
    logger.info(
        "T-ZONE %s starting in %s mode, serving %s company database(s)",
        config.VERSION,
        config.APP_ENV,
        len(company_ids),
    )

    if not company_ids:
        logger.warning(
            "No companies are provisioned. Create one with "
            "`python -m tools.manage_platform create-company`."
        )

    # The sweeps below only open the companies the work index names, so the
    # index has to be true before they start. Rebuilding it here is what makes a
    # queue survive a restart: `pending_replies` is a table precisely so a
    # deploy does not discard the customers still waiting, and an index rebuilt
    # from those tables carries that guarantee forward. It is also the upgrade
    # path — a database written before this index existed arrives with work and
    # no entries.
    try:
        summary = await asyncio.to_thread(work_index_service.reconcile_all)
        logger.info(
            "Work index ready: %s of %s company database(s) have outstanding work",
            summary["with_work"],
            summary["companies"],
        )
        if summary["failed"]:
            logger.warning(
                "%s company database(s) could not be read while building the "
                "work index; their queues will be retried on the next hourly "
                "reconcile",
                summary["failed"],
            )
    except Exception:
        # Deliberately not fatal. A control database that cannot be read has
        # already stopped the boot above; anything failing here is narrower than
        # that, and refusing to serve HTTP because a background index could not
        # be built would turn a delayed reply into a total outage.
        logger.exception("Could not build the work index at startup")

    tasks = [
        asyncio.create_task(takeover_timeout_worker()),
        asyncio.create_task(pending_reply_worker()),
        asyncio.create_task(scheduled_post_worker()),
        asyncio.create_task(maintenance_worker()),
    ]

    try:
        yield
    finally:
        # Webhook deliveries are acknowledged before they are processed, so
        # anything still running is work no provider will send again. Finish it
        # before the sweeps are torn down rather than cancelling it mid-message.
        try:
            await drain_webhook_work()
        except Exception:
            logger.exception("Draining accepted webhook work failed")

        for task in tasks:
            task.cancel()

        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title=config.APP_NAME,
    version=config.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if config.ENABLE_DOCS else None,
    redoc_url="/redoc" if config.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if config.ENABLE_DOCS else None,
)


# Added before CORS so it ends up outermost: every response leaving the
# application carries the headers, including CORS preflights and error
# responses raised inside the stack.
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    # `Retry-After` is not a CORS-safelisted response header, so without this
    # the browser hides it from the application even though the server sent it.
    # The login screen uses it to say how long a lockout has left; withholding
    # it turns a precise answer into "try again later".
    expose_headers=["Retry-After"],
)


# Reachable without a company: the service banner, signing in, the control
# plane, and the customer app asking which modules it may draw.
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(platform.router)
app.include_router(platform_ui.router)

# Everything below is a module the Super Admin can switch off for a company.
# The switch is enforced here rather than in each handler, so a module added to
# the navigation without a gate is visible as a missing line in this block
# rather than as a setting that silently does nothing.
#
# `require_module` runs before the router's own permission checks: being denied
# a module the company does not have should not depend on which permission the
# employee happens to hold.
def _module(key: str) -> list:
    return [Depends(require_module(key))]


app.include_router(dashboard.router, dependencies=_module("dashboard"))
app.include_router(analytics.router, dependencies=_module("analytics"))
app.include_router(ai_teaching.router, dependencies=_module("ai_teaching"))
app.include_router(conversations.router, dependencies=_module("conversations"))
app.include_router(manual_messages.router, dependencies=_module("conversations"))
app.include_router(conversation_tags.router, dependencies=_module("conversations"))
app.include_router(company_settings.router, dependencies=_module("company_settings"))
app.include_router(customers.router, dependencies=_module("customers"))
app.include_router(knowledge.router, dependencies=_module("knowledge"))
app.include_router(channels.router, dependencies=_module("channels"))
app.include_router(catalogue.router, dependencies=_module("catalogue"))
app.include_router(comments.router, dependencies=_module("comments"))
app.include_router(scheduler.router, dependencies=_module("scheduler"))
app.include_router(appointments.router, dependencies=_module("appointments"))
app.include_router(team_chat.router, dependencies=_module("team_chat"))
app.include_router(notifications.router, dependencies=_module("notifications"))
app.include_router(roles.router, dependencies=_module("roles"))
app.include_router(tickets.router, dependencies=_module("tasks"))
app.include_router(tickets.tasks_router, dependencies=_module("tasks"))

app.include_router(developer_center.router)

app.include_router(whatsapp_webhook.router)
app.include_router(meta_webhook.router)


@app.get("/")
def home():
    """Minimal service banner.

    Deliberately does not enumerate routes or report configuration: this is the
    one endpoint reachable without a token, and it used to hand an unauthenticated
    caller a map of the whole API.
    """
    return {
        "service": config.APP_NAME,
        "version": config.VERSION,
        "status": "running",
    }
