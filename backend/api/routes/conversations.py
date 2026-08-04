
import csv
import io
import json
from datetime import datetime
import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:  # Optional Arabic PDF support.
    arabic_reshaper = None
    get_display = None

from backend.services.auth_service import (
    auth_service,
    get_current_user,
)
from backend.services.conversation_control_service import (
    ConversationOwnershipConflict,
    ConversationVersionConflict,
    conversation_control_service,
)
from core.conversation_store import (
    get_conversation,
)
from channels.meta.profile import resolve_meta_profile


def _pdf_display_text(value: Any) -> str:
    text = str(value or "")
    if arabic_reshaper is not None and get_display is not None:
        try:
            return get_display(arabic_reshaper.reshape(text))
        except Exception:
            return text
    return text


router = APIRouter(
    prefix="/conversations",
    tags=["Conversations"],
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[3]
)

CONVERSATIONS_DIR = (
    PROJECT_ROOT
    / "data"
    / "conversations"
)


DEPARTMENTS = [
    "Unassigned",
    "Sales",
    "IPTV",
    "Support",
    "Accounting",
    "Maintenance",
    "Telecom",
    "Information",
]


class ConversationControlUpdate(
    BaseModel,
):
    status: str | None = None
    priority: str | None = None
    department: str | None = None
    assigned_user_id: int | None = None
    customer_alias: str | None = None
    folder: str | None = None
    is_starred: bool | None = None
    is_pinned: bool | None = None
    tags: list[str] | None = None
    clear_assignment: bool = False
    is_unread: bool | None = None
    # Optimistic-concurrency marker: the dedicated "control_version" the
    # client last saw from GET .../control (conversation.control_version).
    # When present, the write is rejected with 409 if the stored version has
    # since moved on (someone else changed a control field in the
    # meantime). This is intentionally NOT the row's general "updated_at",
    # which is also bumped by unrelated activity (inbound customer
    # messages, AI/employee replies, takeover-timeout expiry, ...) and
    # would otherwise cause spurious conflicts unrelated to concurrent
    # control-field edits.
    expected_control_version: int | None = None


class ConversationModeChangeRequest(BaseModel):
    # Same optimistic-concurrency marker as ConversationControlUpdate
    # above. Take Over / Release / Return to AI mutate the same control
    # fields (assigned_user_id, handled_by_ai, status, workflow_state,
    # needs_human) as the generic control-update path, so they must be
    # able to participate in the same stale-version check: without this,
    # a user with a stale local `control_version` who clicks one of these
    # buttons could silently clobber a concurrent change made by someone
    # else via either path. Optional and defaults to None (no check) so
    # older/other callers keep working unchanged.
    expected_control_version: int | None = None


class ConversationNoteCreate(
    BaseModel,
):
    note: str


class ConversationTagCreate(BaseModel):
    name: str
    color: str | None = None


class ConversationTagUpdate(BaseModel):
    name: str
    color: str | None = None


def _safe_read_json_line(
    line: str,
) -> dict[str, Any] | None:
    try:
        value = json.loads(line)

        if isinstance(value, dict):
            return value

    except (
        json.JSONDecodeError,
        TypeError,
    ):
        return None

    return None


def _parse_datetime(
    value: Any,
) -> datetime:
    if not value:
        return datetime.min

    try:
        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return datetime.min


def _read_conversation_file(
    file_path: Path,
) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []

    try:
        lines = file_path.read_text(
            encoding="utf-8",
        ).splitlines()

    except OSError:
        return []

    rows: list[dict[str, Any]] = []

    for line in lines:
        row = _safe_read_json_line(line)

        if row is not None:
            rows.append(row)

    return rows


def _latest_metadata(
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    for message in reversed(messages):
        metadata = message.get("metadata")

        if isinstance(metadata, dict):
            return metadata

    return {}


def _user_name(
    company_id: int,
    user_id: int | None,
) -> str | None:
    if user_id is None:
        return None

    from database.database import db

    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT
                users.full_name,
                users.email
            FROM users
            JOIN company_users
                ON company_users.user_id = users.id
            WHERE users.id = ?
              AND company_users.company_id = ?
              AND users.status = 'active'
              AND company_users.status = 'active'
            LIMIT 1
            """,
            (
                user_id,
                company_id,
            ),
        ).fetchone()

    if not row:
        return None

    return (
        row["full_name"]
        or row["email"]
        or f"User {user_id}"
    )


def _company_employees(
    company_id: int,
) -> list[dict[str, Any]]:
    from database.database import db

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                users.id,
                users.full_name,
                users.email,
                users.phone,
                roles.name AS role_name,
                roles.code AS role_code,
                company_users.branch_id
            FROM company_users
            JOIN users
                ON users.id =
                   company_users.user_id
            LEFT JOIN roles
                ON roles.id =
                   company_users.role_id
            WHERE company_users.company_id = ?
              AND company_users.status = 'active'
              AND users.status = 'active'
            ORDER BY
                users.full_name ASC,
                users.email ASC
            """,
            (company_id,),
        ).fetchall()

    return [
        {
            **dict(row),
            "display_name": (
                row["full_name"]
                or row["email"]
                or f"User {row['id']}"
            ),
        }
        for row in rows
    ]


def _is_conversation_admin(
    current_user: dict[str, Any],
    company_id: int,
) -> bool:
    return auth_service.has_permission(
        user_id=int(current_user["id"]),
        company_id=company_id,
        permission_code="users.manage",
        is_super_admin=bool(current_user.get("is_super_admin")),
    )


def _has_conversations_view(
    current_user: dict[str, Any],
    company_id: int,
) -> bool:
    # has_permission already grants the "owner" role and super admins
    # unconditional access, so this covers those bypasses too.
    return auth_service.has_permission(
        user_id=int(current_user["id"]),
        company_id=company_id,
        permission_code="conversations.view",
        is_super_admin=bool(current_user.get("is_super_admin")),
    )


def _has_conversations_reply(
    current_user: dict[str, Any],
    company_id: int,
) -> bool:
    return auth_service.has_permission(
        user_id=int(current_user["id"]),
        company_id=company_id,
        permission_code="conversations.reply",
        is_super_admin=bool(current_user.get("is_super_admin")),
    )


def _require_conversations_view(
    current_user: dict[str, Any],
    company_id: int,
) -> None:
    if not _has_conversations_view(current_user, company_id):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to view conversations.",
        )


def _require_conversations_reply(
    current_user: dict[str, Any],
    company_id: int,
) -> None:
    # Replying/mutating a conversation is additive on top of the existing
    # ownership + company-scoping rules: the caller must hold the
    # "conversations.reply" permission (owner role / super admin bypass via
    # has_permission), AND still satisfy the ownership checks that follow.
    if not _has_conversations_reply(current_user, company_id):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to reply to conversations.",
        )


def _conversation_owner_detail(
    company_id: int,
    owner_user_id: int | None,
    fallback: str,
) -> dict[str, Any]:
    owner_name = _user_name(company_id, owner_user_id)
    message = (
        f"Conversation is assigned to {owner_name}."
        if owner_name
        else fallback
    )
    return {
        "code": "conversation_owned",
        "message": message,
        "owner_user_id": owner_user_id,
        "owner_user_name": owner_name,
    }


def _assert_can_control_conversation(
    *,
    current_user: dict[str, Any],
    company_id: int,
    channel: str,
    external_user_id: str,
) -> tuple[dict[str, Any], bool]:
    # SECURITY (repair round): create_if_missing=False -- this helper backs
    # every employee-initiated control action (return-to-ai, update-control,
    # notes). It must never auto-vivify a company-scoped ownership row for a
    # conversation this company has never actually had; that side effect is
    # exactly what let one company's employee manufacture "ownership" of
    # another company's conversation and defeat conversation_exists().
    state = conversation_control_service.get_state(
        company_id=company_id,
        channel=channel,
        external_user_id=external_user_id,
        create_if_missing=False,
    )
    if state is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
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
            detail=_conversation_owner_detail(
                company_id,
                int(owner_user_id) if owner_user_id is not None else None,
                "Take over this conversation before changing it.",
            ),
        )
    return state, is_admin


def _message_search_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        for key in ("text", "message", "content"):
            value = message.get(key)
            if value:
                parts.append(str(value))
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            for value in metadata.values():
                if isinstance(value, (str, int, float, bool)):
                    parts.append(str(value))
    return " ".join(parts).casefold()


def _public_conversation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if not key.startswith("_")
    }


def _build_summary(
    company_id: int,
    channel: str,
    user_id: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not messages:
        return None

    # SECURITY (repair round): create_if_missing=False. This function is
    # invoked once per file under the GLOBAL (not company-scoped) flat-file
    # conversation store for every list_conversations/live_events poll. With
    # auto-vivification on, simply opening the inbox as any employee would
    # silently create a company-scoped ownership row for every OTHER
    # company's customer in the entire system, and (before this fix) also
    # leaked those companies' customer name / last message / tags into this
    # company's list -- with no gate at all, unlike read_conversation. A
    # conversation file that has no existing row for this company_id is not
    # this company's conversation; skip it instead of adopting it.
    control = (
        conversation_control_service
        .get_state(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            create_if_missing=False,
        )
    )

    if control is None:
        return None

    last_message = messages[-1]
    metadata = _latest_metadata(messages)

    display_name = (
        control.get("official_customer_name")
        or metadata.get("customer_name")
        or metadata.get("sender_name")
        or metadata.get("name")
    )

    if not display_name and channel == "messenger":
        profile = resolve_meta_profile(user_id=user_id, channel=channel)
        display_name = profile.get("customer_name")

    display_name = display_name or f"{channel.title()} Customer"

    handled_by_ai = bool(
        control.get(
            "handled_by_ai",
            True,
        )
    )

    ai_enabled = bool(
        control.get(
            "ai_enabled",
            True,
        )
    )

    assigned_user_id = control.get(
        "assigned_user_id"
    )

    return {
        "id": f"{channel}:{user_id}",
        "channel": channel,
        "external_user_id": user_id,
        "customer_name": display_name,
        "customer_profile_picture": (
            control.get("customer_profile_picture")
            or metadata.get("customer_profile_picture")
        ),
        "customer_alias": control.get(
            "customer_alias"
        ),
        "folder": control.get("folder") or "inbox",
        "is_starred": bool(control.get("is_starred", False)),
        "is_pinned": bool(control.get("is_pinned", False)),
        "tags": control.get("tags", []),
        "department": (
            control.get("department")
            or "Unassigned"
        ),
        "topic": (
            metadata.get("topic")
            or metadata.get("intent")
            or "General"
        ),
        "status": (
            control.get("status")
            or "open"
        ),
        "priority": (
            control.get("priority")
            or "normal"
        ),
        "handled_by_ai": handled_by_ai,
        "ai_enabled": ai_enabled,
        "ai_status": (
            "active"
            if handled_by_ai and ai_enabled
            else "human"
        ),
        "assigned_user_id": assigned_user_id,
        "assigned_user_name": _user_name(
            company_id,
            assigned_user_id,
        ),
        "unread_count": int(
            control.get(
                "unread_count",
                0,
            )
            or 0
        ),
        "takeover_expires_at": (
            control.get(
                "takeover_expires_at"
            )
        ),
        "last_message": (
            last_message.get("text")
            or ""
        ),
        "last_direction": (
            last_message.get("direction")
            or ""
        ),
        "updated_at": (
            last_message.get("time")
            or control.get("updated_at")
            or ""
        ),
        # Optimistic-concurrency anchor for quick actions (star/pin/folder/
        # tags) fired straight from the list view — see control_version on
        # the control PATCH payload / GET .../control response.
        "control_version": control.get("control_version"),
        "message_count": len(messages),
        "branch_id": control.get(
            "branch_id"
        ),
        "channel_account_id": control.get(
            "channel_account_id"
        ),
        "_search_text": _message_search_text(messages),
    }


def _load_all_conversations(
    company_id: int,
) -> list[dict[str, Any]]:
    if not CONVERSATIONS_DIR.exists():
        return []

    conversations: list[
        dict[str, Any]
    ] = []

    for channel_dir in (
        CONVERSATIONS_DIR.iterdir()
    ):
        if not channel_dir.is_dir():
            continue

        channel = (
            channel_dir.name
            .strip()
            .lower()
        )

        for file_path in channel_dir.glob(
            "*.jsonl"
        ):
            user_id = file_path.stem

            messages = (
                _read_conversation_file(
                    file_path
                )
            )

            summary = _build_summary(
                company_id=company_id,
                channel=channel,
                user_id=user_id,
                messages=messages,
            )

            if summary is not None:
                conversations.append(
                    summary
                )

    conversations.sort(
        key=lambda conversation:
            _parse_datetime(
                conversation.get(
                    "updated_at"
                )
            ),
        reverse=True,
    )

    return conversations


def _channel_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {"all": 0}
    for row in rows:
        if int(row.get("unread_count", 0) or 0) <= 0:
            continue
        channel = str(row.get("channel") or "unknown")
        counts["all"] += 1
        counts[channel] = counts.get(channel, 0) + 1
    return counts


def _export_filename(
    channel: str,
    user_id: str,
    scope: str,
    extension: str,
) -> str:
    safe_channel = (
        channel.replace("/", "_")
    )

    safe_user_id = (
        user_id.replace("/", "_")
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    return (
        f"{safe_channel}_"
        f"{safe_user_id}_"
        f"{scope}_"
        f"{timestamp}."
        f"{extension}"
    )


def _csv_response(
    rows: list[dict[str, Any]],
    filename: str,
) -> StreamingResponse:
    output = io.StringIO()

    if rows:
        fieldnames: list[str] = []

        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)

        writer = csv.DictWriter(
            output,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for row in rows:
            serialized_row: dict[
                str,
                Any,
            ] = {}

            for key, value in row.items():
                if isinstance(
                    value,
                    (
                        dict,
                        list,
                        tuple,
                    ),
                ):
                    serialized_row[key] = (
                        json.dumps(
                            value,
                            ensure_ascii=False,
                        )
                    )
                else:
                    serialized_row[key] = value

            writer.writerow(
                serialized_row
            )

    content = output.getvalue()

    return StreamingResponse(
        iter([content]),
        media_type=(
            "text/csv; charset=utf-8"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
        },
    )


@router.get("/")
def list_conversations(
    search: str = Query(
        default="",
        max_length=200,
    ),
    channel: str = Query(
        default="all",
        max_length=50,
    ),
    status: str = Query(
        default="all",
        max_length=50,
    ),
    department: str = Query(
        default="all",
        max_length=100,
    ),
    assigned_user_id: int | None = Query(
        default=None,
    ),
    folder: str = Query(
        default="inbox",
        max_length=30,
    ),
    tag: str = Query(
        default="",
        max_length=50,
    ),
    read_status: str = Query(
        default="all",
        max_length=20,
    ),
    page: int = Query(
        default=1,
        ge=1,
    ),
    page_size: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    current_user: dict[
        str,
        Any,
    ] = Depends(
        get_current_user
    ),
):
    company_id = (
        auth_service.resolve_company_id(
            current_user
        )
    )

    _require_conversations_view(current_user, company_id)

    all_rows = (
        _load_all_conversations(
            company_id
        )
    )

    counts = _channel_counts(all_rows)
    available_channels = sorted({
        str(row.get("channel"))
        for row in all_rows
        if row.get("channel")
    })
    current_user_id = int(current_user["id"])
    current_user_is_admin = _is_conversation_admin(current_user, company_id)

    normalized_search = (
        search.strip().casefold()
    )

    filtered_rows: list[
        dict[str, Any]
    ] = []

    for row in all_rows:
        searchable_values = (
            row.get("customer_name"),
            row.get("customer_alias"),
            row.get("external_user_id"),
            row.get("last_message"),
            row.get("topic"),
            row.get("department"),
            row.get("channel"),
            row.get("assigned_user_name"),
            row.get("_search_text"),
        )

        matches_search = (
            not normalized_search
            or any(
                normalized_search
                in str(
                    value or ""
                ).casefold()
                for value
                in searchable_values
            )
        )

        matches_channel = (
            channel == "all"
            or row.get("channel")
            == channel
        )

        matches_status = (
            status == "all"
            or row.get("status")
            == status
        )

        matches_department = (
            department == "all"
            or row.get("department")
            == department
        )

        matches_assignment = (
            assigned_user_id is None
            or row.get(
                "assigned_user_id"
            ) == assigned_user_id
        )

        matches_folder = (
            folder == "all"
            or row.get("folder", "inbox") == folder
            or (folder == "starred" and row.get("is_starred"))
            or (folder == "pinned" and row.get("is_pinned"))
            or (
                folder == "assigned_to_me"
                and row.get("assigned_user_id") == current_user_id
            )
            or (
                folder == "unread"
                and int(row.get("unread_count", 0) or 0) > 0
            )
        )

        normalized_tag = tag.strip().casefold()
        matches_tag = (
            not normalized_tag
            or any(
                str(item).casefold() == normalized_tag
                for item in row.get("tags", [])
            )
        )

        is_unread = int(row.get("unread_count", 0) or 0) > 0
        matches_read_status = (
            read_status == "all"
            or (read_status == "unread" and is_unread)
            or (read_status == "read" and not is_unread)
        )

        if (
            matches_search
            and matches_channel
            and matches_status
            and matches_department
            and matches_assignment
            and matches_folder
            and matches_tag
            and matches_read_status
        ):
            public_row = _public_conversation(row)
            owner_id = row.get("assigned_user_id")
            public_row["can_manage"] = bool(
                current_user_is_admin
                or (
                    owner_id is not None
                    and int(owner_id) == current_user_id
                )
            )
            public_row["is_assigned_to_me"] = bool(
                owner_id is not None
                and int(owner_id) == current_user_id
            )
            filtered_rows.append(public_row)

    total = len(filtered_rows)

    start = (
        page - 1
    ) * page_size

    end = start + page_size

    return {
        "status": "ok",
        "items": filtered_rows[
            start:end
        ],
        "channel_counts": counts,
        "available_channels": available_channels,
        "current_user_id": current_user_id,
        "current_user_is_admin": current_user_is_admin,
        "departments": DEPARTMENTS,
        "employees": (
            _company_employees(
                company_id
            )
        ),
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(
                1,
                (
                    total
                    + page_size
                    - 1
                )
                // page_size,
            ),
        },
    }


@router.get("/options")
def conversation_options(
    current_user: dict[
        str,
        Any,
    ] = Depends(
        get_current_user
    ),
):
    company_id = (
        auth_service.resolve_company_id(
            current_user
        )
    )

    _require_conversations_view(current_user, company_id)

    return {
        "status": "ok",
        "departments": DEPARTMENTS,
        "employees": (
            _company_employees(
                company_id
            )
        ),
    }


@router.get("/live/events")
async def live_conversation_events(
    current_user: dict[str, Any] = Depends(
        get_current_user
    ),
):
    company_id = auth_service.resolve_company_id(
        current_user
    )

    _require_conversations_view(current_user, company_id)

    async def event_stream():
        last_signature = ""

        while True:
            rows = _load_all_conversations(company_id)
            signature_payload = [
                {
                    "id": row.get("id"),
                    "updated_at": row.get("updated_at"),
                    "last_message": row.get("last_message"),
                    "folder": row.get("folder"),
                    "is_starred": row.get("is_starred"),
                    "is_pinned": row.get("is_pinned"),
                    "tags": row.get("tags", []),
                    "assigned_user_id": row.get("assigned_user_id"),
                    "handled_by_ai": row.get("handled_by_ai"),
                    "unread_count": row.get("unread_count", 0),
                    "takeover_expires_at": row.get("takeover_expires_at"),
                }
                for row in rows
            ]
            signature = json.dumps(
                signature_payload,
                ensure_ascii=False,
                sort_keys=True,
            )

            if signature != last_signature:
                last_signature = signature
                payload = {
                    "type": "conversations_updated",
                    "items": [_public_conversation(row) for row in rows],
                    "channel_counts": _channel_counts(rows),
                    "available_channels": sorted({
                        str(row.get("channel"))
                        for row in rows
                        if row.get("channel")
                    }),
                    "timestamp": datetime.now().isoformat(),
                }
                yield (
                    "event: conversations_updated\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )

            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )




@router.get(
    "/{channel}/{user_id}"
)
def read_conversation(
    channel: str,
    user_id: str,
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
    ),
    current_user: dict[
        str,
        Any,
    ] = Depends(
        get_current_user
    ),
):
    company_id = auth_service.resolve_company_id(current_user)

    if not conversation_control_service.conversation_exists(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    ):
        # Do not distinguish "no such conversation anywhere" from
        # "this conversation belongs to a different company" -- both
        # cases must return the same 404 so this endpoint cannot be used
        # to confirm/deny another tenant's customers by probing channel
        # + user_id combinations.
        raise HTTPException(
            status_code=404,
            detail=(
                "Conversation not found."
            ),
        )

    messages = get_conversation(
        channel,
        user_id,
        limit,
    )

    if not messages:
        raise HTTPException(
            status_code=404,
            detail=(
                "Conversation not found."
            ),
        )

    _require_conversations_view(current_user, company_id)
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


@router.get(
    "/{channel}/{user_id}/control"
)
def read_control(
    channel: str,
    user_id: str,
    current_user: dict[
        str,
        Any,
    ] = Depends(
        get_current_user
    ),
):
    company_id = (
        auth_service.resolve_company_id(
            current_user
        )
    )

    _require_conversations_view(current_user, company_id)

    # SECURITY (repair round): timeline() calls get_state() internally,
    # which used to auto-vivify a company-scoped ownership row on first
    # lookup. A single, otherwise-harmless-looking GET here was enough for
    # any employee to manufacture ownership of another company's
    # conversation and defeat conversation_exists() in read_conversation /
    # export_conversation. Gate on the read-only existence check *before*
    # calling timeline(), so the auto-create side effect never runs for a
    # conversation this company doesn't actually own.
    if not conversation_control_service.conversation_exists(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    ):
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    result = (
        conversation_control_service
        .timeline(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
        )
    )

    conversation = result.get(
        "conversation",
        {},
    )

    assigned_user_id = (
        conversation.get(
            "assigned_user_id"
        )
    )

    conversation[
        "assigned_user_name"
    ] = _user_name(
        company_id,
        assigned_user_id,
    )

    result["employees"] = (
        _company_employees(
            company_id
        )
    )

    result["departments"] = (
        DEPARTMENTS
    )
    current_user_id = int(current_user["id"])
    is_admin = _is_conversation_admin(current_user, company_id)
    is_owner = (
        assigned_user_id is not None
        and int(assigned_user_id) == current_user_id
    )
    can_reply_permission = _has_conversations_reply(current_user, company_id)
    result["current_user_id"] = current_user_id
    result["current_user_is_admin"] = is_admin
    result["can_reply_permission"] = can_reply_permission
    # Every mutating/reply control is additionally gated on the
    # "conversations.reply" permission so the UI never offers an action the
    # backend will reject with 403. Read-only viewers keep the timeline open
    # but see no reply/take-over/manage controls.
    result["permissions"] = {
        "can_reply": bool(
            can_reply_permission
            and is_owner
            and not conversation.get("handled_by_ai", True)
        ),
        "can_manage": bool(can_reply_permission and (is_admin or is_owner)),
        "can_mark_read": bool(can_reply_permission and (is_admin or is_owner)),
        "can_take_over": bool(
            can_reply_permission
            and (assigned_user_id is None or is_owner)
        ),
    }

    return result


@router.post(
    "/{channel}/{user_id}/take-over"
)
def take_over(
    channel: str,
    user_id: str,
    payload: ConversationModeChangeRequest | None = None,
    current_user: dict[
        str,
        Any,
    ] = Depends(
        get_current_user
    ),
):
    company_id = (
        auth_service.resolve_company_id(
            current_user
        )
    )

    _require_conversations_reply(current_user, company_id)

    # SECURITY (repair round): set_ai_mode() calls get_or_create()
    # internally, which used to auto-vivify a company-scoped ownership row
    # on first lookup. Taking over a conversation your company was never
    # actually contacted about is not a legitimate action; gate on the
    # read-only existence check first so no such row can be manufactured.
    if not conversation_control_service.conversation_exists(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    ):
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    try:
        conversation = (
            conversation_control_service
            .set_ai_mode(
                company_id=company_id,
                channel=channel,
                external_user_id=user_id,
                handled_by_ai=False,
                actor_user_id=current_user["id"],
                expected_control_version=(
                    payload.expected_control_version if payload else None
                ),
            )
        )
    except ConversationOwnershipConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conversation_owned",
                **_conversation_owner_detail(
                    company_id,
                    exc.owner_user_id,
                    "Conversation is currently owned by another employee.",
                ),
            },
        ) from exc
    except ConversationVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_version",
                "message": str(exc),
            },
        ) from exc

    conversation["assigned_user_name"] = _user_name(
        company_id,
        conversation.get("assigned_user_id"),
    )
    return {
        "status": "ok",
        "conversation": conversation,
    }


@router.post(
    "/{channel}/{user_id}/release"
)
def release_conversation(
    channel: str,
    user_id: str,
    payload: ConversationModeChangeRequest | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = auth_service.resolve_company_id(current_user)
    _require_conversations_reply(current_user, company_id)

    # SECURITY (repair round): release() calls get_state() internally,
    # which used to auto-vivify a company-scoped ownership row on first
    # lookup. Gate on the read-only existence check first.
    if not conversation_control_service.conversation_exists(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    ):
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    is_admin = _is_conversation_admin(current_user, company_id)
    try:
        conversation = conversation_control_service.release(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
            actor_user_id=int(current_user["id"]),
            force=is_admin,
            expected_control_version=(
                payload.expected_control_version if payload else None
            ),
        )
    except ConversationOwnershipConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conversation_owned",
                **_conversation_owner_detail(
                    company_id,
                    exc.owner_user_id,
                    "Only the assigned employee can release this conversation.",
                ),
            },
        ) from exc
    except ConversationVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_version",
                "message": str(exc),
            },
        ) from exc
    conversation["assigned_user_name"] = None
    return {"status": "ok", "conversation": conversation}


@router.post(
    "/{channel}/{user_id}/return-to-ai"
)
def return_to_ai(
    channel: str,
    user_id: str,
    payload: ConversationModeChangeRequest | None = None,
    current_user: dict[
        str,
        Any,
    ] = Depends(
        get_current_user
    ),
):
    company_id = (
        auth_service.resolve_company_id(
            current_user
        )
    )

    _require_conversations_reply(current_user, company_id)

    _assert_can_control_conversation(
        current_user=current_user,
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    try:
        conversation = (
            conversation_control_service
            .set_ai_mode(
                company_id=company_id,
                channel=channel,
                external_user_id=user_id,
                handled_by_ai=True,
                actor_user_id=(
                    current_user["id"]
                ),
                expected_control_version=(
                    payload.expected_control_version if payload else None
                ),
            )
        )
    except ConversationVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_version",
                "message": str(exc),
            },
        ) from exc

    return {
        "status": "ok",
        "conversation": conversation,
    }


@router.patch(
    "/{channel}/{user_id}/control"
)
def update_control(
    channel: str,
    user_id: str,
    payload: ConversationControlUpdate,
    current_user: dict[
        str,
        Any,
    ] = Depends(
        get_current_user
    ),
):
    company_id = (
        auth_service.resolve_company_id(
            current_user
        )
    )

    _require_conversations_reply(current_user, company_id)

    _control_state, _is_admin = _assert_can_control_conversation(
        current_user=current_user,
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    if (
        payload.department
        is not None
        and payload.department
        not in DEPARTMENTS
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid department."
            ),
        )

    if (
        payload.assigned_user_id
        is not None
    ):
        employee_ids = {
            employee["id"]
            for employee
            in _company_employees(
                company_id
            )
        }

        if (
            payload.assigned_user_id
            not in employee_ids
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Employee does not belong "
                    "to this company."
                ),
            )

    try:
        conversation = (
            conversation_control_service
            .update_state(
                company_id=company_id,
                channel=channel,
                external_user_id=user_id,
                actor_user_id=(
                    current_user["id"]
                ),
                status=payload.status,
                priority=(
                    payload.priority
                ),
                department=(
                    payload.department
                ),
                assigned_user_id=(
                    payload
                    .assigned_user_id
                ),
                is_admin=_is_admin,
                expected_control_version=(
                    payload.expected_control_version
                ),
            )
        )
    except ConversationOwnershipConflict as exc:
        # SECURITY FIX (Patch 9.1): this path used to update the row with
        # no ownership check at all. Now it raises the same 409 conflict
        # as Take Over, instead of silently overwriting another
        # employee's conversation.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conversation_owned",
                **_conversation_owner_detail(
                    company_id,
                    exc.owner_user_id,
                    "Conversation is currently owned by another employee.",
                ),
            },
        ) from exc

    except ConversationVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_version",
                "message": str(exc),
            },
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    try:
        conversation = (
            conversation_control_service
            .update_workspace_state(
                company_id=company_id,
                channel=channel,
                external_user_id=user_id,
                actor_user_id=current_user["id"],
                customer_alias=payload.customer_alias,
                folder=payload.folder,
                is_starred=payload.is_starred,
                is_pinned=payload.is_pinned,
                tags=payload.tags,
                clear_assignment=payload.clear_assignment,
                is_unread=payload.is_unread,
                # `conversation` here already reflects this same request's
                # update_state() write (if any), so we check freshness
                # against that, not the pre-request snapshot — otherwise
                # a request that touches both status/priority fields *and*
                # workspace fields (e.g. alias) would always conflict with
                # itself on the second write.
                expected_control_version=(
                    conversation.get("control_version")
                    if payload.expected_control_version is not None
                    else None
                ),
            )
        )
    except ConversationVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_version",
                "message": str(exc),
            },
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    conversation[
        "assigned_user_name"
    ] = _user_name(
        company_id,
        conversation.get(
            "assigned_user_id"
        ),
    )

    return {
        "status": "ok",
        "conversation": conversation,
    }


@router.post(
    "/{channel}/{user_id}/notes"
)
def add_note(
    channel: str,
    user_id: str,
    payload: ConversationNoteCreate,
    current_user: dict[
        str,
        Any,
    ] = Depends(
        get_current_user
    ),
):
    company_id = (
        auth_service.resolve_company_id(
            current_user
        )
    )

    _require_conversations_reply(current_user, company_id)

    _assert_can_control_conversation(
        current_user=current_user,
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    )

    try:
        note = (
            conversation_control_service
            .add_note(
                company_id=company_id,
                channel=channel,
                external_user_id=user_id,
                author_user_id=(
                    current_user["id"]
                ),
                note=payload.note,
            )
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    return {
        "status": "ok",
        "note": note,
    }


@router.get(
    "/{channel}/{user_id}/export"
)
def export_conversation(
    channel: str,
    user_id: str,
    scope: Literal[
        "chat",
        "timeline",
        "full",
    ] = Query(
        default="full",
    ),
    file_format: Literal[
        "json",
        "csv",
        "txt",
        "pdf",
    ] = Query(
        default="json",
        alias="format",
    ),
    current_user: dict[
        str,
        Any,
    ] = Depends(
        get_current_user
    ),
):
    company_id = (
        auth_service.resolve_company_id(
            current_user
        )
    )

    _require_conversations_view(current_user, company_id)

    if not conversation_control_service.conversation_exists(
        company_id=company_id,
        channel=channel,
        external_user_id=user_id,
    ):
        # Same cross-tenant gate as read_conversation: a 404 here must
        # not reveal whether the conversation exists for some other
        # company.
        raise HTTPException(
            status_code=404,
            detail=(
                "Conversation not found."
            ),
        )

    messages = get_conversation(
        channel,
        user_id,
        500,
    )

    timeline_result = (
        conversation_control_service
        .timeline(
            company_id=company_id,
            channel=channel,
            external_user_id=user_id,
        )
    )

    conversation = (
        timeline_result.get(
            "conversation",
            {},
        )
    )

    conversation[
        "assigned_user_name"
    ] = _user_name(
        company_id,
        conversation.get(
            "assigned_user_id"
        ),
    )

    report = {
        "exported_at": (
            datetime.now()
            .isoformat()
        ),
        "exported_by": {
            "id": current_user.get("id"),
            "name": (
                current_user.get(
                    "full_name"
                )
                or current_user.get(
                    "email"
                )
            ),
        },
        "conversation": conversation,
        "messages": (
            messages
            if scope
            in {"chat", "full"}
            else []
        ),
        "timeline": (
            timeline_result.get(
                "events",
                [],
            )
            if scope
            in {
                "timeline",
                "full",
            }
            else []
        ),
        "notes": (
            timeline_result.get(
                "notes",
                [],
            )
            if scope == "full"
            else []
        ),
    }

    def build_text_report() -> str:
        lines = [
            "T-ZONE Conversation Report",
            f"Channel: {channel}",
            f"Customer ID: {user_id}",
            f"Exported at: {report['exported_at']}",
            "",
        ]

        if scope in {"chat", "full"}:
            lines.append("=== CHAT ===")
            for message in messages:
                direction = message.get("direction") or "unknown"
                created_at = message.get("time") or message.get("created_at") or ""
                text = message.get("text") or message.get("message") or "[Unsupported message]"
                lines.append(f"[{created_at}] {direction}: {text}")
            lines.append("")

        if scope in {"timeline", "full"}:
            lines.append("=== TIMELINE ===")
            for event in timeline_result.get("events", []):
                lines.append(
                    f"[{event.get('created_at') or ''}] "
                    f"{event.get('event_type') or 'event'} — "
                    f"{event.get('actor_name') or 'System'} — "
                    f"{json.dumps(event.get('data') or {}, ensure_ascii=False)}"
                )
            lines.append("")

        if scope == "full":
            lines.append("=== INTERNAL NOTES ===")
            for note in timeline_result.get("notes", []):
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
                except Exception:
                    pass

        pdf.setTitle("T-ZONE Conversation Report")
        pdf.setFont(font_name, 13)
        y = page_height - 42
        for raw_line in build_text_report().splitlines():
            line = raw_line or " "
            chunks = [line[index:index + 105] for index in range(0, len(line), 105)] or [" "]
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
        filename = _export_filename(
            channel,
            user_id,
            scope,
            "json",
        )

        return Response(
            content=json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            media_type=(
                "application/json; "
                "charset=utf-8"
            ),
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{filename}"'
                ),
            },
        )

    rows: list[
        dict[str, Any]
    ] = []

    if scope in {
        "chat",
        "full",
    }:
        for message in messages:
            rows.append(
                {
                    "record_type": "message",
                    "channel": channel,
                    "external_user_id": user_id,
                    "direction": (
                        message.get(
                            "direction"
                        )
                    ),
                    "text": (
                        message.get("text")
                    ),
                    "created_at": (
                        message.get("time")
                        or message.get(
                            "created_at"
                        )
                    ),
                    "metadata": (
                        message.get(
                            "metadata"
                        )
                    ),
                }
            )

    if scope in {
        "timeline",
        "full",
    }:
        for event in timeline_result.get(
            "events",
            [],
        ):
            rows.append(
                {
                    "record_type": "timeline",
                    "channel": channel,
                    "external_user_id": user_id,
                    "event_type": (
                        event.get(
                            "event_type"
                        )
                    ),
                    "actor_name": (
                        event.get(
                            "actor_name"
                        )
                    ),
                    "created_at": (
                        event.get(
                            "created_at"
                        )
                    ),
                    "data": (
                        event.get("data")
                    ),
                }
            )

    if scope == "full":
        for note in timeline_result.get(
            "notes",
            [],
        ):
            rows.append(
                {
                    "record_type": "note",
                    "channel": channel,
                    "external_user_id": user_id,
                    "note": note.get("note"),
                    "author_name": (
                        note.get(
                            "author_name"
                        )
                    ),
                    "created_at": (
                        note.get(
                            "created_at"
                        )
                    ),
                }
            )

    filename = _export_filename(
        channel,
        user_id,
        scope,
        "csv",
    )

    return _csv_response(
        rows,
        filename,
    )
