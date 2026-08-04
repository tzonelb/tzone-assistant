"""Company-scoped CRUD for the Calls module: a call log recording phone
calls with customers (direction, outcome, duration, notes), linked to
customer profiles. Mirrors the layered service pattern in
task_service.py -- the `call_logs` table lives in database/database.py's
central schema init, so this module does not own/create its own tables.

NOTE: this is a manual call LOG, not a live dialer. Actually placing
calls from inside the platform requires an external telephony provider
(e.g. Twilio), a paid account, and phone numbers -- a business decision
with monthly costs that has deliberately not been made yet. When it is,
a provider abstraction can be added alongside this log without changing
its schema (the log records the outcome either way)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ALLOWED_DIRECTIONS = {"inbound", "outbound"}
ALLOWED_OUTCOMES = {"answered", "no_answer", "busy", "voicemail", "wrong_number"}


class CallLogConflictError(Exception):
    """Raised when an update's optimistic-concurrency token is stale."""


class CallLogValidationError(ValueError):
    """Raised for invalid field values: a bad direction/outcome code, a
    missing called_at, a negative duration, or a customer that does not
    belong to this company."""


_CALL_SELECT = """
    SELECT
        c.*,
        customer.display_name AS customer_name,
        logger.full_name AS logged_by_name
    FROM call_logs c
    LEFT JOIN customers customer ON customer.id = c.customer_id
    LEFT JOIN users logger ON logger.id = c.logged_by
"""


class CallLogService:
    EDITABLE_FIELDS = {
        "customer_id",
        "phone_number",
        "direction",
        "outcome",
        "duration_seconds",
        "notes",
        "called_at",
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
            if key in ("phone_number", "notes", "called_at"):
                cleaned[key] = self._clean_text(value)
            elif key == "direction":
                direction = (self._clean_text(value) or "outbound").lower()
                if direction not in ALLOWED_DIRECTIONS:
                    raise CallLogValidationError(
                        f"direction must be one of {sorted(ALLOWED_DIRECTIONS)}"
                    )
                cleaned[key] = direction
            elif key == "outcome":
                outcome = (self._clean_text(value) or "answered").lower()
                if outcome not in ALLOWED_OUTCOMES:
                    raise CallLogValidationError(
                        f"outcome must be one of {sorted(ALLOWED_OUTCOMES)}"
                    )
                cleaned[key] = outcome
            elif key == "duration_seconds":
                if value is None or value == "":
                    cleaned[key] = None
                    continue
                try:
                    duration = int(value)
                except (TypeError, ValueError) as exc:
                    raise CallLogValidationError(
                        "duration_seconds must be a whole number"
                    ) from exc
                if duration < 0:
                    raise CallLogValidationError(
                        "duration_seconds cannot be negative"
                    )
                cleaned[key] = duration
            else:
                cleaned[key] = value
        return cleaned

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
            raise CallLogValidationError("Customer must belong to this company.")

    def list_calls(
        self,
        *,
        company_id: int,
        direction: str | None = None,
        outcome: str | None = None,
        customer_id: int | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["c.company_id = ?"]
        params: list[Any] = [company_id]

        if direction and direction != "all":
            where.append("c.direction = ?")
            params.append(direction)

        if outcome and outcome != "all":
            where.append("c.outcome = ?")
            params.append(outcome)

        if customer_id is not None:
            where.append("c.customer_id = ?")
            params.append(customer_id)

        if search and search.strip():
            pattern = f"%{search.strip()}%"
            where.append(
                "(c.phone_number LIKE ? OR c.notes LIKE ? OR customer.display_name LIKE ?)"
            )
            params.extend([pattern, pattern, pattern])

        clause = " AND ".join(where)

        with db.connect() as conn:
            total = conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM call_logs c
                LEFT JOIN customers customer ON customer.id = c.customer_id
                WHERE {clause}
                """,
                params,
            ).fetchone()["total"]

            rows = conn.execute(
                f"""
                {_CALL_SELECT}
                WHERE {clause}
                ORDER BY c.called_at DESC, c.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(500, limit)), max(0, offset)],
            ).fetchall()

        return {"items": [dict(row) for row in rows], "total": int(total or 0)}

    def get_call(self, *, company_id: int, call_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                f"{_CALL_SELECT} WHERE c.id = ? AND c.company_id = ? LIMIT 1",
                (call_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Call not found")
        return dict(row)

    def create_call(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        cleaned = self._clean_values(values)

        called_at = cleaned.get("called_at")
        if not called_at:
            raise CallLogValidationError("called_at is required")

        if not cleaned.get("customer_id") and not cleaned.get("phone_number"):
            raise CallLogValidationError(
                "Either a customer or a phone number is required."
            )

        direction = cleaned.get("direction") or "outbound"
        outcome = cleaned.get("outcome") or "answered"
        now = utc_now_iso()

        with db.connect() as conn:
            self._validate_customer(conn, company_id, cleaned.get("customer_id"))

            cursor = conn.execute(
                """
                INSERT INTO call_logs (
                    company_id, customer_id, phone_number, direction, outcome,
                    duration_seconds, notes, called_at, logged_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    cleaned.get("customer_id"),
                    cleaned.get("phone_number"),
                    direction,
                    outcome,
                    cleaned.get("duration_seconds"),
                    cleaned.get("notes"),
                    called_at,
                    actor_user_id,
                    now,
                    now,
                ),
            )
            call_id = int(cursor.lastrowid)
            conn.commit()

        # Bot Triggers hook: a call was just logged. fire_event never raises.
        try:
            from backend.services.trigger_service import trigger_service

            trigger_service.fire_event(
                company_id=company_id,
                trigger_type="call_logged",
                dedupe_suffix=f"call_logged:call:{call_id}",
                context={
                    "customer_id": cleaned.get("customer_id"),
                    "reference_id": call_id,
                    "summary": (
                        f"Call logged ({direction}, {outcome})"
                    ),
                },
            )
        except Exception as exc:
            print("TRIGGER HOOK ERROR (call_logged):", exc)

        return self.get_call(company_id=company_id, call_id=call_id)

    def update_call(
        self,
        *,
        company_id: int,
        call_id: int,
        values: dict[str, Any],
        expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        cleaned = self._clean_values(values)
        now = utc_now_iso()

        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id, updated_at, customer_id, phone_number "
                "FROM call_logs WHERE id = ? AND company_id = ?",
                (call_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Call not found")

            if (
                expected_updated_at is not None
                and str(existing["updated_at"]) != str(expected_updated_at)
            ):
                raise CallLogConflictError(
                    "This call record was changed elsewhere. Reload to see "
                    "the latest details before editing."
                )

            if "called_at" in cleaned and not cleaned["called_at"]:
                raise CallLogValidationError("called_at cannot be empty")

            if not cleaned:
                return self.get_call(company_id=company_id, call_id=call_id)

            # The create-time invariant (a call references a customer OR a
            # phone number) must survive edits: reject an update that would
            # leave both empty.
            resulting_customer_id = (
                cleaned["customer_id"]
                if "customer_id" in cleaned
                else existing["customer_id"]
            )
            resulting_phone_number = (
                cleaned["phone_number"]
                if "phone_number" in cleaned
                else existing["phone_number"]
            )
            if not resulting_customer_id and not resulting_phone_number:
                raise CallLogValidationError(
                    "Either a customer or a phone number is required."
                )

            if "customer_id" in cleaned:
                self._validate_customer(conn, company_id, cleaned["customer_id"])

            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE call_logs SET {assignments}, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, call_id, company_id],
            )
            conn.commit()

        return self.get_call(company_id=company_id, call_id=call_id)

    def delete_call(self, *, company_id: int, call_id: int) -> bool:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM call_logs WHERE id = ? AND company_id = ?",
                (call_id, company_id),
            )
            conn.commit()
            return cursor.rowcount > 0


call_log_service = CallLogService()
