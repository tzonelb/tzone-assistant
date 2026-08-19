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
    activity,
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
from backend.api.middleware import (
    SecurityHeadersMiddleware,
    SessionCookieMiddleware,
)
from backend.security.keyring import KeyringError
from backend.services.module_access import (
    require_active_subscription,
    require_module,
)
from backend.services.work_index_service import work_index_service
from channels.meta import webhook as meta_webhook
from channels.webhook_limits import drain as drain_webhook_work
from channels.telegram import webhook as telegram_webhook
from channels.whatsapp import webhook as whatsapp_webhook
from config.settings import config
from database.manager import database_manager


logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

logger = logging.getLogger("tzone")


# The background jobs live in `backend/workers.py`. Imported by name rather
# than with a star, so this file lists in one place exactly what the process
# runs on a timer — and so that removing a worker breaks the import here
# instead of leaving a schedule that starts nothing.
from backend.workers import (  # noqa: E402
    maintenance_worker,
    pending_reply_worker,
    scheduled_post_worker,
    self_check_worker,
    takeover_timeout_worker,
)


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
    # Bring any company behind the current schema up to it, before the sweeps
    # start reading their tables. `upgrade_all_tenants` existed and had no
    # callers anywhere — not even at boot — so a release that added a column
    # left every existing company failing at query time until somebody
    # remembered to run the CLI.
    #
    # Only the outdated ones are opened: the recorded version is one cheap read
    # of the control plane, so a release that changed nothing opens nothing,
    # and a thousand companies do not cost a thousand decryptions at every boot.
    try:
        upgraded = await asyncio.to_thread(database_manager.upgrade_outdated_tenants)

        if upgraded:
            logger.info(
                "Upgraded the schema of %s company database(s): %s",
                len(upgraded),
                {company: changes for company, changes in upgraded.items() if changes},
            )
    except Exception:
        logger.exception("Schema upgrade at startup failed")

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
        asyncio.create_task(self_check_worker()),
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

# Added after CORS so it ends up *inside* it: a preflight must be answered by
# CORS before this ever sees a request, and a browser that is refused at the
# preflight never sends the real one — so a CSRF refusal here would be reported
# to the page as a network failure with no explanation.
app.add_middleware(SessionCookieMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # `X-CSRF-Token` has to be allowed explicitly: it is not a CORS-safelisted
    # request header, so without it the browser refuses the preflight and every
    # cookie-authenticated write fails before it is sent.
    allow_headers=["Authorization", "Content-Type", "Accept", "X-CSRF-Token"],
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
    """The module switch, plus the bill.

    Both gates go on every module router, in one helper, so the two cannot
    drift apart and a router added later cannot pick up one and miss the other.
    They answer different questions and both have to pass: `require_module` is
    the operator deciding this company does not have Catalogue;
    `require_active_subscription` is this company not having paid for any of it.

    Order matters a little. The module gate runs first, so a company that never
    had a module is told that rather than told to pay for one it does not have.
    """
    return [
        Depends(require_module(key)),
        Depends(require_active_subscription),
    ]


def _module_unpaid_too(key: str) -> list:
    """The module switch alone — for the screens a lapsed company must keep.

    Exactly one router is registered this way. The dashboard carries
    `/api/dashboard/subscription`, which is where an owner finds out the
    workspace is paused and what to do about it. Pausing the screen that
    explains the pause would make the pause unactionable, and the point of
    stopping the service is to prompt an action rather than to hide from it.
    """
    return [Depends(require_module(key))]


app.include_router(dashboard.router, dependencies=_module_unpaid_too("dashboard"))
app.include_router(analytics.router, dependencies=_module("analytics"))
app.include_router(ai_teaching.router, dependencies=_module("ai_teaching"))
app.include_router(conversations.router, dependencies=_module("conversations"))
app.include_router(manual_messages.router, dependencies=_module("conversations"))
app.include_router(conversation_tags.router, dependencies=_module("conversations"))
app.include_router(company_settings.router, dependencies=_module("company_settings"))
# The activity log rides with company_settings: it is read by the same
# people, from the same screen area, under the same permission.
app.include_router(activity.router, dependencies=_module("company_settings"))
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
app.include_router(telegram_webhook.router)


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
