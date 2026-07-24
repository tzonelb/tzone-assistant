import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import (
    auth,
    conversations,
    company_settings,
    customers,
    conversation_tags,
    developer_center,
    dashboard,
    health,
    knowledge,
    manual_messages,
    notifications,
    platform_admin,
    roles,
    test_whatsapp,
    tickets,
)
from backend.services.auth_service import (
    auth_service,
)
from backend.services.conversation_control_service import (
    conversation_control_service,
)
from backend.services.company_settings_service import company_settings_service
from backend.services.customer_service import customer_service
from backend.services.diagnostics_service import diagnostics_service
from backend.services.notification_service import notification_service
from channels.meta import (
    webhook as meta_webhook,
)
from channels.whatsapp import (
    webhook as whatsapp_webhook,
)
from config.settings import config
from database.database import db


async def takeover_timeout_worker() -> None:
    while True:
        try:
            conversation_control_service.expire_overdue_takeovers()
        except Exception as exc:
            print(
                "TAKEOVER TIMEOUT WORKER ERROR:",
                exc,
            )

        await asyncio.sleep(10)


async def run_telegram_bot(telegram_app) -> None:
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()
    try:
        # Keep this task alive until it's cancelled at shutdown; the
        # actual polling loop runs inside telegram_app.updater, this just
        # holds the asyncio task open so lifespan() can cancel it cleanly.
        await asyncio.Event().wait()
    finally:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()
    customer_service.ensure_schema()
    diagnostics_service.ensure_schema()
    notification_service.ensure_schema()

    timeout_task = asyncio.create_task(
        takeover_timeout_worker()
    )

    telegram_task = None
    try:
        from channels.telegram.bot import build_telegram_application
        telegram_application = build_telegram_application()
        telegram_task = asyncio.create_task(
            run_telegram_bot(telegram_application)
        )
    except Exception as exc:
        print(
            "TELEGRAM BOT DISABLED (this does not affect other channels):",
            exc,
        )

    try:
        yield
    finally:
        timeout_task.cancel()

        with suppress(
            asyncio.CancelledError
        ):
            await timeout_task

        if telegram_task is not None:
            telegram_task.cancel()
            with suppress(asyncio.CancelledError):
                await telegram_task


app = FastAPI(
    title=config.APP_NAME,
    version=config.VERSION,
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
    ],
)


app.include_router(health.router)
app.include_router(tickets.router)
app.include_router(knowledge.router)
app.include_router(test_whatsapp.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(conversations.router)
app.include_router(company_settings.router)
app.include_router(customers.router)
app.include_router(conversation_tags.router)
app.include_router(developer_center.router)
app.include_router(notifications.router)
app.include_router(manual_messages.router)
app.include_router(roles.router)
app.include_router(platform_admin.router)

app.include_router(
    whatsapp_webhook.router
)
app.include_router(
    meta_webhook.router
)


@app.get("/")
def home():
    return {
        "app": config.APP_NAME,
        "status": "running",
        "version": config.VERSION,
        "environment":
            config.APP_ENV,
        "database": "connected",
        "takeover_timeout_worker":
            "running",
        "dashboard_api": {
            "login":
                "/api/auth/login",
            "current_user":
                "/api/auth/me",
            "summary":
                "/api/dashboard/summary",
            "conversations":
                "/conversations/",
            "manual_reply": (
                "/conversations/"
                "{channel}/{user_id}/reply"
            ),
        },
        "documentation": "/docs",
        "webhooks": [
            "/webhook/whatsapp/",
            "/webhook/meta/",
            "/webhook/messenger/",
            "/webhook/instagram/",
        ],
    }