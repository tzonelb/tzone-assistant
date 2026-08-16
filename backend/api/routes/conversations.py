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

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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

from backend.services.auth_service import (
    auth_service,
    require_permission,
)
from backend.services.conversation_control_service import (
    ConversationOwnershipConflict,
    conversation_control_service,
)
from backend.services.message_service import message_service
from database.manager import DatabaseError, database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["Conversations"])


DEPARTMENTS = [
    "Unassigned",
    "Sales",
    "IPTV",
    "Support",
    "Accounting",
    "Maintenance",
    "Orders",
    "Information",
]

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


class ConversationNoteCreate(BaseModel):
    note: str = Field(min_length=1, max_length=4000)


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
        "current_user_id": int(current_user["id"]),
        "current_user_is_admin": is_admin,
        "departments": DEPARTMENTS,
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
        "departments": DEPARTMENTS,
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
    limit: int = Query(default=50, ge=1, le=500),
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

    conversation_control_service.record_opened(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
        actor_user_id=int(current_user["id"]),
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
    result["departments"] = DEPARTMENTS
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

    if payload.department is not None and payload.department not in DEPARTMENTS:
        raise HTTPException(status_code=422, detail="Invalid department.")

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
    scope: Literal["chat", "timeline", "full"] = Query(default="full"),
    file_format: Literal["json", "csv", "txt", "pdf"] = Query(
        default="json", alias="format"
    ),
    current_user: dict[str, Any] = Depends(require_permission("conversations.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

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
                    key: (
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
