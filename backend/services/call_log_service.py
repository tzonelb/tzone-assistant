from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# This is a call LOG — recording calls that already happened (phone,
# walk-in, WhatsApp voice note, whatever). It is deliberately not a
# dialer/click-to-call system: actually placing or receiving calls
# through the platform needs a telephony provider (Twilio or similar)
# with its own account/phone number, which is a business decision +
# credentials the company has to set up first, not something this
# codebase can fabricate. This module gives real value today (a real
# CRM call history per contact) and is the natural place to plug a
# provider's webhooks into later without changing the data model.
DIRECTIONS = ["inbound", "outbound"]
STATUSES = ["completed", "missed", "no_answer", "voicemail"]


class CallLogService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS call_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    customer_id INTEGER,
                    direction TEXT NOT NULL,
                    phone_number TEXT,
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'completed',
                    notes TEXT,
                    called_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL,
                    FOREIGN KEY(called_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            conn.commit()

    def _validate_customer(self, conn, *, company_id: int, customer_id: int | None) -> None:
        if customer_id is None:
            return
        exists = conn.execute(
            "SELECT 1 FROM customers WHERE id = ? AND company_id = ?",
            (customer_id, company_id),
        ).fetchone()
        if not exists:
            raise KeyError("Customer not found")

    def create_call_log(
        self,
        *,
        company_id: int,
        direction: str,
        phone_number: str | None = None,
        customer_id: int | None = None,
        duration_seconds: int = 0,
        status: str = "completed",
        notes: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        direction = (direction or "").strip().lower()
        if direction not in DIRECTIONS:
            raise ValueError(f'"{direction}" is not a valid direction. Choose one of: {", ".join(DIRECTIONS)}.')
        status = (status or "completed").strip().lower()
        if status not in STATUSES:
            raise ValueError(f'"{status}" is not a valid status. Choose one of: {", ".join(STATUSES)}.')
        if duration_seconds < 0:
            raise ValueError("Duration cannot be negative.")
        phone_number = (phone_number or "").strip() or None
        if not phone_number and customer_id is None:
            raise ValueError("Provide a phone number or a linked contact.")

        now = utc_now_iso()
        with db.connect() as conn:
            self._validate_customer(conn, company_id=company_id, customer_id=customer_id)
            cursor = conn.execute(
                """
                INSERT INTO call_logs (
                    company_id, customer_id, direction, phone_number, duration_seconds,
                    status, notes, called_by_user_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (company_id, customer_id, direction, phone_number, duration_seconds, status, notes, actor_user_id, now),
            )
            call_id = int(cursor.lastrowid)
            conn.commit()
        return self.get_call_log(company_id=company_id, call_id=call_id)

    def list_call_logs(
        self, *, company_id: int, customer_id: int | None = None, direction: str | None = None, status: str | None = None,
    ) -> dict[str, Any]:
        where = ["cl.company_id = ?"]
        params: list[Any] = [company_id]
        if customer_id is not None:
            where.append("cl.customer_id = ?")
            params.append(customer_id)
        if direction:
            where.append("cl.direction = ?")
            params.append(direction)
        if status:
            where.append("cl.status = ?")
            params.append(status)
        clause = " AND ".join(where)

        with db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT cl.*, COALESCE(c.display_name, c.internal_name) AS customer_name,
                       COALESCE(u.full_name, u.email) AS called_by_name
                FROM call_logs cl
                LEFT JOIN customers c ON c.id = cl.customer_id
                LEFT JOIN users u ON u.id = cl.called_by_user_id
                WHERE {clause}
                ORDER BY cl.created_at DESC, cl.id DESC
                """,
                params,
            ).fetchall()
        items = [dict(row) for row in rows]
        return {"items": items, "total": len(items)}

    def get_call_log(self, *, company_id: int, call_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT cl.*, COALESCE(c.display_name, c.internal_name) AS customer_name,
                       COALESCE(u.full_name, u.email) AS called_by_name
                FROM call_logs cl
                LEFT JOIN customers c ON c.id = cl.customer_id
                LEFT JOIN users u ON u.id = cl.called_by_user_id
                WHERE cl.id = ? AND cl.company_id = ?
                """,
                (call_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Call log not found")
        return dict(row)

    def delete_call_log(self, *, company_id: int, call_id: int) -> None:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM call_logs WHERE id = ? AND company_id = ?",
                (call_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Call log not found")


call_log_service = CallLogService()
