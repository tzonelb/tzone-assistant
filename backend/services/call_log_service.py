"""The call history of record.

This is a call **log**: what happened on the phone, written down. A call typed
in after a walk-in, a call returned from somebody's mobile, and a call the
Dialer placed through a telephony provider all end up here, in one table, so
"have we spoken to this customer" has a single answer instead of one per route
the call happened to take.

It deliberately does not place calls. That is `telephony_service`, which needs a
provider account, a phone number and credentials the company has to arrange —
and which writes finished calls into this log when they end, rather than keeping
a second history of its own.

Two rules the rest of the platform shares:

* The company is a database, not a filter. Every call resolves the company's own
  encrypted file through `database_manager.tenant`, so a call log belonging to
  another company is not merely excluded from a query, it is in a file this code
  never opened.
* Table creation belongs to `database/schema_tenant.py` alone. Nothing here
  creates or alters a table at runtime.

Employee names are resolved by the router through
`auth_service.user_display_names`, not joined here: `users` lives in the control
plane, a separate SQLite file, so there is no join to make.
"""

from __future__ import annotations

import logging
from typing import Any

from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)


# The vocabulary the screen offers, and the only values that can be stored. Kept
# here rather than in the route so the Dialer's mirror writes (below, through
# `create_call_log`) are validated by exactly the same list the form is built
# from — a status the form cannot offer is a status nothing can insert.
DIRECTIONS: tuple[str, ...] = ("inbound", "outbound")
STATUSES: tuple[str, ...] = ("completed", "missed", "no_answer", "voicemail")

MAX_PHONE = 80
MAX_NOTES = 2000


class CallLogNotFound(Exception):
    """No such call in this company's history."""


class CustomerNotFound(Exception):
    """The contact a call was linked to does not belong to this company."""


class CallLogService:
    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create_call_log(
        self,
        *,
        company_id: int,
        direction: str,
        phone_number: str | None = None,
        customer_id: int | None = None,
        duration_seconds: int = 0,
        status: str = "completed",
        notes: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Record one call that happened.

        A call needs somebody on the other end of it: either a contact from the
        customer list or a bare phone number. Refusing the one with neither is
        the difference between a history and a list of blank rows.
        """
        company_id = int(company_id)

        direction = (direction or "").strip().lower()

        if direction not in DIRECTIONS:
            raise ValueError(
                f'"{direction}" is not a valid direction. '
                f'Choose one of: {", ".join(DIRECTIONS)}.'
            )

        status = (status or "completed").strip().lower()

        if status not in STATUSES:
            raise ValueError(
                f'"{status}" is not a valid outcome. '
                f'Choose one of: {", ".join(STATUSES)}.'
            )

        duration_seconds = int(duration_seconds or 0)

        if duration_seconds < 0:
            raise ValueError("A call cannot have lasted less than no time.")

        phone_number = (phone_number or "").strip()[:MAX_PHONE] or None
        notes = (notes or "").strip()[:MAX_NOTES] or None

        if not phone_number and customer_id is None:
            raise ValueError("Give a phone number or pick a contact.")

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            self._assert_customer(conn, company_id, customer_id)

            cursor = conn.execute(
                """
                INSERT INTO call_logs (
                    company_id, customer_id, direction, phone_number,
                    duration_seconds, status, notes, called_by_user_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    customer_id,
                    direction,
                    phone_number,
                    duration_seconds,
                    status,
                    notes,
                    actor_user_id,
                    now,
                ),
            )
            call_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_call_log(company_id=company_id, call_id=call_id)

    def delete_call_log(self, *, company_id: int, call_id: int) -> None:
        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                "DELETE FROM call_logs WHERE id = ? AND company_id = ?",
                (int(call_id), int(company_id)),
            )
            conn.commit()

            if cursor.rowcount == 0:
                raise CallLogNotFound("Call log not found")

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_call_logs(
        self,
        *,
        company_id: int,
        customer_id: int | None = None,
        direction: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)

        where = ["call_logs.company_id = ?"]
        params: list[Any] = [company_id]

        if customer_id is not None:
            where.append("call_logs.customer_id = ?")
            params.append(int(customer_id))

        # An unknown filter value narrows to nothing rather than being ignored:
        # a screen asking for an outcome that does not exist has asked a
        # question with an empty answer, and answering it with every row would
        # look like the filter had been applied.
        if direction:
            where.append("call_logs.direction = ?")
            params.append(str(direction).strip().lower())

        if status:
            where.append("call_logs.status = ?")
            params.append(str(status).strip().lower())

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                f"""
                SELECT call_logs.*,
                       COALESCE(customers.display_name, customers.internal_name)
                           AS customer_name
                FROM call_logs
                LEFT JOIN customers ON customers.id = call_logs.customer_id
                WHERE {clause}
                ORDER BY call_logs.created_at DESC, call_logs.id DESC
                """,
                params,
            ).fetchall()

        items = [dict(row) for row in rows]

        return {"items": items, "total": len(items)}

    def get_call_log(self, *, company_id: int, call_id: int) -> dict[str, Any]:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                """
                SELECT call_logs.*,
                       COALESCE(customers.display_name, customers.internal_name)
                           AS customer_name
                FROM call_logs
                LEFT JOIN customers ON customers.id = call_logs.customer_id
                WHERE call_logs.id = ? AND call_logs.company_id = ?
                """,
                (int(call_id), int(company_id)),
            ).fetchone()

        if not row:
            raise CallLogNotFound("Call log not found")

        return dict(row)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _assert_customer(conn, company_id: int, customer_id: int | None) -> None:
        """A linked contact has to be one of this company's.

        Inside a tenant file every customer already belongs to this company, so
        this is checking that the id exists at all — but the `company_id` clause
        stays, for the same reason every other query here carries one: a row
        that arrived in the wrong file should fail loudly rather than be read.
        """
        if customer_id is None:
            return

        found = conn.execute(
            "SELECT 1 FROM customers WHERE id = ? AND company_id = ? LIMIT 1",
            (int(customer_id), int(company_id)),
        ).fetchone()

        if not found:
            raise CustomerNotFound("Customer not found")


call_log_service = CallLogService()
