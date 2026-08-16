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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import (
    analytics,
    auth,
    channels,
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
    roles,
    tickets,
)
from backend.security.keyring import KeyringError
from backend.services.auth_service import auth_service
from backend.services.conversation_control_service import conversation_control_service
from channels.meta import webhook as meta_webhook
from channels.meta.smart_reply import process_due_replies
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
ATTEMPT_PRUNE_SECONDS = 3600


async def _run_for_every_company(label: str, work) -> None:
    """Run one unit of work per company, isolating failures between them.

    A company whose database cannot be opened must not stop the others from
    being served.
    """
    try:
        company_ids = await asyncio.to_thread(database_manager.list_company_ids)
    except Exception:
        logger.exception("%s: could not list companies", label)
        return

    for company_id in company_ids:
        try:
            await asyncio.to_thread(work, company_id)
        except Exception:
            logger.exception("%s failed for company %s", label, company_id)


async def takeover_timeout_worker() -> None:
    """Return conversations to the assistant when a takeover lapses."""
    while True:
        await _run_for_every_company(
            "takeover sweep",
            conversation_control_service.expire_overdue_takeovers,
        )
        await asyncio.sleep(TAKEOVER_SWEEP_SECONDS)


async def pending_reply_worker() -> None:
    """Deliver assistant replies whose collection window has closed."""
    while True:
        await _run_for_every_company("assistant reply sweep", process_due_replies)
        await asyncio.sleep(PENDING_REPLY_SWEEP_SECONDS)


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

    tasks = [
        asyncio.create_task(takeover_timeout_worker()),
        asyncio.create_task(pending_reply_worker()),
        asyncio.create_task(maintenance_worker()),
    ]

    try:
        yield
    finally:
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(analytics.router)
app.include_router(conversations.router)
app.include_router(manual_messages.router)
app.include_router(conversation_tags.router)
app.include_router(company_settings.router)
app.include_router(customers.router)
app.include_router(knowledge.router)
app.include_router(channels.router)
app.include_router(notifications.router)
app.include_router(roles.router)
app.include_router(tickets.router)
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
