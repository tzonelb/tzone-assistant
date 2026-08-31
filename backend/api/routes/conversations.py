"""The shared inbox.

Everything here reads from the company's own encrypted database. The previous
implementation scanned a shared folder of ``.jsonl`` files on every request,
which is why one company could read another's conversations and why the live
stream degraded as the archive grew.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from starlette.concurrency import run_in_threadpool

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:  # Optional Arabic PDF shaping.
    arabic_reshaper = None
    get_display = None

from backend.services.activity_service import Action, activity_service
from backend.services.conversation_reminder_service import (
    ReminderError,
    conversation_reminder_service,
)
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
from backend.services.conversation_control_service import (
    ConversationOwnershipConflict,
    conversation_control_service,
)
from backend.services.business_department_service import business_department_service
from backend.services.channel_account_service import SUPPORTED_CHANNELS
from backend.services.message_service import message_service
from backend.services.stream_access import may_continue
from database.manager import DatabaseError, database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["Conversations"])


# What the column holds when a conversation belongs to no section. Not a
# department, so it is not looked for in the company's list — it is the absence
# of one, and it is what the inbox has always rendered.
UNASSIGNED = "Unassigned"

LIVE_POLL_SECONDS = 2


class ConversationControlUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    department: str | None = None
    assigned_user_id: int | None = None
    customer_alias: str | None = Field(default=None, max_length=120)
    folder: str | None = None
    is_starred: bool | None = None
    is_pinned: bool | None = None
    tags: list[str] | None = None
    clear_assignment: bool | None = None
    is_unread: bool | None = None


class ConversationReminderRequest(BaseModel):
    """A follow-up on one conversation.

    `message_text` is only meaningful with `auto_send`; the service refuses the
    combination that promises to send something and carries nothing to send.
    """

    reminder_at: str = Field(min_length=4, max_length=64)
    note: str | None = Field(default=None, max_length=500)
    auto_send: bool = False
    message_text: str | None = Field(default=None, max_length=4000)


class ConversationNoteCreate(BaseModel):
    note: str = Field(min_length=1, max_length=4000)
    # Who the note is for. The picker in the composer sends the ids of the
    # colleagues it offered; the service checks every one of them against this
    # company's own directory before it stores or notifies anything, so an id
    # from another company that was typed into the payload by hand names
    # nobody. Bounded so a payload cannot ask for an unbounded fan-out of
    # notifications.
    mentioned_user_ids: list[int] = Field(default_factory=list, max_length=100)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _pdf_display_text(value: Any) -> str:
    text = str(value or "")

    if arabic_reshaper is not None and get_display is not None:
        try:
            return get_display(arabic_reshaper.reshape(text))
        except Exception:  # noqa: BLE001
            return text

    return text


def _department_options(company_id: int) -> list[dict[str, str]]:
    """The sections this company actually defined, plus "no section yet".

    This endpoint used to serve a Title-Case constant — Sales, IPTV, Support,
    Accounting, Maintenance, Orders, Information — hardcoded in this file. It
    was one company's list shown to every company's employees, in a casing that
    matched nothing: the assistant routes on ``business_departments.code``,
    which is lower_snake, so a conversation the model put in ``sales`` and a
    conversation an employee put in ``Sales`` were two different departments as
    far as the inbox filter was concerned. Everything now speaks the code.

    Never raises: a department table that will not open costs the inbox its
    transfer list, not the employee their inbox.
    """
    options = [{"code": UNASSIGNED, "label": UNASSIGNED}]

    try:
        rows = business_department_service.list_departments(
            company_id=company_id,
            enabled_only=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not read the departments of company %s", company_id
        )
        return options

    options.extend(
        {
            "code": str(row["code"]),
            "label": str(
                row.get("name_en") or row.get("name_ar") or row["code"]
            ),
        }
        for row in rows
        if row.get("code")
    )

    return options


def _department_codes(company_id: int) -> list[str]:
    return [option["code"] for option in _department_options(company_id)]


def _is_conversation_admin(current_user: dict[str, Any], company_id: int) -> bool:
    return auth_service.has_permission(
        user_id=int(current_user["id"]),
        company_id=company_id,
        permission_code="conversations.manage",
        is_super_admin=bool(current_user.get("is_super_admin")),
    )


def _employees(current_user: dict[str, Any], company_id: int) -> list[dict[str, Any]]:
    """The assignment list, carrying contact details only when allowed.

    The inbox needs a name and an id to fill a dropdown. It used to receive
    every colleague's email, phone, role and branch as well — to anyone holding
    `conversations.view`, the lowest permission there is — and rendered none of
    it. Whether employees may see each other's contact details is the company
    owner's call, expressed by who they give `users.view` to.
    """
    return auth_service.company_employees(
        company_id,
        include_contact_details=auth_service.has_permission(
            user_id=int(current_user["id"]),
            company_id=company_id,
            permission_code="users.view",
            is_super_admin=bool(current_user.get("is_super_admin")),
        ),
    )


def _owner_detail(
    company_id: int,
    owner_user_id: int | None,
    fallback: str,
) -> dict[str, Any]:
    names = auth_service.user_display_names(
        company_id, [owner_user_id] if owner_user_id else []
    )
    owner_name = names.get(int(owner_user_id)) if owner_user_id else None

    return {
        "code": "conversation_owned",
        "message": (
            f"Conversation is assigned to {owner_name}." if owner_name else fallback
        ),
        "owner_user_id": owner_user_id,
        "owner_user_name": owner_name,
    }


def _ownership_conflict(
    company_id: int,
    exc: ConversationOwnershipConflict,
    fallback: str,
) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=_owner_detail(company_id, exc.owner_user_id, fallback),
    )


def _assert_can_control(
    *,
    current_user: dict[str, Any],
    company_id: int,
    channel: str,
    external_user_id: str,
) -> tuple[dict[str, Any], bool]:
    state = conversation_control_service.get_state(
        company_id=company_id,
        channel=channel,
        external_user_id=external_user_id,
    )
    is_admin = _is_conversation_admin(current_user, company_id)
    owner_user_id = state.get("assigned_user_id")
    is_owner = (
        owner_user_id is not None
        and int(owner_user_id) == int(current_user["id"])
    )

    if not is_admin and not is_owner:
        raise HTTPException(
            status_code=409,
            detail=_owner_detail(
                company_id,
                int(owner_user_id) if owner_user_id is not None else None,
                "Take over this conversation before changing it.",
            ),
        )

    return state, is_admin


def _decorate_rows(
    company_id: int,
    rows: list[dict[str, Any]],
    current_user: dict[str, Any],
    is_admin: bool,
) -> list[dict[str, Any]]:
    """Attach employee names and per-row capability flags.

    Names come from the control-plane database in one query for the whole page,
    because conversations and users no longer live in the same file.
    """
    current_user_id = int(current_user["id"])
    names = auth_service.user_display_names(
        company_id, [row.get("assigned_user_id") for row in rows]
    )

    for row in rows:
        owner_id = row.get("assigned_user_id")
        is_owner = owner_id is not None and int(owner_id) == current_user_id

        row["assigned_user_name"] = (
            names.get(int(owner_id)) if owner_id is not None else None
        )
        row["can_manage"] = bool(is_admin or is_owner)
        row["is_assigned_to_me"] = bool(is_owner)

    return rows


def _export_filename(channel: str, user_id: str, scope: str, extension: str) -> str:
    safe_channel = str(channel).replace("/", "_")
    safe_user_id = str(user_id).replace("/", "_")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{safe_channel}_{safe_user_id}_{scope}_{timestamp}.{extension}"


# Cells beginning with one of these are read as a formula by Excel, LibreOffice
# and Google Sheets when the exported file is opened. A customer's message body
# and their channel display name both reach the CSV verbatim, so a customer who
# writes `=HYPERLINK(...)` or `=cmd|'/C calc'!A0` turns an employee's export into
# a live exploit on the employee's machine. Prefixing a single quote makes the
# spreadsheet treat the cell as text; it changes nothing about how the value
# reads for a human and is the standard neutralisation.
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")


def _csv_cell(value: object) -> object:
    if isinstance(value, str) and value[:1] in _CSV_FORMULA_TRIGGERS:
        return "'" + value
    return value


# ----------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------


@router.get("/")
def list_conversations(
    search: str = Query(default="", max_length=200),
    channel: str = Query(default="all", max_length=50),
    status: str = Query(default="all", max_length=50),
    department: str = Query(default="all", max_length=100),
    assigned_user_id: int | None = Query(default=None),
    folder: str = Query(default="inbox", max_length=30),
    tag: str = Query(default="", max_length=50),
    read_status: str = Query(default="all", max_length=20),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    company_id = auth_service.resolve_company_id(current_user)
    is_admin = _is_conversation_admin(current_user, company_id)

    result = message_service.list_conversations(
        company_id=company_id,
        search=search,
        channel=channel,
        status=status,
        department=department,
        assigned_user_id=assigned_user_id,
        folder=folder,
        tag=tag,
        read_status=read_status,
        current_user_id=int(current_user["id"]),
        page=page,
        page_size=page_size,
    )

    return {
        "status": "ok",
        "items": _decorate_rows(
            company_id, result["items"], current_user, is_admin
        ),
        "channel_counts": result["channel_counts"],
        "available_channels": result["available_channels"],
        # The whole catalogue, not just what this company has connected. The
        # inbox draws a tab per channel and greys out the ones with no account,
        # so it needs both lists. It used to hold its own hardcoded copy, which
        # included `website` — a channel the platform has never supported and
        # cannot be connected from the Channels screen, shown to every company
        # with the tooltip "Website is not connected yet". Not connected, and
        # not connectable.
        "supported_channels": list(SUPPORTED_CHANNELS),
        "current_user_id": int(current_user["id"]),
        "current_user_is_admin": is_admin,
        # `departments` stays a list of plain codes, which is what the screen
        # already binds a filter value to; `department_options` carries the name
        # the company gave each one, for the label.
        "departments": _department_codes(company_id),
        "department_options": _department_options(company_id),
        "employees": _employees(current_user, company_id),
        "pagination": result["pagination"],
    }


@router.get("/options")
def conversation_options(
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    return {
        "status": "ok",
        "departments": _department_codes(company_id),
        "department_options": _department_options(company_id),
        "employees": _employees(current_user, company_id),
    }


@router.get("/live/events")
async def live_conversation_events(
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    """Push inbox changes to an open dashboard.

    The poll compares a cheap aggregate signature and only builds a full page
    when something actually changed. Every database call runs in a worker
    thread, because blocking here stalls every other request on the server.
    """
    company_id = auth_service.resolve_company_id(current_user)
    is_admin = _is_conversation_admin(current_user, company_id)

    async def event_stream():
        last_signature = ""

        while True:
            # Re-checked every pass, not only when the connection opened.
            # See `backend/services/stream_access.py`: a dependency runs once,
            # and this loop outlives it by hours.
            if not await run_in_threadpool(may_continue, current_user):
                yield "event: access_ended\ndata: {}\n\n"

                return

            try:
                signature = await run_in_threadpool(
                    message_service.live_signature, company_id
                )
            except Exception:  # noqa: BLE001
                logger.exception("Live inbox signature failed")
                yield ": error\n\n"
                await asyncio.sleep(LIVE_POLL_SECONDS)
                continue

            if signature != last_signature:
                last_signature = signature

                result = await run_in_threadpool(
                    lambda: message_service.list_conversations(
                        company_id=company_id,
                        folder="all",
                        current_user_id=int(current_user["id"]),
                        page=1,
                        page_size=100,
                    )
                )

                items = await run_in_threadpool(
                    _decorate_rows,
                    company_id,
                    result["items"],
                    current_user,
                    is_admin,
                )

                payload = {
                    "type": "conversations_updated",
                    "items": items,
                    "channel_counts": result["channel_counts"],
                    "available_channels": result["available_channels"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                yield (
                    "event: conversations_updated\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(LIVE_POLL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ----------------------------------------------------------------------
# One conversation
# ----------------------------------------------------------------------


@router.get("/{channel}/{user_id}")
def read_conversation(
    channel: str,
    user_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    # An open screen re-reads this route every few seconds to stay current. That
    # refresh is not somebody opening the conversation, and treating it as one
    # made "mark as unread" impossible: the next poll marked it read again.
    mark_read: bool = Query(default=True),
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    messages = message_service.list_messages(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        limit=limit,
    )

    if not messages:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    if mark_read:
        conversation_control_service.record_opened(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            actor_user_id=int(current_user["id"]),
        )

    # Reading is recorded, not only writing. A customer's conversation holds
    # what they told this company in confidence, and who read it is a fact the
    # owner is answerable for even when nothing was changed. `kind="read"`
    # carries its own retention — 90 days against 730 for changes — so the
    # volume of ordinary work does not bury the record of who touched what.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.CONVERSATION_OPENED,
        category="conversations",
        kind="read",
        target_type="conversation",
        target_id=f"{channel}:{user_id}",
        summary=f"Opened a {channel} conversation",
        ip_address=client_ip(request),
    )

    return {
        "status": "ok",
        "channel": channel,
        "user_id": user_id,
        "messages": messages,
    }


@router.get("/{channel}/{user_id}/control")
def read_control(
    channel: str,
    user_id: str,
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    result = conversation_control_service.timeline(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    conversation = result.get("conversation", {})
    assigned_user_id = conversation.get("assigned_user_id")

    names = auth_service.user_display_names(
        company_id, [assigned_user_id] if assigned_user_id else []
    )
    conversation["assigned_user_name"] = (
        names.get(int(assigned_user_id)) if assigned_user_id else None
    )

    current_user_id = int(current_user["id"])
    is_admin = _is_conversation_admin(current_user, company_id)
    is_owner = (
        assigned_user_id is not None and int(assigned_user_id) == current_user_id
    )
    can_reply_permission = auth_service.has_permission(
        user_id=current_user_id,
        company_id=company_id,
        permission_code="conversations.reply",
        is_super_admin=bool(current_user.get("is_super_admin")),
    )

    result["employees"] = _employees(current_user, company_id)
    result["departments"] = _department_codes(company_id)
    result["department_options"] = _department_options(company_id)
    result["current_user_id"] = current_user_id
    result["current_user_is_admin"] = is_admin
    result["permissions"] = {
        "can_reply": bool(
            can_reply_permission
            and is_owner
            and not conversation.get("handled_by_ai", True)
        ),
        "can_manage": bool(is_admin or is_owner),
        "can_mark_read": bool(is_admin or is_owner),
        "can_take_over": bool(
            can_reply_permission and (assigned_user_id is None or is_owner)
        ),
    }

    return result


@router.post("/{channel}/{user_id}/take-over")
def take_over(
    channel: str,
    user_id: str,
    current_user: dict[str, Any] = Depends(require_permission("conversations.reply")),
):
    company_id = auth_service.resolve_company_id(current_user)

    try:
        conversation = conversation_control_service.set_ai_mode(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            handled_by_ai=False,
            actor_user_id=int(current_user["id"]),
        )
    except ConversationOwnershipConflict as exc:
        raise _ownership_conflict(
            company_id, exc, "Conversation is currently owned by another employee."
        ) from exc

    names = auth_service.user_display_names(
        company_id, [conversation.get("assigned_user_id")]
    )
    conversation["assigned_user_name"] = names.get(
        int(conversation["assigned_user_id"])
        if conversation.get("assigned_user_id")
        else 0
    )

    return {"status": "ok", "conversation": conversation}


@router.post("/{channel}/{user_id}/release")
def release_conversation(
    channel: str,
    user_id: str,
    current_user: dict[str, Any] = Depends(require_permission("conversations.reply")),
):
    company_id = auth_service.resolve_company_id(current_user)
    is_admin = _is_conversation_admin(current_user, company_id)

    try:
        conversation = conversation_control_service.release(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            actor_user_id=int(current_user["id"]),
            force=is_admin,
        )
    except ConversationOwnershipConflict as exc:
        raise _ownership_conflict(
            company_id, exc, "Only the assigned employee can release this conversation."
        ) from exc

    conversation["assigned_user_name"] = None
    return {"status": "ok", "conversation": conversation}


@router.post("/{channel}/{user_id}/return-to-ai")
def return_to_ai(
    channel: str,
    user_id: str,
    current_user: dict[str, Any] = Depends(require_permission("conversations.reply")),
):
    company_id = auth_service.resolve_company_id(current_user)

    _assert_can_control(
        current_user=current_user,
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    conversation = conversation_control_service.set_ai_mode(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        handled_by_ai=True,
        actor_user_id=int(current_user["id"]),
    )

    return {"status": "ok", "conversation": conversation}


@router.patch("/{channel}/{user_id}/control")
def update_control(
    channel: str,
    user_id: str,
    payload: ConversationControlUpdate,
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    _state, is_admin = _assert_can_control(
        current_user=current_user,
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    # Validated against this company's own sections. The constant this replaced
    # accepted "Sales" from any company and rejected a code the company itself
    # had defined, so the one screen an employee uses to transfer a conversation
    # could not name most of the places it could be transferred to.
    if (
        payload.department is not None
        and payload.department not in _department_codes(company_id)
    ):
        raise HTTPException(
            status_code=422,
            detail="That department does not belong to this company.",
        )

    if payload.assigned_user_id is not None:
        # Server-side validation, not a response: ids are all this needs, so it
        # skips the contact-detail permission check rather than paying for it.
        employee_ids = {
            employee["id"]
            for employee in auth_service.company_employees(company_id)
        }

        if payload.assigned_user_id not in employee_ids:
            raise HTTPException(
                status_code=422,
                detail="Employee does not belong to this company.",
            )

    try:
        conversation_control_service.update_state(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            actor_user_id=int(current_user["id"]),
            status=payload.status,
            priority=payload.priority,
            department=payload.department,
            assigned_user_id=payload.assigned_user_id,
            is_admin=is_admin,
        )
    except ConversationOwnershipConflict as exc:
        raise _ownership_conflict(
            company_id, exc, "Conversation is currently owned by another employee."
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    conversation = conversation_control_service.update_workspace_state(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        actor_user_id=int(current_user["id"]),
        customer_alias=payload.customer_alias,
        folder=payload.folder,
        is_starred=payload.is_starred,
        is_pinned=payload.is_pinned,
        tags=payload.tags,
        clear_assignment=payload.clear_assignment,
        is_unread=payload.is_unread,
    )

    assigned_user_id = conversation.get("assigned_user_id")
    names = auth_service.user_display_names(
        company_id, [assigned_user_id] if assigned_user_id else []
    )
    conversation["assigned_user_name"] = (
        names.get(int(assigned_user_id)) if assigned_user_id else None
    )

    return {"status": "ok", "conversation": conversation}


@router.post("/{channel}/{user_id}/reminder")
def set_conversation_reminder(
    channel: str,
    user_id: str,
    payload: ConversationReminderRequest,
    current_user: dict[str, Any] = Depends(require_permission("conversations.reply")),
):
    """Come back to this conversation at a time, optionally sending a message.

    Takes `conversations.reply` rather than `conversations.view`: a reminder can
    carry a message the platform sends to the customer on the employee's behalf,
    which is a reply scheduled rather than typed.
    """
    company_id = auth_service.resolve_company_id(current_user)

    _assert_can_control(
        current_user=current_user,
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    try:
        reminder = conversation_reminder_service.set(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            remind_at=payload.reminder_at,
            note=payload.note,
            auto_send=payload.auto_send,
            message_text=payload.message_text,
            created_by_user_id=int(current_user["id"]),
        )
    except ReminderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return reminder


@router.delete("/{channel}/{user_id}/reminder")
def clear_conversation_reminder(
    channel: str,
    user_id: str,
    current_user: dict[str, Any] = Depends(require_permission("conversations.reply")),
):
    company_id = auth_service.resolve_company_id(current_user)

    _assert_can_control(
        current_user=current_user,
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    cleared = conversation_reminder_service.clear(
        company_id=company_id, channel=channel, external_user_id=user_id
    )

    return {"success": True, "cleared": cleared}


@router.post("/{channel}/{user_id}/notes")
def add_note(
    channel: str,
    user_id: str,
    payload: ConversationNoteCreate,
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    _assert_can_control(
        current_user=current_user,
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    try:
        note = conversation_control_service.add_note(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            author_user_id=int(current_user["id"]),
            note=payload.note,
            mentioned_user_ids=payload.mentioned_user_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"status": "ok", "note": note}


# ----------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------


def _company_name(company_id: int) -> str:
    """This company's own name, for anything a customer might read."""
    try:
        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT name FROM companies WHERE id = ? LIMIT 1", (int(company_id),)
            ).fetchone()
    except DatabaseError:
        return "Conversation"

    return str(row["name"]) if row and row["name"] else "Conversation"


@router.get("/{channel}/{user_id}/export")
def export_conversation(
    channel: str,
    user_id: str,
    request: Request,
    scope: Literal["chat", "timeline", "full"] = Query(default="full"),
    file_format: Literal["json", "csv", "txt", "pdf"] = Query(
        default="json", alias="format"
    ),
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    # An export is the one read that leaves the platform. Whatever the file is
    # used for afterwards, the record that it was taken — by whom, of which
    # conversation, in what format — has to survive here.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.CONVERSATION_EXPORTED,
        category="conversations",
        kind="read",
        target_type="conversation",
        target_id=f"{channel}:{user_id}",
        summary=f"Exported a {channel} conversation as {file_format}",
        severity="notice",
        after={"scope": scope, "format": file_format},
        ip_address=client_ip(request),
    )

    messages = message_service.list_messages(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        limit=500,
    )

    timeline_result = conversation_control_service.timeline(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    if timeline_result is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    conversation = timeline_result.get("conversation", {})
    assigned_user_id = conversation.get("assigned_user_id")
    names = auth_service.user_display_names(
        company_id, [assigned_user_id] if assigned_user_id else []
    )
    conversation["assigned_user_name"] = (
        names.get(int(assigned_user_id)) if assigned_user_id else None
    )

    include_chat = scope in {"chat", "full"}
    include_timeline = scope in {"timeline", "full"}
    events = timeline_result.get("events", []) if include_timeline else []
    notes = timeline_result.get("notes", []) if scope == "full" else []

    report = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by": {
            "id": current_user.get("id"),
            "name": current_user.get("full_name") or current_user.get("email"),
        },
        "conversation": conversation,
        "messages": messages if include_chat else [],
        "timeline": events,
        "notes": notes,
    }

    # The export belongs to the company that made it. It used to be headed
    # "T-ZONE Conversation Report" whoever produced it, so a company handing a
    # transcript to its own customer handed over the platform owner's name.
    report_title = f"{_company_name(company_id)} Conversation Report"

    def build_text_report() -> str:
        lines = [
            report_title,
            f"Channel: {channel}",
            f"Customer ID: {user_id}",
            f"Exported at: {report['exported_at']}",
            "",
        ]

        if include_chat:
            lines.append("=== CHAT ===")
            for message in messages:
                lines.append(
                    f"[{message.get('time') or ''}] "
                    f"{message.get('direction') or 'unknown'}: "
                    f"{message.get('text') or '[Unsupported message]'}"
                )
            lines.append("")

        if include_timeline:
            lines.append("=== TIMELINE ===")
            for event in events:
                lines.append(
                    f"[{event.get('created_at') or ''}] "
                    f"{event.get('event_type') or 'event'} — "
                    f"{event.get('actor_name') or 'System'} — "
                    f"{json.dumps(event.get('data') or {}, ensure_ascii=False)}"
                )
            lines.append("")

        if scope == "full":
            lines.append("=== INTERNAL NOTES ===")
            for note in notes:
                lines.append(
                    f"[{note.get('created_at') or ''}] "
                    f"{note.get('author_name') or 'Unknown'}: "
                    f"{note.get('note') or ''}"
                )

        return "\n".join(lines)

    if file_format == "txt":
        filename = _export_filename(channel, user_id, scope, "txt")
        return Response(
            content=build_text_report(),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if file_format == "pdf":
        filename = _export_filename(channel, user_id, scope, "pdf")
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        page_width, page_height = A4
        font_name = "Helvetica"

        for font_path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ):
            if Path(font_path).exists():
                try:
                    pdfmetrics.registerFont(TTFont("TZoneUnicode", font_path))
                    font_name = "TZoneUnicode"
                    break
                except Exception:  # noqa: BLE001
                    pass

        pdf.setTitle(report_title)
        pdf.setFont(font_name, 13)
        y = page_height - 42

        for raw_line in build_text_report().splitlines():
            line = raw_line or " "
            chunks = [line[index : index + 105] for index in range(0, len(line), 105)] or [" "]

            for chunk in chunks:
                if y < 42:
                    pdf.showPage()
                    pdf.setFont(font_name, 9)
                    y = page_height - 42

                display_chunk = _pdf_display_text(chunk)

                if any("\u0600" <= char <= "\u06ff" for char in chunk):
                    pdf.drawRightString(page_width - 36, y, display_chunk)
                else:
                    pdf.drawString(36, y, display_chunk)

                y -= 14

            if raw_line.startswith("==="):
                y -= 4

        pdf.save()
        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if file_format == "json":
        filename = _export_filename(channel, user_id, scope, "json")
        return Response(
            content=json.dumps(report, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    rows: list[dict[str, Any]] = []

    if include_chat:
        rows.extend(
            {
                "record_type": "message",
                "channel": channel,
                "external_user_id": user_id,
                "direction": message.get("direction"),
                "text": message.get("text"),
                "created_at": message.get("time"),
                "metadata": message.get("metadata"),
            }
            for message in messages
        )

    if include_timeline:
        rows.extend(
            {
                "record_type": "event",
                "channel": channel,
                "external_user_id": user_id,
                "event_type": event.get("event_type"),
                "actor": event.get("actor_name"),
                "created_at": event.get("created_at"),
                "metadata": event.get("data"),
            }
            for event in events
        )

    if scope == "full":
        rows.extend(
            {
                "record_type": "note",
                "channel": channel,
                "external_user_id": user_id,
                "text": note.get("note"),
                "actor": note.get("author_name"),
                "created_at": note.get("created_at"),
            }
            for note in notes
        )

    output = io.StringIO()

    if rows:
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)

        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    key: _csv_cell(
                        json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (dict, list, tuple))
                        else value
                    )
                    for key, value in row.items()
                }
            )

    filename = _export_filename(channel, user_id, scope, "csv")

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
