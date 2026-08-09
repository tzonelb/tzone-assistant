from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from database.database import db

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_dt(value: Any) -> str | None:
    """Canonicalize a date/time string to UTC ISO with a +00:00 offset so the
    string comparisons in scan_due_tasks (due_at <= now) are correct regardless
    of whether the client sent a naive, Z-suffixed, or offset value. Unparseable
    input is returned as-is (never blocks a save)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


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


def _log_activity(*, company_id: int, actor_user_id: int | None, action: str, entity_id: int | None, description: str) -> None:
    try:
        from backend.services.activity_log_service import activity_log_service
        activity_log_service.record(
            company_id=company_id, actor_user_id=actor_user_id, action=action,
            entity_type="task", entity_id=entity_id, description=description,
        )
    except Exception:
        logger.exception("Could not record activity log entry for task #%s", entity_id)


def _fire_task_reply_flow_trigger(*, company_id: int, customer_id: int | None) -> None:
    """Same fire-and-forget contract as appointment_service/call_log_service's
    equivalent hooks — never raises. A task with no linked customer (a purely
    internal to-do) is a no-op inside fire_event_for_customer itself."""
    try:
        from core.reply_flow_engine import reply_flow_engine
        reply_flow_engine.fire_event_for_customer(company_id=company_id, customer_id=customer_id, trigger_type="task_completed")
    except Exception:
        logger.exception("task_completed reply flow trigger failed for customer #%s", customer_id)


def _notify_task_assigned(*, company_id: int, task_id: int, title: str, assigned_user_id: int, due_at: str | None) -> None:
    """Bell notification to the assignee when a task lands on them. Deduped
    per task+assignee so re-assigning back and forth never spams. Never raises."""
    try:
        from backend.services.notification_service import notification_service
        body = f"Due {due_at}" if due_at else None
        notification_service.create(
            company_id=company_id,
            notification_type="task_assigned",
            title=f'Task assigned: "{title}"',
            body=body,
            recipient_user_id=assigned_user_id,
            severity="info",
            data={"task_id": task_id},
            dedupe_key=f"task_assigned:{task_id}:{assigned_user_id}",
        )
    except Exception:
        logger.exception("task_assigned notification failed for task #%s", task_id)


def _notify_task_completed(*, company_id: int, task_id: int, title: str) -> None:
    """Team-wide bell notification when a task is completed. Never raises."""
    try:
        from backend.services.notification_service import notification_service
        notification_service.create(
            company_id=company_id,
            notification_type="task_completed",
            title=f'Task completed: "{title}"',
            severity="info",
            data={"task_id": task_id},
            dedupe_key=f"task_completed:{task_id}",
        )
    except Exception:
        logger.exception("task_completed notification failed for task #%s", task_id)


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
            # Claim marker for the due-task bell scan: set once a due alert has
            # fired so scan_due_tasks doesn't re-examine the same overdue task
            # every 30s forever. Reset to NULL when due_at is changed.
            if "due_notified_at" not in existing_columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN due_notified_at TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_due_scan ON tasks(status, due_at)"
            )
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
        due_at = normalize_dt(self._clean(due_at))
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

        _log_activity(
            company_id=company_id, actor_user_id=actor_user_id, action="task_created",
            entity_id=task_id, description=f'Created task "{clean_title}"',
        )
        if assigned_user_id is not None:
            _notify_task_assigned(
                company_id=company_id, task_id=task_id, title=clean_title,
                assigned_user_id=assigned_user_id, due_at=due_at,
            )
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
            cleaned["due_at"] = normalize_dt(self._clean(values["due_at"]))

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
                "SELECT id, status, customer_id, title, assigned_user_id FROM tasks WHERE id = ? AND company_id = ?",
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

            # A due-date change re-arms the due bell (a task pushed to
            # tomorrow should alert again when tomorrow arrives).
            if "due_at" in cleaned:
                cleaned["due_notified_at"] = None

            just_completed = False
            completing = False
            if status_requested:
                was_done = existing["status"] == "done"
                if new_status == "done" and not was_done:
                    # Claim the completion atomically below instead of via the
                    # bulk update, so only ONE of two concurrent "mark done"
                    # calls fires the reply-flow trigger (a customer-facing
                    # message) and the completion bell.
                    completing = True
                else:
                    cleaned["status"] = new_status
                    if new_status != "done" and was_done:
                        cleaned["completed_at"] = None

            if cleaned:
                assignments = ", ".join(f"{key} = ?" for key in cleaned)
                conn.execute(
                    f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id = ? AND company_id = ?",
                    [*cleaned.values(), now, task_id, company_id],
                )

            if completing:
                claim = conn.execute(
                    "UPDATE tasks SET status = 'done', completed_at = ?, updated_at = ? "
                    "WHERE id = ? AND company_id = ? AND status != 'done'",
                    (now, now, task_id, company_id),
                )
                just_completed = claim.rowcount == 1

            conn.commit()

        resolved_title = cleaned.get("title", existing["title"])

        if just_completed:
            resolved_customer_id = cleaned.get("customer_id", existing["customer_id"])
            _fire_task_reply_flow_trigger(company_id=company_id, customer_id=resolved_customer_id)
            _notify_task_completed(company_id=company_id, task_id=task_id, title=resolved_title)

        # Notify a newly-assigned user (a real change, not a no-op re-save).
        if assign_requested and assigned_user_id is not None and assigned_user_id != existing["assigned_user_id"]:
            _notify_task_assigned(
                company_id=company_id, task_id=task_id, title=resolved_title,
                assigned_user_id=assigned_user_id, due_at=cleaned.get("due_at"),
            )

        if status_requested:
            _log_activity(
                company_id=company_id, actor_user_id=actor_user_id, action="task_status_changed",
                entity_id=task_id, description=f'Changed task #{task_id} status to "{new_status}"',
            )

        return self.get_task(company_id=company_id, task_id=task_id)

    def delete_task(self, *, company_id: int, task_id: int, actor_user_id: int | None = None) -> None:
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT title FROM tasks WHERE id = ? AND company_id = ?",
                (task_id, company_id),
            ).fetchone()
            cursor = conn.execute(
                "DELETE FROM tasks WHERE id = ? AND company_id = ?",
                (task_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Task not found")

        _log_activity(
            company_id=company_id, actor_user_id=actor_user_id, action="task_deleted",
            entity_id=task_id, description=f'Deleted task "{existing["title"] if existing else task_id}"',
        )

    def scan_due_tasks(self) -> int:
        """Called on the reminder-worker cadence: raise a one-time bell alert
        for every open task whose due time has arrived. Uses the claim marker
        `due_notified_at` (set once fired) so an overdue task is examined ONCE,
        not re-scanned every 30s forever — the same claim-then-act pattern as
        appointment reminders. Never raises; returns count fired."""
        now = utc_now_iso()
        try:
            with db.connect() as conn:
                # Only un-notified, now-due, still-open tasks — the set drains
                # as they're claimed instead of growing without bound.
                rows = conn.execute(
                    """
                    SELECT id, company_id, title, assigned_user_id FROM tasks
                    WHERE status NOT IN ('done', 'cancelled')
                      AND due_at IS NOT NULL AND due_at <= ?
                      AND due_notified_at IS NULL
                    """,
                    (now,),
                ).fetchall()
        except Exception:
            logger.exception("scan_due_tasks query failed")
            return 0

        fired = 0
        from backend.services.notification_service import notification_service
        for row in rows:
            try:
                # Claim first so a create() failure doesn't cause an endless
                # retry; the dedupe_key is a second guard against duplicates.
                with db.connect() as conn:
                    conn.execute("UPDATE tasks SET due_notified_at = ? WHERE id = ?", (now, row["id"]))
                    conn.commit()
                notification_service.create(
                    company_id=row["company_id"],
                    notification_type="task_due",
                    title=f'Task due: "{row["title"]}"',
                    recipient_user_id=row["assigned_user_id"],
                    severity="warning",
                    data={"task_id": row["id"]},
                    dedupe_key=f"task_due:{row['id']}",
                )
                fired += 1
            except Exception:
                logger.exception("task_due notification failed for task #%s", row["id"])
        return fired


task_service = TaskService()
