"""The sections one company offers its customers.

A department is what the customer actually sees: a line in the "available
sections" sentence, a quick-reply button, and the label the assistant routes a
conversation to. Every one of them belongs to exactly one company and lives in
that company's own encrypted database.

There used to be no such table. The engine read ``config/business_modules.json``
— a single file on disk naming one company's departments (Sales, Accounting,
Maintenance, IPTV, Information) — and served it as the menu to every company on
the platform, so a customer messaging one business was offered another
business's departments.

Two consequences of that history are deliberate here:

* **There is no default set.** A company that has defined no departments gets an
  empty list, and the engine omits the menu entirely. Falling back to a built-in
  list would recreate exactly the leak this replaces.
* **Nothing is seeded at provisioning.** The founding company's real departments
  are imported into that one company by
  ``tools/manage_platform.py import-departments``.

Column meaning, since the names are short:

``code``
    The stable identifier the rest of the engine routes on (``sales``,
    ``iptv``…). It is what ``session['current_department']`` and the model's
    ``department`` field carry, so it is normalised and unique per company, and
    a rename of the display name never changes it.
``name_ar`` / ``name_en``
    What the section is called in the overview sentence.
``button_ar`` / ``button_en``
    The quick-reply label. Often shorter than the name, and sometimes absent —
    a section can exist without being offered as a button.
``sort_order``
    The order the menu is rendered in. Ties break on ``id`` so the order is
    total and stable rather than whatever the database happens to return.

Table creation belongs to ``database/schema_tenant.py`` alone. This service
issues no DDL.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


MAX_CODE = 60
MAX_NAME = 120
MAX_BUTTON = 60

_CODE_PATTERN = re.compile(r"[^a-z0-9_]+")


class BusinessDepartmentError(ValueError):
    """A department change that was refused, with a reason worth showing."""


class BusinessDepartmentService:
    # The whole enabled set is rendered as buttons and serialized into the
    # system prompt, so it has to be bounded. A business with more sections than
    # this needs a different navigation, not a longer button row.
    MAX_DEPARTMENTS = 40

    EDITABLE_FIELDS = (
        "code",
        "enabled",
        "name_ar",
        "name_en",
        "button_ar",
        "button_en",
        "sort_order",
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean(value: Any, limit: int) -> str | None:
        if value is None:
            return None

        text = str(value).strip()

        if not text:
            return None

        return text[:limit]

    @classmethod
    def _normalize_code(cls, value: Any) -> str:
        """Fold a code down to what the engine can route on.

        The code is compared against the model's ``department`` field and stored
        in the session, both of which are lowercase ascii. Accepting
        ``Sales Team`` verbatim would produce a department nothing ever matches.
        """
        text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        text = _CODE_PATTERN.sub("", text)

        if not text:
            raise BusinessDepartmentError(
                "A department needs a code made of letters, numbers or "
                "underscores — it is what the assistant routes on."
            )

        return text[:MAX_CODE]

    @classmethod
    def _row(cls, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        return data

    def _existing(self, conn, *, company_id: int, department_id: int):
        return conn.execute(
            """
            SELECT * FROM business_departments
            WHERE id = ? AND company_id = ?
            LIMIT 1
            """,
            (int(department_id), int(company_id)),
        ).fetchone()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_departments(
        self,
        *,
        company_id: int,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        company_id = int(company_id)

        where = ["company_id = ?"]
        params: list[Any] = [company_id]

        if enabled_only:
            where.append("enabled = 1")

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM business_departments
                WHERE {clause}
                ORDER BY sort_order ASC, id ASC
                LIMIT ?
                """,
                [*params, self.MAX_DEPARTMENTS],
            ).fetchall()

        return [self._row(row) for row in rows]

    def get_department(
        self,
        *,
        company_id: int,
        department_id: int,
    ) -> dict[str, Any] | None:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            row = self._existing(
                conn, company_id=company_id, department_id=department_id
            )

        return self._row(row) if row else None

    def find_by_code(
        self,
        *,
        company_id: int,
        code: Any,
        enabled_only: bool = False,
    ) -> dict[str, Any] | None:
        """The department carrying this code, inside this company only.

        The code is normalised the same way it was on the way in, so a value
        that came back from the model as ``"Sales Team"`` still resolves to the
        row stored as ``sales_team``. A code this company never defined returns
        ``None`` — it is not looked for anywhere else, which is what stops
        another company's vocabulary from routing this company's customer.
        """
        company_id = int(company_id)

        try:
            clean_code = self._normalize_code(code)
        except BusinessDepartmentError:
            return None

        clause = "company_id = ? AND code = ?"
        params: list[Any] = [company_id, clean_code]

        if enabled_only:
            clause += " AND enabled = 1"

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                f"SELECT * FROM business_departments WHERE {clause} LIMIT 1",
                params,
            ).fetchone()

        return self._row(row) if row else None

    def codes(self, company_id: int | None, enabled_only: bool = True) -> list[str]:
        """Just the codes, for anywhere a vocabulary is needed.

        This is the list ``AIRouter`` validates the model's answer against and
        the list the inbox validates a ``PATCH`` against. Both used to carry a
        hardcoded set of nine, so a code a company actually defined was thrown
        away as invalid.
        """
        if not company_id:
            return []

        return [
            str(row["code"])
            for row in self.for_assistant(company_id)
            if row.get("code") and (row.get("enabled") or not enabled_only)
        ]

    def for_channel_account(
        self,
        *,
        company_id: int | None,
        channel_account_id: int | None,
    ) -> dict[str, Any] | None:
        """The department an account feeds by default, or ``None``.

        Reads the pointer from the control database and the department itself
        from the company's own database, and checks the account really belongs
        to the company being asked about — an account id is a small integer and
        guessing one must not reach across a tenant boundary.

        Never raises: this runs on the customer reply path, where a routing
        default that cannot be read must cost the conversation its department,
        not the customer their answer.
        """
        if not company_id or not channel_account_id:
            return None

        try:
            with database_manager.control() as conn:
                row = conn.execute(
                    """
                    SELECT department_id FROM channel_accounts
                    WHERE id = ? AND company_id = ?
                    LIMIT 1
                    """,
                    (int(channel_account_id), int(company_id)),
                ).fetchone()

            if not row or row["department_id"] is None:
                return None

            return self.get_department(
                company_id=int(company_id),
                department_id=int(row["department_id"]),
            )
        except Exception:
            logger.exception(
                "Could not read the default department of account %s "
                "for company %s",
                channel_account_id,
                company_id,
            )
            return None

    def for_assistant(self, company_id: int | None) -> list[dict[str, Any]]:
        """The enabled departments, or nothing — never raises.

        This runs on the customer reply path. No company means no departments,
        because guessing one is the leak this module replaces; and a database
        that will not open must degrade to an assistant with no menu rather than
        to a customer with no answer.
        """
        if not company_id:
            return []

        try:
            return self.list_departments(company_id=company_id, enabled_only=True)
        except Exception:
            logger.exception(
                "Could not read the departments of company %s", company_id
            )
            return []

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def create_department(
        self,
        *,
        company_id: int,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        company_id = int(company_id)

        code = self._normalize_code(data.get("code"))
        name_ar = self._clean(data.get("name_ar"), MAX_NAME)
        name_en = self._clean(data.get("name_en"), MAX_NAME)

        if not name_ar and not name_en:
            raise BusinessDepartmentError(
                "A department needs an Arabic or an English name, otherwise the "
                "customer sees a blank entry in the menu."
            )

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    "SELECT COUNT(*) AS total FROM business_departments "
                    "WHERE company_id = ?",
                    (company_id,),
                ).fetchone()["total"]
            )

            if total >= self.MAX_DEPARTMENTS:
                raise BusinessDepartmentError(
                    f"Keep {self.MAX_DEPARTMENTS} departments or fewer; every "
                    "enabled one is a button and a line in every prompt."
                )

            clash = conn.execute(
                """
                SELECT id FROM business_departments
                WHERE company_id = ? AND code = ?
                LIMIT 1
                """,
                (company_id, code),
            ).fetchone()

            if clash:
                raise BusinessDepartmentError(
                    f"A department with the code {code!r} already exists."
                )

            sort_order = data.get("sort_order")

            if sort_order is None:
                sort_order = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next "
                        "FROM business_departments WHERE company_id = ?",
                        (company_id,),
                    ).fetchone()["next"]
                )

            cursor = conn.execute(
                """
                INSERT INTO business_departments (
                    company_id, code, enabled, name_ar, name_en,
                    button_ar, button_en, sort_order, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    code,
                    1 if data.get("enabled", True) else 0,
                    name_ar,
                    name_en,
                    self._clean(data.get("button_ar"), MAX_BUTTON),
                    self._clean(data.get("button_en"), MAX_BUTTON),
                    int(sort_order),
                    now,
                    now,
                ),
            )
            conn.commit()
            department_id = int(cursor.lastrowid)

        logger.info(
            "Created department id=%s code=%s company id=%s",
            department_id,
            code,
            company_id,
        )

        return self.get_department(
            company_id=company_id, department_id=department_id
        )

    def update_department(
        self,
        *,
        company_id: int,
        department_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any] | None:
        company_id = int(company_id)
        department_id = int(department_id)

        updates: dict[str, Any] = {}

        for field in self.EDITABLE_FIELDS:
            if field not in values:
                continue

            value = values[field]

            if field == "code":
                updates[field] = self._normalize_code(value)
            elif field == "enabled":
                updates[field] = 1 if bool(value) else 0
            elif field == "sort_order":
                updates[field] = int(value or 0)
            elif field in ("name_ar", "name_en"):
                updates[field] = self._clean(value, MAX_NAME)
            else:
                updates[field] = self._clean(value, MAX_BUTTON)

        if not updates:
            return self.get_department(
                company_id=company_id, department_id=department_id
            )

        with database_manager.tenant(company_id) as conn:
            existing = self._existing(
                conn, company_id=company_id, department_id=department_id
            )

            if not existing:
                return None

            name_ar = updates.get("name_ar", existing["name_ar"])
            name_en = updates.get("name_en", existing["name_en"])

            if not name_ar and not name_en:
                raise BusinessDepartmentError(
                    "A department needs an Arabic or an English name, otherwise "
                    "the customer sees a blank entry in the menu."
                )

            if "code" in updates and updates["code"] != existing["code"]:
                clash = conn.execute(
                    """
                    SELECT id FROM business_departments
                    WHERE company_id = ? AND code = ? AND id <> ?
                    LIMIT 1
                    """,
                    (company_id, updates["code"], department_id),
                ).fetchone()

                if clash:
                    raise BusinessDepartmentError(
                        f"A department with the code {updates['code']!r} "
                        "already exists."
                    )

            assignments = ", ".join(f"{field} = ?" for field in updates)

            conn.execute(
                f"""
                UPDATE business_departments
                SET {assignments}, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                [*updates.values(), utc_now_iso(), department_id, company_id],
            )
            conn.commit()

        logger.info(
            "Updated department id=%s company id=%s fields=%s",
            department_id,
            company_id,
            sorted(updates),
        )

        return self.get_department(
            company_id=company_id, department_id=department_id
        )

    def delete_department(self, *, company_id: int, department_id: int) -> bool:
        company_id = int(company_id)
        department_id = int(department_id)

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                "DELETE FROM business_departments WHERE id = ? AND company_id = ?",
                (department_id, company_id),
            )
            conn.commit()
            deleted = cursor.rowcount > 0

        if deleted:
            logger.info(
                "Deleted department id=%s company id=%s", department_id, company_id
            )

        return deleted

    def reorder(
        self,
        *,
        company_id: int,
        department_ids: list[int],
    ) -> list[dict[str, Any]]:
        """Renumber the menu in the order the ids are given.

        Ids this company does not own are ignored rather than applied, so a
        crafted list cannot renumber another company's menu; ids left out keep
        their place after the ones named here.
        """
        company_id = int(company_id)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            owned = {
                int(row["id"])
                for row in conn.execute(
                    "SELECT id FROM business_departments WHERE company_id = ?",
                    (company_id,),
                ).fetchall()
            }

            position = 0

            for department_id in department_ids:
                try:
                    department_id = int(department_id)
                except (TypeError, ValueError):
                    continue

                if department_id not in owned:
                    continue

                conn.execute(
                    """
                    UPDATE business_departments
                    SET sort_order = ?, updated_at = ?
                    WHERE id = ? AND company_id = ?
                    """,
                    (position, now, department_id, company_id),
                )
                position += 1

            conn.commit()

        return self.list_departments(company_id=company_id)

    def upsert_by_code(
        self,
        *,
        company_id: int,
        code: str,
        data: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Create or refresh the department carrying this code.

        Returns ``(department, created)``. Matching on the code is what makes
        the import repeatable: re-running it corrects the existing rows instead
        of stacking a second copy of the menu, and the code the session already
        stores keeps resolving.
        """
        company_id = int(company_id)
        clean_code = self._normalize_code(code)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT id FROM business_departments
                WHERE company_id = ? AND code = ?
                LIMIT 1
                """,
                (company_id, clean_code),
            ).fetchone()

        if row:
            updated = self.update_department(
                company_id=company_id,
                department_id=int(row["id"]),
                values={key: value for key, value in data.items() if key != "code"},
            )
            return updated, False

        created = self.create_department(
            company_id=company_id,
            data={**data, "code": clean_code},
        )
        return created, True


business_department_service = BusinessDepartmentService()
