"""Support tickets and team tasks, stored inside the owning company's database.

A ticket raised by the assistant and a task an employee writes down are the same
record: one row in ``tickets``. The task columns — ``title``, ``task_type``,
``due_date``, ``created_by_user_id`` and ``closed_at`` — are what turn a ticket
into something a team can plan around, and ``ticket_comments`` is the thread
hanging off it.

``assigned_user_id``, ``created_by_user_id`` and ``author_user_id`` point at rows
in the control-plane database. They are stored as plain integers and resolved
through ``auth_service.user_display_names`` where a name is actually needed: a
tenant database is a separate encrypted file and cannot join to ``users``.

Table creation belongs to ``database/schema_tenant.py`` alone. This service only
reads and writes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TicketService:
    ALLOWED_STATUS = ("open", "in_progress", "resolved", "closed")
    ALLOWED_PRIORITY = ("low", "normal", "high", "urgent")
    ALLOWED_TASK_TYPE = (
        "support",
        "task",
        "follow_up",
        "maintenance",
        "delivery",
        "internal",
    )

    # A task in one of these is finished. However far its due date is in the
    # past it is not overdue any more, or every completed task would sit in the
    # overdue list forever and the filter would stop meaning anything.
    DONE_STATUS = ("resolved", "closed")

    # Fields the tasks screen is allowed to write. Everything else on the row —
    # the channel identity the assistant collected, the company id — is set once
    # at creation and never edited from a form.
    TASK_FIELDS = (
        "title",
        "task_type",
        "priority",
        "status",
        "due_date",
        "assigned_user_id",
        "department",
        "problem",
    )

    MAX_LIMIT = 200
    MAX_COMMENT = 4000

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None

        text = str(value).strip()
        return text or None

    @staticmethod
    def _optional_user_id(value: Any) -> int | None:
        if value in (None, "", 0, "0"):
            return None

        return int(value)

    def _normalize_status(self, value: Any, default: str = "open") -> str:
        status = (self._clean(value) or default).lower()

        if status not in self.ALLOWED_STATUS:
            raise ValueError(
                f"Status must be one of: {', '.join(self.ALLOWED_STATUS)}."
            )

        return status

    def _normalize_priority(self, value: Any, default: str = "normal") -> str:
        priority = (self._clean(value) or default).lower()

        if priority not in self.ALLOWED_PRIORITY:
            raise ValueError(
                f"Priority must be one of: {', '.join(self.ALLOWED_PRIORITY)}."
            )

        return priority

    def _normalize_task_type(self, value: Any, default: str = "task") -> str:
        task_type = (self._clean(value) or default).lower()

        if task_type not in self.ALLOWED_TASK_TYPE:
            raise ValueError(
                f"Task type must be one of: {', '.join(self.ALLOWED_TASK_TYPE)}."
            )

        return task_type

    def _normalize_due_date(self, value: Any) -> str | None:
        """Store every due date as one comparable UTC timestamp.

        Overdue is decided by comparing strings in SQL, which only works if
        every row is written in the same shape. A bare ``2026-04-20`` is read as
        the end of that day: a task due "today" is not late at one minute past
        midnight, which is what a plain string comparison would otherwise claim.
        """
        text = self._clean(value)

        if not text:
            return None

        if len(text) == 10:
            text = f"{text}T23:59:59"

        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "Due date must be an ISO date (2026-04-20) or timestamp."
            ) from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc).isoformat()

    @classmethod
    def _is_overdue(cls, task: dict[str, Any], now: str) -> bool:
        due_date = task.get("due_date")

        if not due_date:
            return False

        if str(task.get("status") or "") in cls.DONE_STATUS:
            return False

        return str(due_date) < now

    def _decorate(self, row: Any, now: str | None = None) -> dict[str, Any]:
        """Return the row as a plain dict with `is_overdue` already decided.

        The screen must not recompute this from the raw due date: the browser's
        clock is the customer's clock, and a task would change colour depending
        on which machine it was opened from.
        """
        task = dict(row)
        task["is_overdue"] = self._is_overdue(task, now or utc_now_iso())
        return task

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------

    def create(self, *, company_id: int, data: dict[str, Any]) -> int:
        """Insert one ticket row and return its id.

        Still returns the id rather than the row: the assistant's escalation
        path in ``core/engine.py`` shows that number to the customer.
        """
        company_id = int(company_id)
        now = utc_now_iso()

        status = self._normalize_status(data.get("status"), default="open")

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                INSERT INTO tickets (
                    company_id, conversation_id, title, task_type, due_date,
                    created_by_user_id, closed_at, platform, user_id, language,
                    department, iptv_username, device, os, app, problem,
                    assigned_user_id, status, priority, created_at, updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    company_id,
                    data.get("conversation_id"),
                    self._clean(data.get("title")),
                    self._normalize_task_type(
                        data.get("task_type"), default="support"
                    ),
                    self._normalize_due_date(data.get("due_date")),
                    self._optional_user_id(data.get("created_by_user_id")),
                    now if status in self.DONE_STATUS else None,
                    data.get("platform"),
                    data.get("user_id"),
                    data.get("language"),
                    data.get("department", "support"),
                    data.get("iptv_username"),
                    data.get("device"),
                    data.get("os"),
                    data.get("app"),
                    data.get("problem"),
                    self._optional_user_id(data.get("assigned_user_id")),
                    status,
                    self._normalize_priority(data.get("priority")),
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

        now = utc_now_iso()

        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                """
                UPDATE tickets
                SET status = ?,
                    assigned_user_id = ?,
                    closed_at = CASE
                        WHEN ? IN ('resolved', 'closed')
                        THEN COALESCE(closed_at, ?)
                        ELSE NULL
                    END,
                    updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (
                    status,
                    assigned_user_id,
                    status,
                    now,
                    now,
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

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def create_task(
        self,
        *,
        company_id: int,
        data: dict[str, Any],
        created_by_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a task from the tasks screen and return the stored row.

        A title is required here and not on ``create``: a ticket opened by the
        assistant is identified by the conversation behind it, but a task with
        no title is a line in a list nobody can act on.
        """
        title = self._clean(data.get("title"))

        if not title:
            raise ValueError("Give this task a title.")

        payload = dict(data)
        payload["title"] = title
        payload["task_type"] = self._normalize_task_type(
            data.get("task_type"), default="task"
        )
        payload["department"] = self._clean(data.get("department"))
        payload["created_by_user_id"] = created_by_user_id

        task_id = self.create(company_id=company_id, data=payload)

        logger.info(
            "Created task id=%s company id=%s actor id=%s",
            task_id,
            company_id,
            created_by_user_id,
        )

        return self.get_task(company_id=company_id, task_id=task_id)

    def get_task(self, *, company_id: int, task_id: int) -> dict[str, Any]:
        row = self.get(company_id=company_id, ticket_id=task_id)

        if not row:
            raise KeyError("Task not found")

        return self._decorate(row)

    def update_task(
        self,
        *,
        company_id: int,
        task_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply a partial edit and return the stored row.

        The company id is part of the WHERE clause and not only of the file that
        was opened, so a task id guessed from another company matches nothing
        and raises instead of silently editing row number one.
        """
        company_id = int(company_id)
        task_id = int(task_id)

        cleaned: dict[str, Any] = {}

        for field in self.TASK_FIELDS:
            if field not in values:
                continue

            value = values[field]

            if field == "title":
                title = self._clean(value)
                if not title:
                    raise ValueError("Give this task a title.")
                cleaned[field] = title
            elif field == "task_type":
                cleaned[field] = self._normalize_task_type(value)
            elif field == "priority":
                cleaned[field] = self._normalize_priority(value)
            elif field == "status":
                cleaned[field] = self._normalize_status(value)
            elif field == "due_date":
                cleaned[field] = self._normalize_due_date(value)
            elif field == "assigned_user_id":
                cleaned[field] = self._optional_user_id(value)
            else:
                cleaned[field] = self._clean(value)

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                "SELECT id, status, closed_at FROM tickets WHERE id = ? AND company_id = ? LIMIT 1",
                (task_id, company_id),
            ).fetchone()

            if not existing:
                raise KeyError("Task not found")

            if cleaned:
                if "status" in cleaned:
                    if cleaned["status"] in self.DONE_STATUS:
                        # Keep the first closing time: reporting on how long a
                        # task took must not be reset by a later edit.
                        if not existing["closed_at"]:
                            cleaned["closed_at"] = now
                    else:
                        cleaned["closed_at"] = None

                assignments = ", ".join(f"{key} = ?" for key in cleaned)

                conn.execute(
                    f"""
                    UPDATE tickets
                    SET {assignments}, updated_at = ?
                    WHERE id = ? AND company_id = ?
                    """,
                    [*cleaned.values(), now, task_id, company_id],
                )
                conn.commit()

        logger.info(
            "Updated task id=%s company id=%s fields=%s",
            task_id,
            company_id,
            sorted(cleaned),
        )

        return self.get_task(company_id=company_id, task_id=task_id)

    def assign_task(
        self,
        *,
        company_id: int,
        task_id: int,
        assigned_user_id: int | None,
    ) -> dict[str, Any]:
        """Hand a task to an employee, or clear the assignment with None."""
        return self.update_task(
            company_id=company_id,
            task_id=task_id,
            values={"assigned_user_id": assigned_user_id},
        )

    def change_status(
        self,
        *,
        company_id: int,
        task_id: int,
        status: str,
    ) -> dict[str, Any]:
        return self.update_task(
            company_id=company_id,
            task_id=task_id,
            values={"status": status},
        )

    def _task_filters(
        self,
        *,
        company_id: int,
        status: str | None,
        task_type: str | None,
        priority: str | None,
        assigned_user_id: int | None,
        unassigned: bool,
        overdue: bool | None,
        search: str | None,
        now: str,
    ) -> tuple[str, list[Any]]:
        where = ["company_id = ?"]
        params: list[Any] = [int(company_id)]

        status = self._clean(status)
        if status:
            where.append("status = ?")
            params.append(self._normalize_status(status))

        task_type = self._clean(task_type)
        if task_type:
            where.append("task_type = ?")
            params.append(self._normalize_task_type(task_type))

        priority = self._clean(priority)
        if priority:
            where.append("priority = ?")
            params.append(self._normalize_priority(priority))

        if unassigned:
            where.append("assigned_user_id IS NULL")
        elif assigned_user_id is not None:
            where.append("assigned_user_id = ?")
            params.append(int(assigned_user_id))

        if overdue is not None:
            overdue_clause = (
                "(due_date IS NOT NULL AND due_date <> '' AND due_date < ? "
                "AND status NOT IN ('resolved', 'closed'))"
            )
            where.append(overdue_clause if overdue else f"NOT {overdue_clause}")
            params.append(now)

        search = self._clean(search)
        if search:
            pattern = f"%{search}%"
            where.append(
                "(title LIKE ? OR problem LIKE ? OR department LIKE ? "
                "OR iptv_username LIKE ? OR user_id LIKE ?)"
            )
            params.extend([pattern] * 5)

        return " AND ".join(where), params

    def list_tasks(
        self,
        *,
        company_id: int,
        status: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
        assigned_user_id: int | None = None,
        unassigned: bool = False,
        overdue: bool | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List this company's tasks, newest deadline first.

        ``assigned_user_id`` is also what the "my tasks" filter is built from:
        the router passes the caller's own id, so the browser never has to send
        a user id it could have changed.
        """
        company_id = int(company_id)
        limit = max(1, min(int(limit), self.MAX_LIMIT))
        offset = max(0, int(offset))
        now = utc_now_iso()

        clause, params = self._task_filters(
            company_id=company_id,
            status=status,
            task_type=task_type,
            priority=priority,
            assigned_user_id=assigned_user_id,
            unassigned=unassigned,
            overdue=overdue,
            search=search,
            now=now,
        )

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
                ORDER BY (due_date IS NULL), due_date ASC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return {
            "items": [self._decorate(row, now) for row in rows],
            "total": total,
        }

    def task_counts(
        self,
        *,
        company_id: int,
        assigned_user_id: int | None = None,
    ) -> dict[str, int]:
        """Counts for the header tiles, with every status present.

        Missing keys are filled with zero on purpose: a tile that disappears
        when its count reaches zero makes the row of tiles move under the
        pointer.
        """
        company_id = int(company_id)
        now = utc_now_iso()

        where = ["company_id = ?"]
        params: list[Any] = [company_id]

        if assigned_user_id is not None:
            where.append("assigned_user_id = ?")
            params.append(int(assigned_user_id))

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                f"""
                SELECT status, COUNT(*) AS total FROM tickets
                WHERE {clause}
                GROUP BY status
                """,
                params,
            ).fetchall()
            overdue = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS total FROM tickets
                    WHERE {clause}
                      AND due_date IS NOT NULL
                      AND due_date <> ''
                      AND due_date < ?
                      AND status NOT IN ('resolved', 'closed')
                    """,
                    [*params, now],
                ).fetchone()["total"]
            )

        counts = {status: 0 for status in self.ALLOWED_STATUS}

        for row in rows:
            counts[str(row["status"])] = int(row["total"])

        counts["total"] = sum(counts[status] for status in self.ALLOWED_STATUS)
        counts["overdue"] = overdue
        return counts

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def add_comment(
        self,
        *,
        company_id: int,
        task_id: int,
        author_user_id: int | None,
        body: str,
    ) -> dict[str, Any]:
        """Append one comment to a task this company owns.

        The task is looked up under the company id first. Without that check a
        comment could be filed against an id belonging to another company and
        would sit in this database pointing at nothing.
        """
        company_id = int(company_id)
        task_id = int(task_id)

        text = self._clean(body)

        if not text:
            raise ValueError("Write something before posting the comment.")

        if len(text) > self.MAX_COMMENT:
            raise ValueError(
                f"A comment cannot be longer than {self.MAX_COMMENT} characters."
            )

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            task = conn.execute(
                "SELECT id FROM tickets WHERE id = ? AND company_id = ? LIMIT 1",
                (task_id, company_id),
            ).fetchone()

            if not task:
                raise KeyError("Task not found")

            cursor = conn.execute(
                """
                INSERT INTO ticket_comments (
                    company_id, ticket_id, author_user_id, body, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    task_id,
                    self._optional_user_id(author_user_id),
                    text,
                    now,
                ),
            )
            conn.execute(
                "UPDATE tickets SET updated_at = ? WHERE id = ? AND company_id = ?",
                (now, task_id, company_id),
            )
            conn.commit()
            comment_id = int(cursor.lastrowid)

            row = conn.execute(
                "SELECT * FROM ticket_comments WHERE id = ? AND company_id = ? LIMIT 1",
                (comment_id, company_id),
            ).fetchone()

        return dict(row)

    def list_comments(
        self,
        *,
        company_id: int,
        task_id: int,
    ) -> list[dict[str, Any]]:
        """The thread, oldest first, for tasks this company owns."""
        company_id = int(company_id)
        task_id = int(task_id)

        with database_manager.tenant(company_id) as conn:
            task = conn.execute(
                "SELECT id FROM tickets WHERE id = ? AND company_id = ? LIMIT 1",
                (task_id, company_id),
            ).fetchone()

            if not task:
                raise KeyError("Task not found")

            rows = conn.execute(
                """
                SELECT * FROM ticket_comments
                WHERE ticket_id = ? AND company_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (task_id, company_id),
            ).fetchall()

        return [dict(row) for row in rows]


ticket_service = TicketService()
