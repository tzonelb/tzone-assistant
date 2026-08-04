"""Company-scoped CRUD for the Tasks module: tasks/follow-ups/payments/
services/internal cases assigned to team members. Mirrors the layered
service pattern in customer_service.py (the `tasks` table itself lives in
database/database.py's central schema init -- unlike customer_service.py,
this module does not own/create its own tables, so it has no ensure_schema()
of its own to call at startup or in tests)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ALLOWED_STATUSES = {"open", "in_progress", "done", "cancelled"}
ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}


class TaskConflictError(Exception):
    """Raised when an update's optimistic-concurrency token is stale, i.e.
    the task was changed by someone else since the client loaded it."""


class TaskValidationError(ValueError):
    """Raised for invalid field values: a bad status/priority code, an empty
    title, or an assignee/related customer that does not belong to this
    company (cross-tenant references are never allowed, even by id)."""


_TASK_SELECT = """
    SELECT
        t.*,
        assignee.full_name AS assignee_name,
        assignee.email AS assignee_email,
        creator.full_name AS created_by_name,
        customer.display_name AS related_customer_name
    FROM tasks t
    LEFT JOIN users assignee ON assignee.id = t.assignee_user_id
    LEFT JOIN users creator ON creator.id = t.created_by
    LEFT JOIN customers customer ON customer.id = t.related_customer_id
"""


class TaskService:
    EDITABLE_FIELDS = {
        "title",
        "description",
        "status",
        "priority",
        "assignee_user_id",
        "due_date",
        "related_customer_id",
    }

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _clean_values(self, values: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in values.items():
            if key not in self.EDITABLE_FIELDS:
                continue
            if key in ("title", "description", "due_date"):
                cleaned[key] = self._clean_text(value)
            elif key == "status":
                status_value = self._clean_text(value)
                status_value = (status_value or "open").lower()
                if status_value not in ALLOWED_STATUSES:
                    raise TaskValidationError(
                        f"status must be one of {sorted(ALLOWED_STATUSES)}"
                    )
                cleaned[key] = status_value
            elif key == "priority":
                priority_value = self._clean_text(value)
                priority_value = (priority_value or "normal").lower()
                if priority_value not in ALLOWED_PRIORITIES:
                    raise TaskValidationError(
                        f"priority must be one of {sorted(ALLOWED_PRIORITIES)}"
                    )
                cleaned[key] = priority_value
            else:
                # assignee_user_id / related_customer_id: ints or None.
                cleaned[key] = value
        return cleaned

    def _validate_assignee(
        self, conn, company_id: int, assignee_user_id: int | None
    ) -> None:
        if assignee_user_id is None:
            return
        row = conn.execute(
            """
            SELECT 1 FROM company_users
            WHERE company_id = ? AND user_id = ? AND status = 'active'
            LIMIT 1
            """,
            (company_id, assignee_user_id),
        ).fetchone()
        if not row:
            raise TaskValidationError(
                "Assignee must be an active member of this company."
            )

    def _validate_related_customer(
        self, conn, company_id: int, related_customer_id: int | None
    ) -> None:
        if related_customer_id is None:
            return
        row = conn.execute(
            "SELECT 1 FROM customers WHERE id = ? AND company_id = ? LIMIT 1",
            (related_customer_id, company_id),
        ).fetchone()
        if not row:
            raise TaskValidationError(
                "Related customer must belong to this company."
            )

    def list_tasks(
        self,
        *,
        company_id: int,
        status: str | None = None,
        assignee_user_id: int | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["t.company_id = ?"]
        params: list[Any] = [company_id]

        if status and status != "all":
            where.append("t.status = ?")
            params.append(status)

        if assignee_user_id is not None:
            where.append("t.assignee_user_id = ?")
            params.append(assignee_user_id)

        if search and search.strip():
            pattern = f"%{search.strip()}%"
            where.append("(t.title LIKE ? OR t.description LIKE ?)")
            params.extend([pattern, pattern])

        clause = " AND ".join(where)

        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM tasks t WHERE {clause}", params
            ).fetchone()["total"]

            rows = conn.execute(
                f"""
                {_TASK_SELECT}
                WHERE {clause}
                ORDER BY
                    CASE t.status
                        WHEN 'open' THEN 0
                        WHEN 'in_progress' THEN 1
                        WHEN 'done' THEN 2
                        ELSE 3
                    END,
                    (t.due_date IS NULL OR t.due_date = ''),
                    t.due_date ASC,
                    t.created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(500, limit)), max(0, offset)],
            ).fetchall()

        return {"items": [dict(row) for row in rows], "total": int(total or 0)}

    def get_task(self, *, company_id: int, task_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                f"{_TASK_SELECT} WHERE t.id = ? AND t.company_id = ? LIMIT 1",
                (task_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Task not found")
        return dict(row)

    def create_task(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        cleaned = self._clean_values(values)
        title = cleaned.get("title")
        if not title:
            raise TaskValidationError("title is required")

        status_value = cleaned.get("status") or "open"
        priority_value = cleaned.get("priority") or "normal"
        now = utc_now_iso()

        with db.connect() as conn:
            self._validate_assignee(conn, company_id, cleaned.get("assignee_user_id"))
            self._validate_related_customer(
                conn, company_id, cleaned.get("related_customer_id")
            )

            cursor = conn.execute(
                """
                INSERT INTO tasks (
                    company_id, title, description, status, priority,
                    assignee_user_id, due_date, related_customer_id,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    title,
                    cleaned.get("description"),
                    status_value,
                    priority_value,
                    cleaned.get("assignee_user_id"),
                    cleaned.get("due_date"),
                    cleaned.get("related_customer_id"),
                    actor_user_id,
                    now,
                    now,
                ),
            )
            task_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_task(company_id=company_id, task_id=task_id)

    def update_task(
        self,
        *,
        company_id: int,
        task_id: int,
        values: dict[str, Any],
        actor_user_id: int | None,
        expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        cleaned = self._clean_values(values)
        now = utc_now_iso()

        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id, updated_at FROM tasks WHERE id = ? AND company_id = ?",
                (task_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Task not found")

            # Optimistic concurrency: if the caller told us which version they
            # were editing, refuse to overwrite a record that has since moved
            # on. Runs even for a no-op save so a stale editor is always told
            # to reload rather than silently "succeeding".
            if (
                expected_updated_at is not None
                and str(existing["updated_at"]) != str(expected_updated_at)
            ):
                raise TaskConflictError(
                    "This task was changed elsewhere. Reload to see the "
                    "latest details before editing."
                )

            if "title" in cleaned and not cleaned["title"]:
                raise TaskValidationError("title cannot be empty")

            if not cleaned:
                return self.get_task(company_id=company_id, task_id=task_id)

            if "assignee_user_id" in cleaned:
                self._validate_assignee(conn, company_id, cleaned["assignee_user_id"])
            if "related_customer_id" in cleaned:
                self._validate_related_customer(
                    conn, company_id, cleaned["related_customer_id"]
                )

            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE tasks SET {assignments}, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, task_id, company_id],
            )
            conn.commit()

        return self.get_task(company_id=company_id, task_id=task_id)

    def delete_task(self, *, company_id: int, task_id: int) -> bool:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM tasks WHERE id = ? AND company_id = ?",
                (task_id, company_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_assignable_users(self, *, company_id: int) -> list[dict[str, Any]]:
        """Active members of this company, for the assignee picker. Kept
        local to the tasks module (rather than reusing conversations.py's
        private `_company_employees`) so tasks.view is the only permission
        this needs -- it does not require conversations.view."""
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    users.id,
                    users.full_name,
                    users.email,
                    roles.name AS role_name
                FROM company_users
                JOIN users ON users.id = company_users.user_id
                LEFT JOIN roles ON roles.id = company_users.role_id
                WHERE company_users.company_id = ?
                  AND company_users.status = 'active'
                  AND users.status = 'active'
                ORDER BY users.full_name ASC, users.email ASC
                """,
                (company_id,),
            ).fetchall()

        return [
            {
                **dict(row),
                "display_name": row["full_name"] or row["email"] or f"User {row['id']}",
            }
            for row in rows
        ]


task_service = TaskService()
