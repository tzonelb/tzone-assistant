"""A company reporting a problem with the platform itself to the T-ZONE team.

Not to be confused with `ticket_service`, which is a company's own customers'
cases and lives inside that company's encrypted database. This one is addressed
to the operator, so the rows live in the control plane where the operator can
read them without opening anybody's tenant file — the same reasoning as
`subscription_requests`.

A ticket carries only what the employee typed about the platform. Nothing here
should ever quote a customer's message; the route bounds the length, and the
screen that writes them says so.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


VALID_PRIORITIES: tuple[str, ...] = ("low", "normal", "high", "urgent")

# Refused rather than coerced. A priority silently rewritten to "normal" reads
# back on the screen as the urgency the reporter chose, and the one person who
# needed it escalated is the one who cannot tell it was not.
_PRIORITIES = frozenset(VALID_PRIORITIES)


class SupportTicketService:
    def list_for_company(self, company_id: int) -> list[dict[str, Any]]:
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT id, subject, description, priority, status,
                       created_at, updated_at
                FROM support_tickets
                WHERE company_id = ?
                ORDER BY id DESC
                """,
                (int(company_id),),
            ).fetchall()

        return [dict(row) for row in rows]

    def create(
        self,
        *,
        company_id: int,
        subject: str,
        description: str,
        priority: str,
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        subject = (subject or "").strip()
        description = (description or "").strip()
        priority = (priority or "normal").strip().lower()

        if not subject or not description:
            raise ValueError("A ticket needs both a subject and a description.")

        if priority not in _PRIORITIES:
            raise ValueError(
                "priority must be one of: " + ", ".join(VALID_PRIORITIES)
            )

        now = utc_now_iso()

        with database_manager.control() as conn:
            cursor = conn.execute(
                """
                INSERT INTO support_tickets (
                    company_id, created_by_user_id, subject, description,
                    priority, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    int(company_id),
                    actor_user_id,
                    subject,
                    description,
                    priority,
                    now,
                    now,
                ),
            )
            ticket_id = int(cursor.lastrowid)
            conn.commit()

        return {
            "id": ticket_id,
            "subject": subject,
            "description": description,
            "priority": priority,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        }


support_ticket_service = SupportTicketService()
