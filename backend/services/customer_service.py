from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CustomerService:
    def __init__(self) -> None:
        # Schema setup happens explicitly via main.py's lifespan (after
        # database.database.db.create_tables()), not here — see the
        # matching note in ConversationControlService.__init__.
        pass

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS customers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    display_name TEXT,
                    internal_name TEXT,
                    profile_picture TEXT,
                    phone TEXT,
                    email TEXT,
                    language TEXT,
                    country TEXT,
                    timezone TEXT,
                    notes TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS customer_identities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    customer_id INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    username TEXT,
                    display_name TEXT,
                    profile_picture TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(company_id, channel, external_user_id),
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_customer_identity_lookup
                ON customer_identities(company_id, channel, external_user_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS customer_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    customer_id INTEGER NOT NULL,
                    actor_user_id INTEGER,
                    action TEXT NOT NULL,
                    data_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "customer_id" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN customer_id INTEGER")
            conn.commit()

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def upsert_from_channel(
        self,
        *,
        company_id: int,
        channel: str,
        external_user_id: str,
        display_name: str | None = None,
        profile_picture: str | None = None,
        username: str | None = None,
    ) -> dict[str, Any]:
        normalized_channel = channel.strip().lower()
        normalized_external_id = external_user_id.strip()
        display_name = self._clean(display_name)
        profile_picture = self._clean(profile_picture)
        username = self._clean(username)
        now = utc_now_iso()

        with db.connect() as conn:
            identity = conn.execute(
                """
                SELECT ci.*, c.internal_name, c.phone, c.email, c.language,
                       c.country, c.timezone, c.notes, c.first_seen_at, c.last_seen_at
                FROM customer_identities ci
                JOIN customers c ON c.id = ci.customer_id
                WHERE ci.company_id = ? AND ci.channel = ? AND ci.external_user_id = ?
                LIMIT 1
                """,
                (company_id, normalized_channel, normalized_external_id),
            ).fetchone()

            if identity:
                customer_id = int(identity["customer_id"])
                current_name = self._clean(identity["display_name"])
                effective_name = display_name or current_name
                effective_picture = profile_picture or self._clean(identity["profile_picture"])
                conn.execute(
                    """
                    UPDATE customer_identities
                    SET display_name = COALESCE(?, display_name),
                        profile_picture = COALESCE(?, profile_picture),
                        username = COALESCE(?, username),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (display_name, profile_picture, username, now, identity["id"]),
                )
                conn.execute(
                    """
                    UPDATE customers
                    SET display_name = CASE
                            WHEN display_name IS NULL OR TRIM(display_name) = ''
                            THEN COALESCE(?, display_name)
                            ELSE display_name
                        END,
                        profile_picture = COALESCE(profile_picture, ?),
                        last_seen_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (effective_name, effective_picture, now, now, customer_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO customers (
                        company_id, display_name, profile_picture,
                        first_seen_at, last_seen_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (company_id, display_name, profile_picture, now, now, now, now),
                )
                customer_id = int(cursor.lastrowid)
                conn.execute(
                    """
                    INSERT INTO customer_identities (
                        company_id, customer_id, channel, external_user_id,
                        username, display_name, profile_picture, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id, customer_id, normalized_channel,
                        normalized_external_id, username, display_name,
                        profile_picture, now, now,
                    ),
                )

            conn.execute(
                """
                UPDATE conversations
                SET customer_id = ?,
                    official_customer_name = COALESCE(?, official_customer_name),
                    customer_profile_picture = COALESCE(?, customer_profile_picture),
                    updated_at = ?
                WHERE company_id = ? AND channel = ? AND external_user_id = ?
                """,
                (
                    customer_id, display_name, profile_picture, now,
                    company_id, normalized_channel, normalized_external_id,
                ),
            )
            conn.commit()

        return self.get_customer(company_id=company_id, customer_id=customer_id)

    def get_customer(self, *, company_id: int, customer_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE id = ? AND company_id = ? LIMIT 1",
                (customer_id, company_id),
            ).fetchone()
            if not row:
                raise KeyError("Customer not found")
            identities = conn.execute(
                """
                SELECT channel, external_user_id, username, display_name,
                       profile_picture, created_at, updated_at
                FROM customer_identities
                WHERE customer_id = ? AND company_id = ?
                ORDER BY id
                """,
                (customer_id, company_id),
            ).fetchall()
            conversation_count = conn.execute(
                "SELECT COUNT(*) AS total FROM conversations WHERE customer_id = ? AND company_id = ?",
                (customer_id, company_id),
            ).fetchone()["total"]
        result = dict(row)
        result["identities"] = [dict(item) for item in identities]
        result["conversation_count"] = int(conversation_count or 0)
        return result

    def list_customers(
        self,
        *,
        company_id: int,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["c.company_id = ?"]
        params: list[Any] = [company_id]
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            where.append(
                "(c.display_name LIKE ? OR c.internal_name LIKE ? OR c.phone LIKE ? OR c.email LIKE ? "
                "OR EXISTS (SELECT 1 FROM customer_identities ci WHERE ci.customer_id = c.id "
                "AND (ci.external_user_id LIKE ? OR ci.username LIKE ? OR ci.display_name LIKE ?)))"
            )
            params.extend([pattern] * 7)
        clause = " AND ".join(where)
        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM customers c WHERE {clause}", params
            ).fetchone()["total"]
            rows = conn.execute(
                f"""
                SELECT c.*,
                       (SELECT COUNT(*) FROM customer_identities ci WHERE ci.customer_id = c.id) AS identity_count,
                       (SELECT COUNT(*) FROM conversations cv WHERE cv.customer_id = c.id) AS conversation_count
                FROM customers c
                WHERE {clause}
                ORDER BY c.last_seen_at DESC, c.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(500, limit)), max(0, offset)],
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": int(total or 0)}

    def update_customer(
        self,
        *,
        company_id: int,
        customer_id: int,
        values: dict[str, Any],
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        allowed = {
            "display_name", "internal_name", "phone", "email",
            "language", "country", "timezone", "notes",
        }
        cleaned = {key: self._clean(value) for key, value in values.items() if key in allowed}
        if not cleaned:
            return self.get_customer(company_id=company_id, customer_id=customer_id)
        now = utc_now_iso()
        assignments = ", ".join(f"{key} = ?" for key in cleaned)
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM customers WHERE id = ? AND company_id = ?",
                (customer_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Customer not found")
            conn.execute(
                f"UPDATE customers SET {assignments}, updated_at = ? WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, customer_id, company_id],
            )
            import json
            conn.execute(
                """
                INSERT INTO customer_audit (
                    company_id, customer_id, actor_user_id, action, data_json, created_at
                ) VALUES (?, ?, ?, 'customer_updated', ?, ?)
                """,
                (company_id, customer_id, actor_user_id, json.dumps(cleaned, ensure_ascii=False), now),
            )
            conn.commit()
        return self.get_customer(company_id=company_id, customer_id=customer_id)


customer_service = CustomerService()
