import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import (
    analytics,
    appointments,
    auth,
    broadcasts,
    catalogue,
    channel_oauth,
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
    roles,
    scheduler,
    tasks,
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

    try:
        yield
    finally:
        timeout_task.cancel()

        with suppress(
            asyncio.CancelledError
        ):
            await timeout_task


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
app.include_router(broadcasts.router)
app.include_router(channel_oauth.router)
app.include_router(analytics.router)
app.include_router(tasks.router)
app.include_router(catalogue.router)
app.include_router(appointments.router)
app.include_router(scheduler.router)

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