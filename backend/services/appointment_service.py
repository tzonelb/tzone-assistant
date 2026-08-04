"""Company-scoped CRUD for the Appointments module: an optional booking
module connected to employees, their calendars, and customer profiles.
Mirrors the layered service pattern in task_service.py -- the
`appointments` table lives in database/database.py's central schema
init, so this module does not own/create its own tables and has no
ensure_schema() of its own to call at startup or in tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ALLOWED_STATUSES = {"scheduled", "completed", "cancelled", "no_show"}


class AppointmentConflictError(Exception):
    """Raised when an update's optimistic-concurrency token is stale, i.e.
    the appointment was changed by someone else since the client loaded
    it."""


class AppointmentOverlapError(Exception):
    """Raised when a new/updated appointment would overlap another
    active (scheduled) appointment already on the same assignee's
    calendar."""


class AppointmentValidationError(ValueError):
    """Raised for invalid field values: a bad status code, a missing
    title/start time, ends_at before starts_at, or an assignee/customer
    that does not belong to this company."""


_APPOINTMENT_SELECT = """
    SELECT
        a.*,
        assignee.full_name AS assignee_name,
        assignee.email AS assignee_email,
        customer.display_name AS customer_name
    FROM appointments a
    LEFT JOIN users assignee ON assignee.id = a.assignee_user_id
    LEFT JOIN customers customer ON customer.id = a.customer_id
"""


class AppointmentService:
    EDITABLE_FIELDS = {
        "title",
        "description",
        "customer_id",
        "assignee_user_id",
        "starts_at",
        "ends_at",
        "location",
        "status",
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
            if key in ("title", "description", "location", "starts_at", "ends_at"):
                cleaned[key] = self._clean_text(value)
            elif key == "status":
                status_value = self._clean_text(value)
                status_value = (status_value or "scheduled").lower()
                if status_value not in ALLOWED_STATUSES:
                    raise AppointmentValidationError(
                        f"status must be one of {sorted(ALLOWED_STATUSES)}"
                    )
                cleaned[key] = status_value
            else:
                # assignee_user_id / customer_id: ints or None.
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
            raise AppointmentValidationError(
                "Assignee must be an active member of this company."
            )

    def _validate_customer(
        self, conn, company_id: int, customer_id: int | None
    ) -> None:
        if customer_id is None:
            return
        row = conn.execute(
            "SELECT 1 FROM customers WHERE id = ? AND company_id = ? LIMIT 1",
            (customer_id, company_id),
        ).fetchone()
        if not row:
            raise AppointmentValidationError(
                "Customer must belong to this company."
            )

    def _check_overlap(
        self,
        conn,
        *,
        company_id: int,
        assignee_user_id: int | None,
        starts_at: str,
        ends_at: str | None,
        exclude_id: int | None,
    ) -> None:
        """Two scheduled appointments for the same assignee must not
        overlap in time. Appointments with no assignee, or that are not
        'scheduled' (completed/cancelled/no_show), never conflict."""
        if assignee_user_id is None:
            return

        effective_end = ends_at or starts_at

        params: list[Any] = [company_id, assignee_user_id, effective_end, starts_at]
        exclude_clause = ""
        if exclude_id is not None:
            exclude_clause = "AND id != ?"
            params.append(exclude_id)

        row = conn.execute(
            f"""
            SELECT id FROM appointments
            WHERE company_id = ?
              AND assignee_user_id = ?
              AND status = 'scheduled'
              AND starts_at < ?
              AND COALESCE(ends_at, starts_at) > ?
              {exclude_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row:
            raise AppointmentOverlapError(
                "This assignee already has another scheduled appointment "
                "that overlaps this time."
            )

    def list_appointments(
        self,
        *,
        company_id: int,
        status: str | None = None,
        assignee_user_id: int | None = None,
        starts_after: str | None = None,
        starts_before: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["a.company_id = ?"]
        params: list[Any] = [company_id]

        if status and status != "all":
            where.append("a.status = ?")
            params.append(status)

        if assignee_user_id is not None:
            where.append("a.assignee_user_id = ?")
            params.append(assignee_user_id)

        if starts_after:
            where.append("a.starts_at >= ?")
            params.append(starts_after)

        if starts_before:
            where.append("a.starts_at <= ?")
            params.append(starts_before)

        if search and search.strip():
            pattern = f"%{search.strip()}%"
            where.append("(a.title LIKE ? OR a.description LIKE ?)")
            params.extend([pattern, pattern])

        clause = " AND ".join(where)

        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM appointments a WHERE {clause}", params
            ).fetchone()["total"]

            rows = conn.execute(
                f"""
                {_APPOINTMENT_SELECT}
                WHERE {clause}
                ORDER BY a.starts_at ASC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(500, limit)), max(0, offset)],
            ).fetchall()

        return {"items": [dict(row) for row in rows], "total": int(total or 0)}

    def get_appointment(self, *, company_id: int, appointment_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                f"{_APPOINTMENT_SELECT} WHERE a.id = ? AND a.company_id = ? LIMIT 1",
                (appointment_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Appointment not found")
        return dict(row)

    def create_appointment(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        cleaned = self._clean_values(values)
        title = cleaned.get("title")
        if not title:
            raise AppointmentValidationError("title is required")

        starts_at = cleaned.get("starts_at")
        if not starts_at:
            raise AppointmentValidationError("starts_at is required")

        ends_at = cleaned.get("ends_at")
        if ends_at and ends_at < starts_at:
            raise AppointmentValidationError("ends_at cannot be before starts_at")

        status_value = cleaned.get("status") or "scheduled"
        now = utc_now_iso()

        with db.connect() as conn:
            self._validate_assignee(conn, company_id, cleaned.get("assignee_user_id"))
            self._validate_customer(conn, company_id, cleaned.get("customer_id"))

            if status_value == "scheduled":
                self._check_overlap(
                    conn,
                    company_id=company_id,
                    assignee_user_id=cleaned.get("assignee_user_id"),
                    starts_at=starts_at,
                    ends_at=ends_at,
                    exclude_id=None,
                )

            cursor = conn.execute(
                """
                INSERT INTO appointments (
                    company_id, title, description, customer_id,
                    assignee_user_id, starts_at, ends_at, location, status,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    title,
                    cleaned.get("description"),
                    cleaned.get("customer_id"),
                    cleaned.get("assignee_user_id"),
                    starts_at,
                    ends_at,
                    cleaned.get("location"),
                    status_value,
                    actor_user_id,
                    now,
                    now,
                ),
            )
            appointment_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_appointment(company_id=company_id, appointment_id=appointment_id)

    def update_appointment(
        self,
        *,
        company_id: int,
        appointment_id: int,
        values: dict[str, Any],
        expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        cleaned = self._clean_values(values)
        now = utc_now_iso()

        with db.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM appointments WHERE id = ? AND company_id = ?",
                (appointment_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Appointment not found")

            # Optimistic concurrency: if the caller told us which version
            # they were editing, refuse to overwrite a record that has
            # since moved on.
            if (
                expected_updated_at is not None
                and str(existing["updated_at"]) != str(expected_updated_at)
            ):
                raise AppointmentConflictError(
                    "This appointment was changed elsewhere. Reload to see "
                    "the latest details before editing."
                )

            if "title" in cleaned and not cleaned["title"]:
                raise AppointmentValidationError("title cannot be empty")
            if "starts_at" in cleaned and not cleaned["starts_at"]:
                raise AppointmentValidationError("starts_at cannot be empty")

            if not cleaned:
                return self.get_appointment(
                    company_id=company_id, appointment_id=appointment_id
                )

            if "assignee_user_id" in cleaned:
                self._validate_assignee(conn, company_id, cleaned["assignee_user_id"])
            if "customer_id" in cleaned:
                self._validate_customer(conn, company_id, cleaned["customer_id"])

            merged_assignee = cleaned.get("assignee_user_id", existing["assignee_user_id"])
            merged_starts_at = cleaned.get("starts_at", existing["starts_at"])
            merged_ends_at = cleaned.get("ends_at", existing["ends_at"])
            merged_status = cleaned.get("status", existing["status"])

            if merged_ends_at and merged_ends_at < merged_starts_at:
                raise AppointmentValidationError("ends_at cannot be before starts_at")

            if merged_status == "scheduled":
                self._check_overlap(
                    conn,
                    company_id=company_id,
                    assignee_user_id=merged_assignee,
                    starts_at=merged_starts_at,
                    ends_at=merged_ends_at,
                    exclude_id=appointment_id,
                )

            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE appointments SET {assignments}, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, appointment_id, company_id],
            )
            conn.commit()

        return self.get_appointment(company_id=company_id, appointment_id=appointment_id)

    def delete_appointment(self, *, company_id: int, appointment_id: int) -> bool:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM appointments WHERE id = ? AND company_id = ?",
                (appointment_id, company_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_assignable_users(self, *, company_id: int) -> list[dict[str, Any]]:
        """Active members of this company, for the assignee picker. Kept
        local to this module (rather than reusing tasks.py's assignable-
        users endpoint) so appointments.view is the only permission this
        needs -- it does not require tasks.view."""
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    users.id,
                    users.full_name,
                    users.email
                FROM company_users
                JOIN users ON users.id = company_users.user_id
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


appointment_service = AppointmentService()
