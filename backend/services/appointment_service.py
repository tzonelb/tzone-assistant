"""Appointments and the availability rules that generate their slots.

Everything here lives in the owning company's encrypted database, reached
through `database_manager.tenant(company_id)`. Table creation belongs to
`database/schema_tenant.py` alone; this service only reads and writes.

Two design decisions are load-bearing and are stated once here rather than
repeated at every call site:

*Times are stored as UTC instants in one fixed-width format.*
`starts_at` and `ends_at` are TEXT columns, and every comparison this module
makes on them — overlap detection, range filtering, ordering — is a string
comparison performed by SQLite. String comparison only equals chronological
comparison when every stored value has the same width, the same offset and the
same field order, so every value written here goes through `normalize_instant`
first: parsed, converted to UTC, and rendered as `YYYY-MM-DDTHH:MM:SS+00:00`.
A row written in any other shape would silently sort wrong and could be booked
over. Availability rules, being wall-clock rules rather than instants, are
likewise interpreted in UTC.

*Only a cancelled appointment releases its slot.*
Everything else — scheduled, confirmed, completed, no_show — still holds the
staff member's time. A no-show consumed the slot as surely as an attended
visit did; treating it as free would let the calendar be rewritten after the
fact.
"""

from __future__ import annotations

import logging
from datetime import date as date_type, datetime, time as time_type, timedelta, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)

INSTANT_FORMAT = "%Y-%m-%dT%H:%M:%S+00:00"

# Every status an appointment may carry.
ALLOWED_STATUS = ("scheduled", "confirmed", "completed", "no_show", "cancelled")

# The statuses that still occupy the staff member's calendar. `cancelled` is
# deliberately the only one missing.
SLOT_HOLDING_STATUS = ("scheduled", "confirmed", "completed", "no_show")

# Statuses an appointment can be moved to by hand from the calendar screen.
SETTABLE_STATUS = ("scheduled", "confirmed", "completed", "no_show")

RULE_STATUS = ("active", "inactive")

MIN_SLOT_MINUTES = 5
MAX_SLOT_MINUTES = 8 * 60
MAX_APPOINTMENT_MINUTES = 24 * 60

WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


class AppointmentError(Exception):
    """Base class so a route can catch the whole module in one clause."""


class AppointmentNotFound(AppointmentError):
    """The appointment does not exist inside this company's database."""


class SlotConflict(AppointmentError):
    """The requested time overlaps an appointment the same staff member holds.

    Carries the offending appointment so the screen can name it instead of
    telling the user only that something, somewhere, clashed.
    """

    def __init__(self, message: str, conflict: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.conflict = conflict or {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_instant(value: Any, *, field: str = "time") -> str:
    """Return `value` as a UTC instant in the one comparable stored format.

    A naive input is read as UTC rather than rejected, because browsers and
    integrations both send naive local strings; an offset-aware input is
    converted. Either way the stored text is directly comparable to every
    other stored text, which is what the overlap SQL depends on.
    """
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()

        if not text:
            raise ValueError(f"A {field} is required.")

        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"

        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                f"The {field} '{value}' is not a valid ISO-8601 date and time."
            ) from exc

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    return moment.astimezone(timezone.utc).strftime(INSTANT_FORMAT)


def parse_instant(value: Any) -> datetime:
    return datetime.strptime(normalize_instant(value), INSTANT_FORMAT).replace(
        tzinfo=timezone.utc
    )


def normalize_date(value: Any, *, field: str = "date") -> date_type:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_type):
        return value

    text = str(value or "").strip()

    if not text:
        raise ValueError(f"A {field} is required.")

    try:
        return date_type.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"The {field} '{value}' is not a valid date (YYYY-MM-DD).") from exc


def normalize_clock(value: Any, *, field: str = "time") -> str:
    """Return a wall-clock time as zero-padded `HH:MM`."""
    text = str(value or "").strip()

    if not text:
        raise ValueError(f"A {field} is required.")

    parts = text.split(":")

    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError) as exc:
        raise ValueError(f"The {field} '{value}' is not a valid time (HH:MM).") from exc

    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"The {field} '{value}' is not a valid time (HH:MM).")

    return f"{hour:02d}:{minute:02d}"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _placeholders(values) -> str:
    return ",".join("?" for _ in values)


class AppointmentService:
    ALLOWED_STATUS = ALLOWED_STATUS
    SLOT_HOLDING_STATUS = SLOT_HOLDING_STATUS
    SETTABLE_STATUS = SETTABLE_STATUS

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_window(self, starts_at: Any, ends_at: Any) -> tuple[str, str]:
        starts = normalize_instant(starts_at, field="start time")
        ends = normalize_instant(ends_at, field="end time")

        if ends <= starts:
            raise ValueError("An appointment must end after it starts.")

        length = parse_instant(ends) - parse_instant(starts)

        if length > timedelta(minutes=MAX_APPOINTMENT_MINUTES):
            raise ValueError("An appointment cannot be longer than 24 hours.")

        return starts, ends

    @staticmethod
    def _require_staff(staff_user_id: Any) -> int:
        """Every appointment names the staff member whose time it takes.

        The column is nullable, but an appointment with nobody attached has no
        calendar to collide with, which would make it a permanent hole in the
        double-booking guarantee. Booking without staff is refused instead.
        """
        if staff_user_id is None or str(staff_user_id).strip() == "":
            raise ValueError("An appointment must be assigned to a staff member.")

        return int(staff_user_id)

    # ------------------------------------------------------------------
    # Overlap
    # ------------------------------------------------------------------

    @staticmethod
    def _overlap_sql(*, exclude_id: bool) -> str:
        """The one definition of "this slot is taken".

        Half-open intervals: `starts_at < other_ends AND ends_at > other_starts`.
        Because both comparisons are strict, an appointment that ends at exactly
        the moment the next one starts does NOT overlap, so back-to-back
        bookings stay legal. Using `<=`/`>=` here would refuse every consecutive
        booking in the day and quietly halve the calendar.
        """
        clause = f"""
            FROM appointments
            WHERE company_id = ?
              AND staff_user_id = ?
              AND status IN ({_placeholders(SLOT_HOLDING_STATUS)})
              AND starts_at < ?
              AND ends_at > ?
        """

        if exclude_id:
            clause += " AND id != ?"

        return clause

    @staticmethod
    def _overlap_params(
        *,
        company_id: int,
        staff_user_id: int,
        starts_at: str,
        ends_at: str,
        exclude_id: int | None = None,
    ) -> list[Any]:
        params: list[Any] = [
            company_id,
            staff_user_id,
            *SLOT_HOLDING_STATUS,
            ends_at,
            starts_at,
        ]

        if exclude_id is not None:
            params.append(exclude_id)

        return params

    def _find_conflict(
        self,
        conn,
        *,
        company_id: int,
        staff_user_id: int,
        starts_at: str,
        ends_at: str,
        exclude_id: int | None = None,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT *" + self._overlap_sql(exclude_id=exclude_id is not None)
            + " ORDER BY starts_at ASC LIMIT 1",
            self._overlap_params(
                company_id=company_id,
                staff_user_id=staff_user_id,
                starts_at=starts_at,
                ends_at=ends_at,
                exclude_id=exclude_id,
            ),
        ).fetchone()

        return dict(row) if row else None

    @staticmethod
    def _conflict_error(conflict: dict[str, Any]) -> SlotConflict:
        return SlotConflict(
            "That time is already booked for this staff member "
            f"({conflict.get('starts_at')} → {conflict.get('ends_at')}).",
            conflict,
        )

    # ------------------------------------------------------------------
    # Appointments
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        company_id: int,
        staff_user_id: int,
        starts_at: Any,
        ends_at: Any,
        title: str,
        customer_id: int | None = None,
        conversation_id: int | None = None,
        branch_id: int | None = None,
        notes: str | None = None,
        status: str = "scheduled",
        created_by_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Book a slot, or refuse because someone else already holds it.

        Double booking is prevented in two layers that must both hold:

        1. `BEGIN IMMEDIATE` takes the database's write lock before the first
           read. A second booking that arrives mid-flight blocks here until
           this transaction commits, so it can never read a calendar that is
           about to change underneath it. This is the same guard the
           conversation takeover code uses.
        2. The insert itself is conditional — `INSERT ... SELECT ... WHERE NOT
           EXISTS (overlapping row)` — so the check and the write are a single
           statement that SQLite evaluates atomically, and `cursor.rowcount`
           reports whether the row was actually written. A plain
           check-then-insert has a window between the two; this has none, even
           if a future caller forgets the lock.

        The preceding SELECT exists only to name the conflicting appointment in
        the error message; the guarantee does not rest on it.
        """
        company_id = int(company_id)
        staff_user_id = self._require_staff(staff_user_id)
        starts, ends = self._validate_window(starts_at, ends_at)

        title = _clean(title) or "Appointment"
        notes = _clean(notes)

        if status not in ALLOWED_STATUS:
            raise ValueError(f"Status must be one of: {', '.join(ALLOWED_STATUS)}.")

        if status == "cancelled":
            raise ValueError("An appointment cannot be booked as cancelled.")

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                conflict = self._find_conflict(
                    conn,
                    company_id=company_id,
                    staff_user_id=staff_user_id,
                    starts_at=starts,
                    ends_at=ends,
                )

                if conflict:
                    conn.rollback()
                    raise self._conflict_error(conflict)

                cursor = conn.execute(
                    f"""
                    INSERT INTO appointments (
                        company_id, customer_id, conversation_id, staff_user_id,
                        branch_id, title, notes, starts_at, ends_at, status,
                        created_by_user_id, created_at, updated_at
                    )
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM appointments
                        WHERE company_id = ?
                          AND staff_user_id = ?
                          AND status IN ({_placeholders(SLOT_HOLDING_STATUS)})
                          AND starts_at < ?
                          AND ends_at > ?
                    )
                    """,
                    (
                        company_id,
                        int(customer_id) if customer_id else None,
                        int(conversation_id) if conversation_id else None,
                        staff_user_id,
                        int(branch_id) if branch_id else None,
                        title,
                        notes,
                        starts,
                        ends,
                        status,
                        int(created_by_user_id) if created_by_user_id else None,
                        now,
                        now,
                        company_id,
                        staff_user_id,
                        *SLOT_HOLDING_STATUS,
                        ends,
                        starts,
                    ),
                )

                if cursor.rowcount != 1:
                    # The guard fired: another transaction committed an
                    # overlapping booking. Nothing was written.
                    conn.rollback()
                    raise SlotConflict(
                        "That time was booked by someone else a moment ago. "
                        "Pick another slot."
                    )

                appointment_id = int(cursor.lastrowid)
                conn.commit()
            except AppointmentError:
                raise
            except Exception:
                conn.rollback()
                raise

        created = self.get(company_id=company_id, appointment_id=appointment_id)

        if created is None:  # pragma: no cover - only reachable on a lost write
            raise AppointmentError("The appointment could not be stored.")

        return created

    def reschedule(
        self,
        *,
        company_id: int,
        appointment_id: int,
        starts_at: Any,
        ends_at: Any,
        staff_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Move an appointment, refusing any move onto an occupied slot.

        Rescheduling is the same race as booking — the new window has to be
        checked and taken indivisibly — so it uses the same two layers, with
        the appointment's own row excluded from the overlap test. Without that
        exclusion an appointment could never be moved by a few minutes,
        because it would collide with itself.
        """
        company_id = int(company_id)
        appointment_id = int(appointment_id)
        starts, ends = self._validate_window(starts_at, ends_at)

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                current = conn.execute(
                    "SELECT * FROM appointments WHERE id = ? AND company_id = ? LIMIT 1",
                    (appointment_id, company_id),
                ).fetchone()

                if current is None:
                    conn.rollback()
                    raise AppointmentNotFound("Appointment not found.")

                current = dict(current)

                if current["status"] == "cancelled":
                    conn.rollback()
                    raise ValueError(
                        "A cancelled appointment cannot be rescheduled. Book a new one."
                    )

                target_staff = self._require_staff(
                    staff_user_id
                    if staff_user_id is not None
                    else current["staff_user_id"]
                )

                conflict = self._find_conflict(
                    conn,
                    company_id=company_id,
                    staff_user_id=target_staff,
                    starts_at=starts,
                    ends_at=ends,
                    exclude_id=appointment_id,
                )

                if conflict:
                    conn.rollback()
                    raise self._conflict_error(conflict)

                cursor = conn.execute(
                    f"""
                    UPDATE appointments
                    SET starts_at = ?, ends_at = ?, staff_user_id = ?, updated_at = ?
                    WHERE id = ?
                      AND company_id = ?
                      AND status != 'cancelled'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM appointments AS other
                          WHERE other.company_id = ?
                            AND other.staff_user_id = ?
                            AND other.id != ?
                            AND other.status IN ({_placeholders(SLOT_HOLDING_STATUS)})
                            AND other.starts_at < ?
                            AND other.ends_at > ?
                      )
                    """,
                    (
                        starts,
                        ends,
                        target_staff,
                        now,
                        appointment_id,
                        company_id,
                        company_id,
                        target_staff,
                        appointment_id,
                        *SLOT_HOLDING_STATUS,
                        ends,
                        starts,
                    ),
                )

                if cursor.rowcount != 1:
                    conn.rollback()
                    raise SlotConflict(
                        "That time was booked by someone else a moment ago. "
                        "Pick another slot."
                    )

                conn.commit()
            except AppointmentError:
                raise
            except Exception:
                conn.rollback()
                raise

        return self.get(company_id=company_id, appointment_id=appointment_id)

    def cancel(
        self,
        *,
        company_id: int,
        appointment_id: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an appointment, which releases the slot for rebooking."""
        company_id = int(company_id)
        appointment_id = int(appointment_id)

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                cursor = conn.execute(
                    """
                    UPDATE appointments
                    SET status = 'cancelled',
                        cancelled_reason = ?,
                        updated_at = ?
                    WHERE id = ? AND company_id = ? AND status != 'cancelled'
                    """,
                    (_clean(reason), utc_now_iso(), appointment_id, company_id),
                )

                if cursor.rowcount != 1:
                    # Either it never existed in this company's database, or it
                    # was already cancelled. Tell those apart before deciding.
                    existing = conn.execute(
                        "SELECT status FROM appointments WHERE id = ? AND company_id = ?",
                        (appointment_id, company_id),
                    ).fetchone()

                    conn.rollback()

                    if existing is None:
                        raise AppointmentNotFound("Appointment not found.")
                else:
                    conn.commit()
            except AppointmentError:
                raise
            except Exception:
                conn.rollback()
                raise

        return self.get(company_id=company_id, appointment_id=appointment_id)

    def set_status(
        self,
        *,
        company_id: int,
        appointment_id: int,
        status: str,
    ) -> dict[str, Any]:
        """Move an appointment between the non-cancelled statuses.

        Cancelling goes through `cancel` so a reason can be recorded and the
        release of the slot stays a single, obvious code path.
        """
        if status not in SETTABLE_STATUS:
            raise ValueError(f"Status must be one of: {', '.join(SETTABLE_STATUS)}.")

        company_id = int(company_id)
        appointment_id = int(appointment_id)

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                current = conn.execute(
                    "SELECT * FROM appointments WHERE id = ? AND company_id = ? LIMIT 1",
                    (appointment_id, company_id),
                ).fetchone()

                if current is None:
                    conn.rollback()
                    raise AppointmentNotFound("Appointment not found.")

                current = dict(current)

                if current["status"] == "cancelled":
                    # Re-activating would silently retake a slot somebody else
                    # may have booked in the meantime.
                    conn.rollback()
                    raise ValueError(
                        "A cancelled appointment cannot be reactivated. Book a new one."
                    )

                conn.execute(
                    """
                    UPDATE appointments
                    SET status = ?, updated_at = ?
                    WHERE id = ? AND company_id = ? AND status != 'cancelled'
                    """,
                    (status, utc_now_iso(), appointment_id, company_id),
                )
                conn.commit()
            except AppointmentError:
                raise
            except Exception:
                conn.rollback()
                raise

        return self.get(company_id=company_id, appointment_id=appointment_id)

    def get(self, *, company_id: int, appointment_id: int) -> dict[str, Any] | None:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                """
                SELECT appointments.*,
                       customers.display_name AS customer_name,
                       customers.phone AS customer_phone
                FROM appointments
                LEFT JOIN customers ON customers.id = appointments.customer_id
                WHERE appointments.id = ? AND appointments.company_id = ?
                LIMIT 1
                """,
                (int(appointment_id), int(company_id)),
            ).fetchone()

        return dict(row) if row else None

    def list(
        self,
        *,
        company_id: int,
        start_date: Any = None,
        end_date: Any = None,
        staff_user_id: int | None = None,
        customer_id: int | None = None,
        status: str | None = None,
        include_cancelled: bool = True,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List appointments, by default for a date range and staff member.

        `start_date` and `end_date` are inclusive calendar days, expanded here
        into the half-open instant range [start 00:00, end+1 day 00:00) so that
        an appointment late on the last day is not dropped by a naive
        `<= end_date` comparison.
        """
        company_id = int(company_id)
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))

        # Column names are table-qualified so the identical clause can be reused
        # by the counting query and by the joined listing query.
        where = ["appointments.company_id = ?"]
        params: list[Any] = [company_id]

        if start_date:
            where.append("appointments.ends_at > ?")
            params.append(
                normalize_instant(
                    datetime.combine(
                        normalize_date(start_date, field="start date"),
                        time_type(0, 0),
                        tzinfo=timezone.utc,
                    )
                )
            )

        if end_date:
            where.append("appointments.starts_at < ?")
            params.append(
                normalize_instant(
                    datetime.combine(
                        normalize_date(end_date, field="end date") + timedelta(days=1),
                        time_type(0, 0),
                        tzinfo=timezone.utc,
                    )
                )
            )

        if staff_user_id is not None:
            where.append("appointments.staff_user_id = ?")
            params.append(int(staff_user_id))

        if customer_id is not None:
            where.append("appointments.customer_id = ?")
            params.append(int(customer_id))

        if status:
            if status not in ALLOWED_STATUS:
                raise ValueError(f"Status must be one of: {', '.join(ALLOWED_STATUS)}.")
            where.append("appointments.status = ?")
            params.append(status)
        elif not include_cancelled:
            where.append(
                f"appointments.status IN ({_placeholders(SLOT_HOLDING_STATUS)})"
            )
            params.extend(SLOT_HOLDING_STATUS)

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM appointments WHERE {clause}",
                    params,
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT appointments.*,
                       customers.display_name AS customer_name,
                       customers.phone AS customer_phone
                FROM appointments
                LEFT JOIN customers ON customers.id = appointments.customer_id
                WHERE {clause}
                ORDER BY appointments.starts_at ASC, appointments.id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        items = [dict(row) for row in rows]

        return {
            "items": items,
            "total": total,
            "status_counts": self.count_by_status(company_id),
        }

    def count_by_status(self, company_id: int) -> dict[str, int]:
        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM appointments
                WHERE company_id = ?
                GROUP BY status
                """,
                (int(company_id),),
            ).fetchall()

        counts = {status: 0 for status in ALLOWED_STATUS}
        counts.update({str(row["status"]): int(row["total"]) for row in rows})
        return counts

    def customer_options(self, company_id: int, limit: int = 500) -> list[dict[str, Any]]:
        """Customers this company can book, for the booking form's picker."""
        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT id, display_name, internal_name, phone
                FROM customers
                WHERE company_id = ?
                ORDER BY COALESCE(display_name, internal_name, ''), id
                LIMIT ?
                """,
                (int(company_id), max(1, min(int(limit), 1000))),
            ).fetchall()

        return [
            {
                "id": int(row["id"]),
                "label": (
                    row["display_name"]
                    or row["internal_name"]
                    or f"Customer #{row['id']}"
                ),
                "phone": row["phone"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Availability rules
    # ------------------------------------------------------------------

    def _validate_rule(
        self,
        *,
        weekday: Any,
        start_time: Any,
        end_time: Any,
        slot_minutes: Any,
        status: Any,
    ) -> tuple[int, str, str, int, str]:
        try:
            weekday = int(weekday)
        except (TypeError, ValueError) as exc:
            raise ValueError("Weekday must be a number from 0 (Monday) to 6 (Sunday).") from exc

        if not 0 <= weekday <= 6:
            raise ValueError("Weekday must be a number from 0 (Monday) to 6 (Sunday).")

        start_clock = normalize_clock(start_time, field="start time")
        end_clock = normalize_clock(end_time, field="end time")

        if end_clock <= start_clock:
            raise ValueError("A working window must end after it starts.")

        try:
            slot_minutes = int(slot_minutes)
        except (TypeError, ValueError) as exc:
            raise ValueError("Slot length must be a whole number of minutes.") from exc

        if not MIN_SLOT_MINUTES <= slot_minutes <= MAX_SLOT_MINUTES:
            raise ValueError(
                f"Slot length must be between {MIN_SLOT_MINUTES} and "
                f"{MAX_SLOT_MINUTES} minutes."
            )

        status = str(status or "active")

        if status not in RULE_STATUS:
            raise ValueError(f"Rule status must be one of: {', '.join(RULE_STATUS)}.")

        return weekday, start_clock, end_clock, slot_minutes, status

    def create_rule(
        self,
        *,
        company_id: int,
        staff_user_id: int,
        weekday: int,
        start_time: str,
        end_time: str,
        slot_minutes: int = 30,
        status: str = "active",
    ) -> dict[str, Any]:
        company_id = int(company_id)
        staff_user_id = self._require_staff(staff_user_id)
        weekday, start_clock, end_clock, slot_minutes, status = self._validate_rule(
            weekday=weekday,
            start_time=start_time,
            end_time=end_time,
            slot_minutes=slot_minutes,
            status=status,
        )
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                INSERT INTO availability_rules (
                    company_id, staff_user_id, weekday, start_time, end_time,
                    slot_minutes, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    staff_user_id,
                    weekday,
                    start_clock,
                    end_clock,
                    slot_minutes,
                    status,
                    now,
                    now,
                ),
            )
            conn.commit()
            rule_id = int(cursor.lastrowid)

        return self.get_rule(company_id=company_id, rule_id=rule_id)

    def update_rule(
        self,
        *,
        company_id: int,
        rule_id: int,
        weekday: Any = None,
        start_time: Any = None,
        end_time: Any = None,
        slot_minutes: Any = None,
        status: Any = None,
        staff_user_id: Any = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        rule_id = int(rule_id)

        current = self.get_rule(company_id=company_id, rule_id=rule_id)

        if current is None:
            raise AppointmentNotFound("Availability rule not found.")

        target_staff = self._require_staff(
            staff_user_id if staff_user_id is not None else current["staff_user_id"]
        )
        weekday, start_clock, end_clock, slot_minutes, status = self._validate_rule(
            weekday=current["weekday"] if weekday is None else weekday,
            start_time=current["start_time"] if start_time is None else start_time,
            end_time=current["end_time"] if end_time is None else end_time,
            slot_minutes=(
                current["slot_minutes"] if slot_minutes is None else slot_minutes
            ),
            status=current["status"] if status is None else status,
        )

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                UPDATE availability_rules
                SET staff_user_id = ?, weekday = ?, start_time = ?, end_time = ?,
                    slot_minutes = ?, status = ?, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (
                    target_staff,
                    weekday,
                    start_clock,
                    end_clock,
                    slot_minutes,
                    status,
                    utc_now_iso(),
                    rule_id,
                    company_id,
                ),
            )
            conn.commit()

            if cursor.rowcount != 1:
                raise AppointmentNotFound("Availability rule not found.")

        return self.get_rule(company_id=company_id, rule_id=rule_id)

    def delete_rule(self, *, company_id: int, rule_id: int) -> bool:
        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                "DELETE FROM availability_rules WHERE id = ? AND company_id = ?",
                (int(rule_id), int(company_id)),
            )
            conn.commit()

            if cursor.rowcount != 1:
                raise AppointmentNotFound("Availability rule not found.")

        return True

    def get_rule(self, *, company_id: int, rule_id: int) -> dict[str, Any] | None:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                "SELECT * FROM availability_rules WHERE id = ? AND company_id = ? LIMIT 1",
                (int(rule_id), int(company_id)),
            ).fetchone()

        return dict(row) if row else None

    def list_rules(
        self,
        *,
        company_id: int,
        staff_user_id: int | None = None,
        weekday: int | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        company_id = int(company_id)

        where = ["company_id = ?"]
        params: list[Any] = [company_id]

        if staff_user_id is not None:
            where.append("staff_user_id = ?")
            params.append(int(staff_user_id))

        if weekday is not None:
            where.append("weekday = ?")
            params.append(int(weekday))

        if active_only:
            where.append("status = 'active'")

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM availability_rules
                WHERE {clause}
                ORDER BY weekday ASC, start_time ASC, id ASC
                """,
                params,
            ).fetchall()

        return [
            {**dict(row), "weekday_name": WEEKDAY_NAMES[int(row["weekday"]) % 7]}
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Free slots
    # ------------------------------------------------------------------

    def available_slots(
        self,
        company_id: int,
        staff_user_id: int,
        date: Any,
        *,
        duration_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Free slots for one staff member on one day.

        The rules say when the staff member works; the appointments say which
        of that time is gone. A candidate slot survives only if it overlaps no
        slot-holding appointment, using the identical half-open comparison the
        booking guard uses — if the two ever disagreed, the screen would offer
        slots the API then refuses, or hide slots it would happily accept.
        """
        company_id = int(company_id)
        staff_user_id = self._require_staff(staff_user_id)
        day = normalize_date(date)
        weekday = day.weekday()

        rules = self.list_rules(
            company_id=company_id,
            staff_user_id=staff_user_id,
            weekday=weekday,
            active_only=True,
        )

        day_start = datetime.combine(day, time_type(0, 0), tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        with database_manager.tenant(company_id) as conn:
            busy_rows = conn.execute(
                f"""
                SELECT starts_at, ends_at
                FROM appointments
                WHERE company_id = ?
                  AND staff_user_id = ?
                  AND status IN ({_placeholders(SLOT_HOLDING_STATUS)})
                  AND starts_at < ?
                  AND ends_at > ?
                ORDER BY starts_at ASC
                """,
                (
                    company_id,
                    staff_user_id,
                    *SLOT_HOLDING_STATUS,
                    normalize_instant(day_end),
                    normalize_instant(day_start),
                ),
            ).fetchall()

        busy = [
            (str(row["starts_at"]), str(row["ends_at"])) for row in busy_rows
        ]

        slots: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for rule in rules:
            rule_start_hour, rule_start_minute = (
                int(part) for part in str(rule["start_time"]).split(":")[:2]
            )
            rule_end_hour, rule_end_minute = (
                int(part) for part in str(rule["end_time"]).split(":")[:2]
            )

            window_start = day_start + timedelta(
                hours=rule_start_hour, minutes=rule_start_minute
            )
            window_end = day_start + timedelta(
                hours=rule_end_hour, minutes=rule_end_minute
            )

            step = timedelta(minutes=int(rule["slot_minutes"]))
            length = timedelta(
                minutes=int(duration_minutes or rule["slot_minutes"])
            )

            if step <= timedelta(0) or length <= timedelta(0):
                continue

            cursor_time = window_start

            while cursor_time + length <= window_end:
                slot_start = normalize_instant(cursor_time)
                slot_end = normalize_instant(cursor_time + length)
                cursor_time += step

                if (slot_start, slot_end) in seen:
                    continue

                seen.add((slot_start, slot_end))

                # Same half-open test as the booking guard: a slot that starts
                # exactly when an appointment ends is free.
                taken = any(
                    slot_start < busy_end and slot_end > busy_start
                    for busy_start, busy_end in busy
                )

                if taken:
                    continue

                slots.append(
                    {
                        "starts_at": slot_start,
                        "ends_at": slot_end,
                        "rule_id": int(rule["id"]),
                    }
                )

        slots.sort(key=lambda slot: (slot["starts_at"], slot["ends_at"]))

        return {
            "date": day.isoformat(),
            "weekday": weekday,
            "weekday_name": WEEKDAY_NAMES[weekday],
            "staff_user_id": staff_user_id,
            "slots": slots,
            "rules": rules,
            "busy": [
                {"starts_at": start, "ends_at": end} for start, end in busy
            ],
        }


appointment_service = AppointmentService()
