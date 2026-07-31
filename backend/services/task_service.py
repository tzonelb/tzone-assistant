from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Tasks are a company's internal work list — follow-ups, payments, services
# and internal cases assigned to the team. Status/priority/type are fixed,
# small enumerations (unlike Departments, which are free-form per company)
# so the UI can render predictable filters/selects without per-company config.
STATUSES = ["open", "in_progress", "done", "cancelled"]
PRIORITIES = ["low", "normal", "high", "urgent"]
TASK_TYPES = ["follow_up", "complaint", "service_request", "sales_inquiry", "internal", "other"]
DEFAULT_STATUS = "open"
DEFAULT_PRIORITY = "normal"
DEFAULT_TASK_TYPE = "other"


class TaskService:
    def __init__(self) -> None:
        # Schema setup happens explicitly via main.py's lifespan (after
        # database.database.db.create_tables()), not here — see the
        # matching note in CustomerService.__init__.
        pass

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    task_type TEXT NOT NULL DEFAULT 'other',
                    status TEXT NOT NULL DEFAULT 'open',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    assigned_user_id INTEGER,
                    customer_id INTEGER,
                    conversation_id INTEGER,
                    due_at TEXT,
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(assigned_user_id) REFERENCES users(id) ON DELETE SET NULL,
                    FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE SET NULL,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "task_type" not in existing_columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'other'")
            if "conversation_id" not in existing_columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN conversation_id INTEGER")
            conn.commit()

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _validate_assignee(self, conn: Any, *, company_id: int, assigned_user_id: int) -> None:
        is_company_employee = conn.execute(
            "SELECT 1 FROM company_users WHERE company_id = ? AND user_id = ? AND status = 'active'",
            (company_id, assigned_user_id),
        ).fetchone()
        if not is_company_employee:
            raise ValueError("Assigned user must be an active employee of this company.")

    def _validate_customer(self, conn: Any, *, company_id: int, customer_id: int) -> None:
        exists = conn.execute(
            "SELECT id FROM customers WHERE id = ? AND company_id = ?",
            (customer_id, company_id),
        ).fetchone()
        if not exists:
            raise KeyError("Customer not found")

    def _validate_conversation(self, conn: Any, *, company_id: int, conversation_id: int) -> None:
        exists = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND company_id = ?",
            (conversation_id, company_id),
        ).fetchone()
        if not exists:
            raise KeyError("Conversation not found")

    def create_task(
        self,
        *,
        company_id: int,
        title: str,
        description: str | None = None,
        task_type: str = DEFAULT_TASK_TYPE,
        priority: str = DEFAULT_PRIORITY,
        assigned_user_id: int | None = None,
        customer_id: int | None = None,
        conversation_id: int | None = None,
        due_at: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        clean_title = self._clean(title)
        if not clean_title:
            raise ValueError("Task title is required.")

        clean_priority = str(priority or DEFAULT_PRIORITY).strip().lower()
        if clean_priority not in PRIORITIES:
            raise ValueError(f'"{clean_priority}" is not a valid priority. Choose one of: {", ".join(PRIORITIES)}.')

        clean_task_type = str(task_type or DEFAULT_TASK_TYPE).strip().lower()
        if clean_task_type not in TASK_TYPES:
            raise ValueError(f'"{clean_task_type}" is not a valid task type. Choose one of: {", ".join(TASK_TYPES)}.')

        description = self._clean(description)
        due_at = self._clean(due_at)
        now = utc_now_iso()

        with db.connect() as conn:
            if assigned_user_id is not None:
                self._validate_assignee(conn, company_id=company_id, assigned_user_id=assigned_user_id)
            if customer_id is not None:
                self._validate_customer(conn, company_id=company_id, customer_id=customer_id)
            if conversation_id is not None:
                self._validate_conversation(conn, company_id=company_id, conversation_id=conversation_id)

            cursor = conn.execute(
                """
                INSERT INTO tasks (
                    company_id, title, description, task_type, status, priority,
                    assigned_user_id, customer_id, conversation_id, due_at, created_by_user_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id, clean_title, description, clean_task_type, DEFAULT_STATUS, clean_priority,
                    assigned_user_id, customer_id, conversation_id, due_at, actor_user_id, now, now,
                ),
            )
            task_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_task(company_id=company_id, task_id=task_id)

    @staticmethod
    def _enriched_select(where_clause: str) -> str:
        return f"""
            SELECT t.*,
                   (SELECT COALESCE(u.full_name, u.email) FROM users u WHERE u.id = t.assigned_user_id) AS assigned_user_name,
                   (SELECT COALESCE(c.display_name, c.internal_name) FROM customers c WHERE c.id = t.customer_id) AS customer_name,
                   (SELECT conv.channel FROM conversations conv WHERE conv.id = t.conversation_id) AS conversation_channel,
                   (SELECT conv.external_user_id FROM conversations conv WHERE conv.id = t.conversation_id) AS conversation_external_user_id
            FROM tasks t
            WHERE {where_clause}
        """

    def get_task(self, *, company_id: int, task_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                self._enriched_select("t.id = ? AND t.company_id = ?") + " LIMIT 1",
                (task_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Task not found")
        return dict(row)

    def list_tasks(
        self,
        *,
        company_id: int,
        status: str | None = None,
        assigned_user_id: int | None = None,
        customer_id: int | None = None,
    ) -> dict[str, Any]:
        where = ["t.company_id = ?"]
        params: list[Any] = [company_id]

        if status is not None:
            where.append("t.status = ?")
            params.append(str(status).strip().lower())

        if assigned_user_id is not None:
            where.append("t.assigned_user_id = ?")
            params.append(assigned_user_id)

        if customer_id is not None:
            where.append("t.customer_id = ?")
            params.append(customer_id)

        clause = " AND ".join(where)
        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM tasks t WHERE {clause}", params
            ).fetchone()["total"]
            rows = conn.execute(
                self._enriched_select(clause)
                + """
                ORDER BY
                    CASE WHEN t.status IN ('done', 'cancelled') THEN 1 ELSE 0 END ASC,
                    CASE WHEN t.due_at IS NULL THEN 1 ELSE 0 END ASC,
                    t.due_at ASC,
                    t.created_at DESC
                """,
                params,
            ).fetchall()

        items = [dict(row) for row in rows]
        return {"items": items, "total": int(total or 0)}

    def update_task(
        self,
        *,
        company_id: int,
        task_id: int,
        values: dict[str, Any],
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}

        if "title" in values:
            clean_title = self._clean(values["title"])
            if not clean_title:
                raise ValueError("Task title is required.")
            cleaned["title"] = clean_title

        if "description" in values:
            cleaned["description"] = self._clean(values["description"])

        if "due_at" in values:
            cleaned["due_at"] = self._clean(values["due_at"])

        if "task_type" in values and values["task_type"] is not None:
            task_type = str(values["task_type"]).strip().lower()
            if task_type not in TASK_TYPES:
                raise ValueError(f'"{task_type}" is not a valid task type. Choose one of: {", ".join(TASK_TYPES)}.')
            cleaned["task_type"] = task_type

        if "priority" in values and values["priority"] is not None:
            priority = str(values["priority"]).strip().lower()
            if priority not in PRIORITIES:
                raise ValueError(f'"{priority}" is not a valid priority. Choose one of: {", ".join(PRIORITIES)}.')
            cleaned["priority"] = priority

        status_requested = "status" in values and values["status"] is not None
        new_status = None
        if status_requested:
            new_status = str(values["status"]).strip().lower()
            if new_status not in STATUSES:
                raise ValueError(f'"{new_status}" is not a valid status. Choose one of: {", ".join(STATUSES)}.')

        assign_requested = "assigned_user_id" in values
        assigned_user_id = values.get("assigned_user_id")

        customer_requested = "customer_id" in values
        customer_id = values.get("customer_id")

        if not cleaned and not status_requested and not assign_requested and not customer_requested:
            return self.get_task(company_id=company_id, task_id=task_id)

        now = utc_now_iso()
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id, status FROM tasks WHERE id = ? AND company_id = ?",
                (task_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Task not found")

            if assign_requested and assigned_user_id is not None:
                self._validate_assignee(conn, company_id=company_id, assigned_user_id=assigned_user_id)
            if assign_requested:
                cleaned["assigned_user_id"] = assigned_user_id

            if customer_requested and customer_id is not None:
                self._validate_customer(conn, company_id=company_id, customer_id=customer_id)
            if customer_requested:
                cleaned["customer_id"] = customer_id

            if status_requested:
                cleaned["status"] = new_status
                was_done = existing["status"] == "done"
                if new_status == "done" and not was_done:
                    cleaned["completed_at"] = now
                elif new_status != "done" and was_done:
                    cleaned["completed_at"] = None

            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, task_id, company_id],
            )
            conn.commit()

        return self.get_task(company_id=company_id, task_id=task_id)

    def delete_task(self, *, company_id: int, task_id: int) -> None:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM tasks WHERE id = ? AND company_id = ?",
                (task_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Task not found")


task_service = TaskService()
