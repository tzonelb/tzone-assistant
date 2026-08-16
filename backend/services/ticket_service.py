"""Support tickets, stored inside the owning company's database."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TicketService:
    ALLOWED_STATUS = ("open", "in_progress", "resolved", "closed")

    def create(self, *, company_id: int, data: dict[str, Any]) -> int:
        company_id = int(company_id)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                INSERT INTO tickets (
                    company_id, conversation_id, platform, user_id, language,
                    department, iptv_username, device, os, app, problem,
                    status, priority, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    data.get("conversation_id"),
                    data.get("platform"),
                    data.get("user_id"),
                    data.get("language"),
                    data.get("department", "support"),
                    data.get("iptv_username"),
                    data.get("device"),
                    data.get("os"),
                    data.get("app"),
                    data.get("problem"),
                    data.get("status", "open"),
                    data.get("priority", "normal"),
                    now,
                    now,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list(
        self,
        *,
        company_id: int,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))

        where = ["company_id = ?"]
        params: list[Any] = [company_id]

        if status and status in self.ALLOWED_STATUS:
            where.append("status = ?")
            params.append(status)

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM tickets WHERE {clause}", params
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT * FROM tickets
                WHERE {clause}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return {"items": [dict(row) for row in rows], "total": total}

    def get(self, *, company_id: int, ticket_id: int) -> dict[str, Any] | None:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                "SELECT * FROM tickets WHERE id = ? AND company_id = ? LIMIT 1",
                (int(ticket_id), int(company_id)),
            ).fetchone()

        return dict(row) if row else None

    def update_status(
        self,
        *,
        company_id: int,
        ticket_id: int,
        status: str,
        assigned_user_id: int | None = None,
    ) -> bool:
        if status not in self.ALLOWED_STATUS:
            raise ValueError(
                f"Status must be one of: {', '.join(self.ALLOWED_STATUS)}."
            )

        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                """
                UPDATE tickets
                SET status = ?, assigned_user_id = ?, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (
                    status,
                    assigned_user_id,
                    utc_now_iso(),
                    int(ticket_id),
                    int(company_id),
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def count_by_status(self, company_id: int) -> dict[str, int]:
        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM tickets GROUP BY status"
            ).fetchall()

        return {str(row["status"]): int(row["total"]) for row in rows}


ticket_service = TicketService()
