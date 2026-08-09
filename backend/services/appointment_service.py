from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from database.database import db

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_dt(value: Any) -> str | None:
    """Canonicalize to UTC ISO (+00:00 offset) so string comparisons in
    scan_upcoming_reminders and the double-booking overlap check are correct
    regardless of the client's timezone representation."""
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


def _fire_appointment_trigger(*, company_id: int, customer_id: int | None, trigger_type: str) -> None:
    """Lazy import breaks the natural import cycle the same way
    conversation_control_service.py's equivalent hook does — never raises,
    since a broken/misconfigured trigger flow must never block a real
    appointment being booked or completed."""
    try:
        from core.reply_flow_engine import reply_flow_engine
        reply_flow_engine.fire_event_for_customer(company_id=company_id, customer_id=customer_id, trigger_type=trigger_type)
    except Exception:
        logger.exception("%s reply flow trigger failed for appointment (customer #%s)", trigger_type, customer_id)


def _notify_appointment_created(
    *, company_id: int, appointment_id: int, title: str, scheduled_at: str, employee_user_id: int | None,
) -> None:
    """Bell notification when an appointment is booked. Targets the assigned
    employee if there is one, otherwise the whole team. Never raises."""
    try:
        from backend.services.notification_service import notification_service
        notification_service.create(
            company_id=company_id,
            notification_type="appointment_created",
            title=f'Appointment booked: "{title}"',
            body=f"Scheduled for {scheduled_at}",
            recipient_user_id=employee_user_id,
            severity="info",
            data={"appointment_id": appointment_id, "scheduled_at": scheduled_at},
            dedupe_key=f"appointment_created:{appointment_id}",
        )
    except Exception:
        logger.exception("appointment_created notification failed for appointment #%s", appointment_id)


STATUSES = ["scheduled", "completed", "cancelled", "no_show"]
DEFAULT_STATUS = "scheduled"
DEFAULT_DURATION_MINUTES = 30


class AppointmentService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS appointments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    customer_id INTEGER,
                    employee_user_id INTEGER,
                    scheduled_at TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL DEFAULT 30,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    notes TEXT,
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL,
                    FOREIGN KEY(employee_user_id) REFERENCES users(id) ON DELETE SET NULL,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(appointments)")}
            if "flow_reminder_sent_at" not in existing_columns:
                # Tracks whether the appointment_reminder Reply Flow trigger has
                # already fired for this appointment, so main.py's reminder
                # worker (via reply_flow_engine.check_appointment_reminders)
                # never sends the same reminder twice.
                conn.execute("ALTER TABLE appointments ADD COLUMN flow_reminder_sent_at TEXT")
            # Separate claim marker for the INTERNAL team bell reminder (this
            # service's scan_upcoming_reminders) so an imminent appointment is
            # alerted once, not re-scanned every 30s until it starts.
            if "reminder_notified_at" not in existing_columns:
                conn.execute("ALTER TABLE appointments ADD COLUMN reminder_notified_at TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_appointments_reminder_scan "
                "ON appointments(status, scheduled_at)"
            )
            conn.commit()

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _validate_customer(self, conn: Any, *, company_id: int, customer_id: int) -> None:
        exists = conn.execute(
            "SELECT id FROM customers WHERE id = ? AND company_id = ?",
            (customer_id, company_id),
        ).fetchone()
        if not exists:
            raise KeyError("Customer not found")

    def _validate_employee(self, conn: Any, *, company_id: int, employee_user_id: int) -> None:
        is_company_employee = conn.execute(
            "SELECT 1 FROM company_users WHERE company_id = ? AND user_id = ? AND status = 'active'",
            (company_id, employee_user_id),
        ).fetchone()
        if not is_company_employee:
            raise ValueError("Assigned employee must be an active employee of this company.")

    def create_appointment(
        self,
        *,
        company_id: int,
        title: str,
        scheduled_at: str,
        customer_id: int | None = None,
        employee_user_id: int | None = None,
        duration_minutes: int = DEFAULT_DURATION_MINUTES,
        status: str = DEFAULT_STATUS,
        notes: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        clean_title = self._clean(title)
        if not clean_title:
            raise ValueError("Appointment title is required.")

        clean_scheduled_at = self._clean(scheduled_at)
        if not clean_scheduled_at:
            raise ValueError("Scheduled date/time is required.")
        try:
            parsed = clean_scheduled_at.replace("Z", "+00:00") if clean_scheduled_at.endswith("Z") else clean_scheduled_at
            datetime.fromisoformat(parsed)
        except ValueError as exc:
            raise ValueError("Scheduled date/time must be a valid ISO date/time.") from exc
        # Store a canonical UTC value so overlap checks and reminder scans
        # compare like-for-like regardless of the client's tz representation.
        clean_scheduled_at = normalize_dt(clean_scheduled_at)

        if duration_minutes <= 0:
            raise ValueError("Duration must be a positive number of minutes.")

        clean_status = str(status or DEFAULT_STATUS).strip().lower()
        if clean_status not in STATUSES:
            raise ValueError(f'"{clean_status}" is not a valid status. Choose one of: {", ".join(STATUSES)}.')

        notes = self._clean(notes)
        now = utc_now_iso()

        with db.connect() as conn:
            if customer_id is not None:
                self._validate_customer(conn, company_id=company_id, customer_id=customer_id)
            if employee_user_id is not None:
                self._validate_employee(conn, company_id=company_id, employee_user_id=employee_user_id)

            cursor = conn.execute(
                """
                INSERT INTO appointments (
                    company_id, title, customer_id, employee_user_id, scheduled_at,
                    duration_minutes, status, notes, created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id, clean_title, customer_id, employee_user_id, clean_scheduled_at,
                    duration_minutes, clean_status, notes, actor_user_id, now, now,
                ),
            )
            appointment_id = int(cursor.lastrowid)
            conn.commit()

        _fire_appointment_trigger(company_id=company_id, customer_id=customer_id, trigger_type="appointment_created")
        _notify_appointment_created(
            company_id=company_id, appointment_id=appointment_id, title=clean_title,
            scheduled_at=clean_scheduled_at, employee_user_id=employee_user_id,
        )

        return self.get_appointment(company_id=company_id, appointment_id=appointment_id)

    @staticmethod
    def _enriched_select(where_clause: str) -> str:
        return f"""
            SELECT a.*,
                   (SELECT COALESCE(u.full_name, u.email) FROM users u WHERE u.id = a.employee_user_id) AS employee_name,
                   (SELECT COALESCE(c.display_name, c.internal_name) FROM customers c WHERE c.id = a.customer_id) AS customer_name,
                   (SELECT c.phone FROM customers c WHERE c.id = a.customer_id) AS customer_phone
            FROM appointments a
            WHERE {where_clause}
        """

    def scan_upcoming_reminders(self, *, minutes_before: int = 30) -> int:
        """Reminder-worker cadence: raise a one-time bell alert for every
        scheduled appointment now within `minutes_before` of its start. The
        dedupe_key (appointment_reminder:<id>) fires it exactly once. This is
        the INTERNAL team alert — separate from the optional customer-facing
        appointment_reminder Reply Flow. Never raises; returns count fired."""
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        window_end = (now + timedelta(minutes=minutes_before)).isoformat()
        now_iso = now.isoformat()
        try:
            with db.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, company_id, title, scheduled_at, employee_user_id FROM appointments
                    WHERE status = 'scheduled' AND scheduled_at > ? AND scheduled_at <= ?
                      AND reminder_notified_at IS NULL
                    """,
                    (now_iso, window_end),
                ).fetchall()
        except Exception:
            logger.exception("scan_upcoming_reminders query failed")
            return 0

        fired = 0
        from backend.services.notification_service import notification_service
        for row in rows:
            try:
                # Claim first (fires once); dedupe_key is the second guard.
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE appointments SET reminder_notified_at = ? WHERE id = ?",
                        (now.isoformat(), row["id"]),
                    )
                    conn.commit()
                notification_service.create(
                    company_id=row["company_id"],
                    notification_type="appointment_reminder",
                    title=f'Upcoming appointment: "{row["title"]}"',
                    body=f"Starts at {row['scheduled_at']}",
                    recipient_user_id=row["employee_user_id"],
                    severity="warning",
                    data={"appointment_id": row["id"], "scheduled_at": row["scheduled_at"]},
                    dedupe_key=f"appointment_reminder:{row['id']}",
                )
                fired += 1
            except Exception:
                logger.exception("appointment_reminder notification failed for appointment #%s", row["id"])
        return fired

    def get_appointment(self, *, company_id: int, appointment_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                self._enriched_select("a.id = ? AND a.company_id = ?") + " LIMIT 1",
                (appointment_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Appointment not found")
        return dict(row)

    def list_appointments(
        self,
        *,
        company_id: int,
        status: str | None = None,
        employee_user_id: int | None = None,
        customer_id: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        where = ["a.company_id = ?"]
        params: list[Any] = [company_id]

        if status is not None:
            where.append("a.status = ?")
            params.append(str(status).strip().lower())
        if employee_user_id is not None:
            where.append("a.employee_user_id = ?")
            params.append(employee_user_id)
        if customer_id is not None:
            where.append("a.customer_id = ?")
            params.append(customer_id)
        if from_date:
            where.append("a.scheduled_at >= ?")
            params.append(from_date)
        if to_date:
            where.append("a.scheduled_at <= ?")
            params.append(to_date)

        clause = " AND ".join(where)
        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM appointments a WHERE {clause}", params
            ).fetchone()["total"]
            rows = conn.execute(
                self._enriched_select(clause) + " ORDER BY a.scheduled_at ASC",
                params,
            ).fetchall()

        items = [dict(row) for row in rows]
        return {"items": items, "total": int(total or 0)}

    def update_appointment(
        self,
        *,
        company_id: int,
        appointment_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}

        if "title" in values:
            clean_title = self._clean(values["title"])
            if not clean_title:
                raise ValueError("Appointment title is required.")
            cleaned["title"] = clean_title

        if "notes" in values:
            cleaned["notes"] = self._clean(values["notes"])

        if "scheduled_at" in values and values["scheduled_at"] is not None:
            clean_scheduled_at = self._clean(values["scheduled_at"])
            if not clean_scheduled_at:
                raise ValueError("Scheduled date/time is required.")
            try:
                parsed = clean_scheduled_at.replace("Z", "+00:00") if clean_scheduled_at.endswith("Z") else clean_scheduled_at
                datetime.fromisoformat(parsed)
            except ValueError as exc:
                raise ValueError("Scheduled date/time must be a valid ISO date/time.") from exc
            cleaned["scheduled_at"] = normalize_dt(clean_scheduled_at)
            # A reschedule re-arms the internal reminder bell.
            cleaned["reminder_notified_at"] = None

        if "duration_minutes" in values and values["duration_minutes"] is not None:
            duration_minutes = int(values["duration_minutes"])
            if duration_minutes <= 0:
                raise ValueError("Duration must be a positive number of minutes.")
            cleaned["duration_minutes"] = duration_minutes

        if "status" in values and values["status"] is not None:
            new_status = str(values["status"]).strip().lower()
            if new_status not in STATUSES:
                raise ValueError(f'"{new_status}" is not a valid status. Choose one of: {", ".join(STATUSES)}.')
            cleaned["status"] = new_status

        employee_requested = "employee_user_id" in values
        employee_user_id = values.get("employee_user_id")

        customer_requested = "customer_id" in values
        customer_id = values.get("customer_id")

        if not cleaned and not employee_requested and not customer_requested:
            return self.get_appointment(company_id=company_id, appointment_id=appointment_id)

        now = utc_now_iso()
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id, status, customer_id FROM appointments WHERE id = ? AND company_id = ?",
                (appointment_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Appointment not found")
            previous_status = existing["status"]
            existing_customer_id = existing["customer_id"]

            if employee_requested and employee_user_id is not None:
                self._validate_employee(conn, company_id=company_id, employee_user_id=employee_user_id)
            if employee_requested:
                cleaned["employee_user_id"] = employee_user_id

            if customer_requested and customer_id is not None:
                self._validate_customer(conn, company_id=company_id, customer_id=customer_id)
            if customer_requested:
                cleaned["customer_id"] = customer_id

            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE appointments SET {assignments}, updated_at = ? WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, appointment_id, company_id],
            )
            conn.commit()

        if cleaned.get("status") == "completed" and previous_status != "completed":
            final_customer_id = cleaned["customer_id"] if customer_requested else existing_customer_id
            _fire_appointment_trigger(company_id=company_id, customer_id=final_customer_id, trigger_type="appointment_completed")

        return self.get_appointment(company_id=company_id, appointment_id=appointment_id)

    def delete_appointment(self, *, company_id: int, appointment_id: int) -> None:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM appointments WHERE id = ? AND company_id = ?",
                (appointment_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Appointment not found")


appointment_service = AppointmentService()
