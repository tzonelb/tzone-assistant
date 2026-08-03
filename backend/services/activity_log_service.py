from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActivityLogService:
    """A real, company-scoped activity log a manager can review — separate
    from platform_admin_service's audit_logs (that one is super-admin/
    platform-level only: company creation, plan changes, module toggles)
    and from each conversation's own Timeline (already covers per-
    conversation events like takeover/status/department changes in full).
    This one covers the meaningful actions employees take OUTSIDE a single
    conversation: task management, customer edits, catalogue changes,
    broadcasts sent, role/permission changes, and logins — the things an
    owner/admin would actually want a cross-company trail of."""

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS company_activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    actor_user_id INTEGER,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_company_activity_log_company_created "
                "ON company_activity_log (company_id, created_at DESC)"
            )
            conn.commit()

    def record(
        self, *, company_id: int, actor_user_id: int | None, action: str,
        entity_type: str, entity_id: int | None = None, description: str = "",
    ) -> None:
        """Fire-and-forget by contract for every caller (each call site
        wraps this in its own try/except) — a logging failure must never
        block the real action it's describing."""
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO company_activity_log (
                    company_id, actor_user_id, action, entity_type, entity_id, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (company_id, actor_user_id, action, entity_type, entity_id, description, utc_now_iso()),
            )
            conn.commit()

    def list_for_company(
        self, *, company_id: int, actor_user_id: int | None = None,
        action: str | None = None, before_id: int | None = None, limit: int = 100,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        where = ["cal.company_id = ?"]
        params: list[Any] = [company_id]
        if actor_user_id is not None:
            where.append("cal.actor_user_id = ?")
            params.append(actor_user_id)
        if action:
            where.append("cal.action = ?")
            params.append(action)
        if before_id is not None:
            where.append("cal.id < ?")
            params.append(before_id)
        clause = " AND ".join(where)

        with db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT cal.*, COALESCE(u.full_name, u.email, 'System') AS actor_name
                FROM company_activity_log cal
                LEFT JOIN users u ON u.id = cal.actor_user_id
                WHERE {clause}
                ORDER BY cal.id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            action_rows = conn.execute(
                "SELECT DISTINCT action FROM company_activity_log WHERE company_id = ? ORDER BY action",
                (company_id,),
            ).fetchall()

        return {
            "items": [dict(row) for row in rows],
            "actions": [row["action"] for row in action_rows],
        }


activity_log_service = ActivityLogService()
