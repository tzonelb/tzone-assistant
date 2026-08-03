import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes import (
    activity_log,
    ai_teaching_chat,
    analytics,
    appointments,
    auth,
    broadcasts,
    calls,
    catalogue,
    channel_connections,
    conversations,
    company_settings,
    customers,
    conversation_tags,
    developer_center,
    dashboard,
    facebook_oauth,
    health,
    knowledge_entries,
    departments,
    comments,
    instructions,
    manual_messages,
    media_uploads,
    notifications,
    notification_preferences,
    platform_admin,
    platform_ui,
    roles,
    saved_replies,
    reply_flows,
    scheduled_posts,
    security_verification,
    signup,
    support_tickets,
    tasks,
    team_chat,
    test_whatsapp,
    tickets,
)
from backend.services.auth_service import (
    auth_service,
)
from backend.services.appointment_service import appointment_service
from backend.services.broadcast_service import broadcast_service
from backend.services.media_upload_service import media_upload_service, UPLOAD_ROOT
from backend.services.scheduled_post_service import scheduled_post_service
from backend.services.call_log_service import call_log_service
from backend.services.catalogue_service import catalogue_service
from backend.services.team_chat_service import team_chat_service
from backend.services.team_chat_rooms_service import team_chat_rooms_service
from backend.services.activity_log_service import activity_log_service
from backend.services.ai_teaching_chat_service import ai_teaching_chat_service
from backend.services.conversation_control_service import (
    conversation_control_service,
)
from backend.services.company_settings_service import company_settings_service
from backend.services.customer_service import customer_service
from backend.services.diagnostics_service import diagnostics_service
from backend.services.notification_service import notification_service
from backend.services.notification_preference_service import notification_preference_service
from backend.services.platform_admin_service import platform_admin_service
from backend.services.platform_ui_service import platform_ui_service
from backend.services.saved_reply_service import saved_reply_service
from backend.services.reply_flow_service import reply_flow_service
from core.reply_flow_engine import reply_flow_engine
from backend.services.signup_service import signup_service
from backend.services.license_key_service import license_key_service
from backend.services.comment_service import comment_service
from backend.services.support_ticket_service import support_ticket_service
from backend.services.security_verification_service import security_verification_service
from backend.services.task_service import task_service
from backend.services.facebook_oauth_service import facebook_oauth_service
from core.knowledge_manager import knowledge_manager
from backend.services.department_service import department_service
from core.instruction_service import instruction_service
from backend.services.message_status_service import message_status_service
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


_AUTO_SEND_SKIP_REASON_TEXT = {
    "customer_already_replied": "customer already replied",
    "human_owned": "conversation is currently handled by a human",
    "missing_message_text": "no follow-up message was saved",
    "missing_reminder_set_at": "could not verify it was still safe to send",
}


async def reminder_worker() -> None:
    while True:
        try:
            # Time-based appointment_reminder Reply Flow flows piggyback on
            # this exact same 30s cadence rather than a second worker loop —
            # see reply_flow_engine.check_appointment_reminders for the scan/
            # claim/fire logic.
            reply_flow_engine.check_appointment_reminders()

            fired = conversation_control_service.check_due_reminders()
            for reminder in fired:
                display_name = (
                    reminder.get("official_customer_name")
                    or reminder.get("customer_alias")
                    or f"{reminder['channel']} customer"
                )

                # Additive: build title/body from the new auto-send outcome
                # (sent / skipped-with-reason / failed / not requested) so
                # the notification reflects what actually happened, instead
                # of always showing the same generic "you set a reminder"
                # text. See conversation_control_service.check_due_reminders
                # for how auto_send_status/auto_send_skip_reason are derived.
                auto_send_status = reminder.get("auto_send_status", "not_requested")
                if auto_send_status == "sent":
                    title = f"Auto follow-up sent to {display_name}"
                    body = (
                        reminder.get("reminder_note")
                        or "The pre-authored follow-up message was sent automatically."
                    )
                elif auto_send_status == "skipped":
                    reason_text = _AUTO_SEND_SKIP_REASON_TEXT.get(
                        reminder.get("auto_send_skip_reason"),
                        "the safety checks did not pass",
                    )
                    title = f"Reminder due for {display_name}"
                    body = f"Auto follow-up skipped — {reason_text}."
                elif auto_send_status == "failed":
                    title = f"Reminder due for {display_name}"
                    body = "Auto follow-up failed to send — you may need to follow up manually."
                else:
                    title = f"Follow up with {display_name}"
                    body = reminder.get("reminder_note") or "You set a reminder to follow up on this conversation."

                notification_service.create(
                    company_id=reminder["company_id"],
                    notification_type="conversation_reminder",
                    title=title,
                    body=body,
                    channel=reminder["channel"],
                    external_user_id=reminder["external_user_id"],
                    conversation_id=reminder["id"],
                    severity="info",
                    data={
                        "user_id": reminder.get("reminder_set_by_user_id"),
                        "auto_send_status": auto_send_status,
                        "auto_send_skip_reason": reminder.get("auto_send_skip_reason"),
                    },
                )
        except Exception as exc:
            print("REMINDER WORKER ERROR:", exc)

        await asyncio.sleep(30)


async def scheduled_post_worker() -> None:
    while True:
        try:
            scheduled_post_service.publish_due_posts()
        except Exception as exc:
            print("SCHEDULED POST WORKER ERROR:", exc)

        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()
    customer_service.ensure_schema()
    broadcast_service.ensure_schema()
    ai_teaching_chat_service.ensure_schema()
    diagnostics_service.ensure_schema()
    notification_service.ensure_schema()
    notification_preference_service.ensure_schema()
    security_verification_service.ensure_schema()
    facebook_oauth_service.ensure_schema()
    knowledge_manager.ensure_schema()
    department_service.ensure_schema()
    instruction_service.ensure_schema()
    message_status_service.ensure_schema()
    platform_admin_service.ensure_schema()
    platform_ui_service.ensure_schema()
    saved_reply_service.ensure_schema()
    reply_flow_service.ensure_schema()
    reply_flow_engine.ensure_schema()
    signup_service.ensure_schema()
    license_key_service.ensure_schema()
    comment_service.ensure_schema()
    support_ticket_service.ensure_schema()
    task_service.ensure_schema()
    catalogue_service.ensure_schema()
    call_log_service.ensure_schema()
    team_chat_service.ensure_schema()
    team_chat_rooms_service.ensure_schema()
    activity_log_service.ensure_schema()
    appointment_service.ensure_schema()
    media_upload_service.ensure_storage()
    scheduled_post_service.ensure_schema()

    timeout_task = asyncio.create_task(
        takeover_timeout_worker()
    )
    reminder_task = asyncio.create_task(
        reminder_worker()
    )
    scheduled_post_task = asyncio.create_task(
        scheduled_post_worker()
    )

    try:
        from channels.telegram import manager as telegram_manager
        telegram_manager.start_all_connected_bots()
        if not telegram_manager._running_bots:
            # No company has connected a Telegram bot yet — fall back to
            # the legacy single .env-configured bot if one is set, so
            # existing single-tenant setups keep working unchanged.
            from channels.telegram.bot import build_telegram_application
            legacy_app = build_telegram_application()
            telegram_manager.start_bot(
                account_id=-1, company_id=config.DEFAULT_COMPANY_ID,
                bot_token=legacy_app.bot.token,
            )
    except Exception as exc:
        print(
            "TELEGRAM BOT(S) DISABLED (this does not affect other channels):",
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

        reminder_task.cancel()
        with suppress(asyncio.CancelledError):
            await reminder_task

        scheduled_post_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduled_post_task

        from channels.telegram import manager as telegram_manager
        await telegram_manager.stop_all()


app = FastAPI(
    title=config.APP_NAME,
    version=config.VERSION,
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    # The Windows desktop app serves the bundled frontend from a local
    # loopback port that can vary between machines, so any loopback origin
    # must be accepted alongside the fixed list above. Auth uses Bearer
    # tokens (not cookies), so this does not widen credential exposure.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
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


# Paths reachable even by a company whose subscription has expired — the
# frontend needs these to show "your access is locked, renew to continue"
# instead of failing opaquely: auth (login/me/logout), billing/plans (so
# they can actually renew), support tickets (so they can ask for help),
# signup for a NEW company, the public webhook receivers, and docs.
_SUBSCRIPTION_LOCK_EXEMPT_PREFIXES = (
    "/api/auth",
    "/api/signup",
    "/api/platform/my-subscription",
    "/api/platform/plans-catalog",
    "/api/platform/subscription-requests",
    "/api/support-tickets",
    "/webhook",
    "/uploads",
    "/docs",
    "/openapi.json",
    "/redoc",
)


@app.middleware("http")
async def enforce_subscription_lock(request: Request, call_next):
    """Any company whose subscription (including its grace period) has
    expired with no renewal is locked out of the platform — this is the
    real enforcement behind the trial/expiry model, not just a number shown
    on a settings page. Super admins are never locked (they manage/unlock
    companies). Requests with no/invalid token pass through untouched —
    normal auth handling downstream returns the correct 401."""
    path = request.url.path
    if any(path.startswith(prefix) for prefix in _SUBSCRIPTION_LOCK_EXEMPT_PREFIXES):
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return await call_next(request)

    token = auth_header[7:].strip()
    user = auth_service.get_user_from_token(token)
    if not user or user.get("is_super_admin"):
        return await call_next(request)

    company_id = user.get("active_company_id")
    if company_id and platform_admin_service.is_company_locked(company_id=company_id):
        return JSONResponse(
            status_code=402,
            content={
                "detail": "Your subscription has expired. Please renew your plan to continue.",
                "subscription_locked": True,
            },
        )

    return await call_next(request)


UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_ROOT)), name="uploads")

app.include_router(health.router)
app.include_router(media_uploads.router)
app.include_router(tickets.router)
app.include_router(knowledge_entries.router)
app.include_router(ai_teaching_chat.router)
app.include_router(departments.router)
app.include_router(instructions.router)
app.include_router(test_whatsapp.router)
app.include_router(auth.router)
app.include_router(signup.router)
app.include_router(comments.router)
app.include_router(dashboard.router)
app.include_router(conversations.router)
app.include_router(company_settings.router)
app.include_router(customers.router)
app.include_router(customers.segments_router)
app.include_router(broadcasts.router)
app.include_router(analytics.router)
app.include_router(conversation_tags.router)
app.include_router(developer_center.router)
app.include_router(notifications.router)
app.include_router(notification_preferences.router)
app.include_router(manual_messages.router)
app.include_router(roles.router)
app.include_router(platform_admin.router)
app.include_router(platform_ui.router)
app.include_router(channel_connections.router)
app.include_router(facebook_oauth.router)
app.include_router(saved_replies.router)
app.include_router(reply_flows.router)
app.include_router(support_tickets.router)
app.include_router(security_verification.router)
app.include_router(tasks.router)
app.include_router(catalogue.router)
app.include_router(calls.router)
app.include_router(team_chat.router)
app.include_router(activity_log.router)
app.include_router(appointments.router)
app.include_router(scheduled_posts.router)

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