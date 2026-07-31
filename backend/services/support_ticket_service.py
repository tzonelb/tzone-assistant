"""
Support tickets — a company opens a maintenance/support ticket to T-ZONE
(the platform vendor) about platform issues. This is distinct from the CRM
`tickets` table, which tracks a company's own end-customers' support cases.
"""
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


VALID_PRIORITIES = {"low", "normal", "high", "urgent"}


class SupportTicketService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS support_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    created_by_user_id INTEGER,
                    subject TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_support_tickets_company ON support_tickets(company_id)"
            )
            conn.commit()

    def list_for_company(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM support_tickets WHERE company_id = ? ORDER BY created_at DESC",
                (company_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create(
        self,
        *,
        company_id: int,
        subject: str,
        description: str,
        priority: str = "normal",
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        subject = (subject or "").strip()
        description = (description or "").strip()
        priority = (priority or "normal").strip().lower()
        if not subject or not description:
            raise ValueError("Both a subject and a description are required.")
        if priority not in VALID_PRIORITIES:
            priority = "normal"

        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO support_tickets
                    (company_id, created_by_user_id, subject, description, priority, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (company_id, actor_user_id, subject, description, priority, now, now),
            )
            ticket_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(company_id=company_id, ticket_id=ticket_id)

    def get(self, *, company_id: int, ticket_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM support_tickets WHERE id = ? AND company_id = ?",
                (ticket_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Support ticket not found")
        return dict(row)


support_ticket_service = SupportTicketService()
