"""A conversation's transcript as plain text, for anything that leaves the
platform as a message rather than a download -- a share link a customer opens
in a browser, or an email an employee sends.

Deliberately separate from `export_conversation`'s own report builder in
`backend/api/routes/conversations.py`: that one is proven and this platform's
sole path for the JSON/CSV/PDF downloads, and duplicating its handful of lines
here is a smaller risk than restructuring a working export around a second
caller with different needs (no attachment headers, a company name in the
title, always plain text).
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.services.auth_service import auth_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.message_service import message_service
from database.manager import database_manager
from database.manager import DatabaseError


def _company_name(company_id: int) -> str:
    try:
        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT name FROM companies WHERE id = ? LIMIT 1", (int(company_id),)
            ).fetchone()
    except DatabaseError:
        return "Conversation"
    return str(row["name"]) if row and row["name"] else "Conversation"


def build_transcript_text(
    *, company_id: int, channel: str, external_user_id: str, scope: str = "chat"
) -> str | None:
    """The conversation as readable text, or None if it does not exist.

    ``scope`` is "chat" (messages only) or "full" (messages, timeline events,
    and internal notes) -- a share link defaults to chat only, since a link a
    customer might open should not carry notes written about them for
    employees; an email an employee sends to themselves or a colleague can ask
    for the full record.
    """
    timeline_result = conversation_control_service.timeline(
        company_id=company_id,
        channel=channel,
        external_user_id=external_user_id,
    )
    if timeline_result is None:
        return None

    messages = message_service.list_messages(
        company_id=company_id,
        channel=channel,
        external_user_id=external_user_id,
        limit=500,
    )

    include_full = scope == "full"
    events = timeline_result.get("events", []) if include_full else []
    notes = timeline_result.get("notes", []) if include_full else []

    lines = [
        f"{_company_name(company_id)} Conversation Transcript",
        f"Channel: {channel}",
        f"Exported at: {datetime.now(timezone.utc).isoformat()}",
        "",
        "=== CHAT ===",
    ]
    for message in messages:
        lines.append(
            f"[{message.get('time') or ''}] "
            f"{message.get('direction') or 'unknown'}: "
            f"{message.get('text') or '[Unsupported message]'}"
        )

    if include_full:
        lines.append("")
        lines.append("=== TIMELINE ===")
        for event in events:
            lines.append(
                f"[{event.get('created_at') or ''}] "
                f"{event.get('event_type') or 'event'} — "
                f"{event.get('actor_name') or 'System'}"
            )
        lines.append("")
        lines.append("=== INTERNAL NOTES ===")
        for note in notes:
            lines.append(
                f"[{note.get('created_at') or ''}] "
                f"{note.get('author_name') or 'Unknown'}: "
                f"{note.get('note') or ''}"
            )

    return "\n".join(lines)
