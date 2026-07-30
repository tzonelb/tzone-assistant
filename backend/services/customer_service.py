from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Lifecycle stages are a fixed, ordered pipeline (unlike Departments,
# which are free-form per company) — CRM stages need a predictable
# order for reporting/board-style views later. "lead" is the default
# for every newly auto-created contact.
LIFECYCLE_STAGES = ["lead", "active", "customer", "vip", "churned"]
DEFAULT_LIFECYCLE_STAGE = "lead"


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

            customer_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(customers)").fetchall()
            }
            if "tags_json" not in customer_columns:
                conn.execute("ALTER TABLE customers ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
            if "lifecycle_stage" not in customer_columns:
                conn.execute(
                    f"ALTER TABLE customers ADD COLUMN lifecycle_stage TEXT NOT NULL DEFAULT '{DEFAULT_LIFECYCLE_STAGE}'"
                )
            if "assigned_user_id" not in customer_columns:
                conn.execute("ALTER TABLE customers ADD COLUMN assigned_user_id INTEGER")
            if "custom_fields_json" not in customer_columns:
                conn.execute("ALTER TABLE customers ADD COLUMN custom_fields_json TEXT NOT NULL DEFAULT '{}'")
            if "documents_json" not in customer_columns:
                conn.execute("ALTER TABLE customers ADD COLUMN documents_json TEXT NOT NULL DEFAULT '[]'")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS customer_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    filters_json TEXT NOT NULL DEFAULT '{}',
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(company_id, name),
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            conn.commit()

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        if not tags:
            return []
        normalized: list[str] = []
        for tag in tags:
            cleaned = str(tag).strip()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    @staticmethod
    def _normalize_custom_fields(fields: dict[str, Any] | None) -> dict[str, str]:
        """Free-form key/value fields the employee defines per contact —
        e.g. "ID number", "Insurance plan", "Preferred branch". Deliberately
        schemaless so any future vertical (clinic, restaurant, delivery...)
        can capture what it needs without a backend change."""
        if not fields:
            return {}
        normalized: dict[str, str] = {}
        for key, value in fields.items():
            clean_key = str(key).strip()[:80]
            clean_value = str(value).strip()[:2000] if value is not None else ""
            if clean_key:
                normalized[clean_key] = clean_value
        return dict(list(normalized.items())[:50])

    @staticmethod
    def _normalize_documents(documents: list[Any] | None) -> list[dict[str, str]]:
        if not documents:
            return []
        normalized: list[dict[str, str]] = []
        for item in documents:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()[:120]
            url = str(item.get("url") or "").strip()[:2000]
            if label and url:
                normalized.append({"label": label, "url": url})
        return normalized[:50]

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
            assigned_user = None
            if row["assigned_user_id"] is not None:
                assigned_user = conn.execute(
                    "SELECT id, full_name, email FROM users WHERE id = ?",
                    (row["assigned_user_id"],),
                ).fetchone()
        result = dict(row)
        result["identities"] = [dict(item) for item in identities]
        result["channels"] = sorted({item["channel"] for item in identities})
        result["conversation_count"] = int(conversation_count or 0)
        result["tags"] = self._parse_tags(result.pop("tags_json", "[]"))
        result["custom_fields"] = self._parse_json_object(result.pop("custom_fields_json", "{}"))
        result["documents"] = self._parse_json_list(result.pop("documents_json", "[]"))
        result["assigned_user_name"] = (
            (assigned_user["full_name"] or assigned_user["email"]) if assigned_user else None
        )
        return result

    @staticmethod
    def _parse_json_object(raw: str | None) -> dict[str, Any]:
        try:
            parsed = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _parse_json_list(raw: str | None) -> list[Any]:
        try:
            parsed = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    def get_timeline(self, *, company_id: int, customer_id: int) -> list[dict[str, Any]]:
        """Chronological history for the Client File — profile edits (from
        customer_audit) merged with every conversation this contact has had,
        newest first. This is intentionally read-only/derived: nothing new
        to maintain, it just reads history that already exists."""
        with db.connect() as conn:
            exists = conn.execute(
                "SELECT id FROM customers WHERE id = ? AND company_id = ?",
                (customer_id, company_id),
            ).fetchone()
            if not exists:
                raise KeyError("Customer not found")

            audit_rows = conn.execute(
                """
                SELECT ca.action, ca.data_json, ca.created_at,
                       COALESCE(u.full_name, u.email) AS actor_name
                FROM customer_audit ca
                LEFT JOIN users u ON u.id = ca.actor_user_id
                WHERE ca.customer_id = ? AND ca.company_id = ?
                """,
                (customer_id, company_id),
            ).fetchall()

            conversation_rows = conn.execute(
                """
                SELECT c.channel, c.external_user_id, c.department, c.topic, c.status, c.created_at,
                       COALESCE(u.full_name, u.email) AS handled_by_name
                FROM conversations c
                LEFT JOIN users u ON u.id = c.assigned_user_id
                WHERE c.customer_id = ? AND c.company_id = ?
                """,
                (customer_id, company_id),
            ).fetchall()

        events: list[dict[str, Any]] = []
        for row in audit_rows:
            try:
                data = json.loads(row["data_json"] or "{}")
            except (TypeError, ValueError):
                data = {}
            events.append({
                "type": "profile_updated",
                "actor_name": row["actor_name"],
                "changes": data,
                "created_at": row["created_at"],
            })
        for row in conversation_rows:
            events.append({
                "type": "conversation_started",
                "channel": row["channel"],
                "external_user_id": row["external_user_id"],
                "department": row["department"],
                "topic": row["topic"],
                "status": row["status"],
                "handled_by_name": row["handled_by_name"],
                "created_at": row["created_at"],
            })

        events.sort(key=lambda item: item["created_at"] or "", reverse=True)
        return events

    @staticmethod
    def _parse_tags(raw_tags_json: str | None) -> list[str]:
        try:
            parsed = json.loads(raw_tags_json or "[]")
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    def list_customers(
        self,
        *,
        company_id: int,
        search: str | None = None,
        lifecycle_stage: str | None = None,
        tag: str | None = None,
        assigned_user_id: int | None = None,
        segment_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if segment_id is not None:
            segment = self.get_segment(company_id=company_id, segment_id=segment_id)
            filters = segment["filters"]
        # Explicit query params always take precedence over (or add to) a segment's saved filters.
        if search is not None:
            filters["search"] = search
        if lifecycle_stage is not None:
            filters["lifecycle_stage"] = lifecycle_stage
        if tag is not None:
            filters["tag"] = tag

        where = ["c.company_id = ?"]
        params: list[Any] = [company_id]

        search_value = filters.get("search")
        if search_value and str(search_value).strip():
            pattern = f"%{str(search_value).strip()}%"
            where.append(
                "(c.display_name LIKE ? OR c.internal_name LIKE ? OR c.phone LIKE ? OR c.email LIKE ? "
                "OR EXISTS (SELECT 1 FROM customer_identities ci WHERE ci.customer_id = c.id "
                "AND (ci.external_user_id LIKE ? OR ci.username LIKE ? OR ci.display_name LIKE ?)))"
            )
            params.extend([pattern] * 7)

        stage_value = filters.get("lifecycle_stage")
        if stage_value:
            where.append("c.lifecycle_stage = ?")
            params.append(str(stage_value).strip())

        tag_value = filters.get("tag")
        if tag_value:
            where.append("c.tags_json LIKE ?")
            params.append(f'%"{str(tag_value).strip()}"%')

        if assigned_user_id is not None:
            where.append("c.assigned_user_id = ?")
            params.append(assigned_user_id)

        channel_value = filters.get("channel")
        if channel_value:
            where.append(
                "EXISTS (SELECT 1 FROM customer_identities ci WHERE ci.customer_id = c.id AND ci.channel = ?)"
            )
            params.append(str(channel_value).strip().lower())

        clause = " AND ".join(where)
        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM customers c WHERE {clause}", params
            ).fetchone()["total"]
            rows = conn.execute(
                f"""
                SELECT c.*,
                       (SELECT COUNT(*) FROM customer_identities ci WHERE ci.customer_id = c.id) AS identity_count,
                       (SELECT GROUP_CONCAT(DISTINCT ci.channel) FROM customer_identities ci WHERE ci.customer_id = c.id) AS channels_concat,
                       (SELECT COUNT(*) FROM conversations cv WHERE cv.customer_id = c.id) AS conversation_count,
                       (SELECT COALESCE(u.full_name, u.email) FROM users u WHERE u.id = c.assigned_user_id) AS assigned_user_name
                FROM customers c
                WHERE {clause}
                ORDER BY c.last_seen_at DESC, c.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(500, limit)), max(0, offset)],
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["tags"] = self._parse_tags(item.pop("tags_json", "[]"))
            channels_concat = item.pop("channels_concat", None)
            item["channels"] = sorted(channels_concat.split(",")) if channels_concat else []
            items.append(item)
        return {"items": items, "total": int(total or 0)}

    def update_customer(
        self,
        *,
        company_id: int,
        customer_id: int,
        values: dict[str, Any],
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        text_fields = {
            "display_name", "internal_name", "phone", "email",
            "language", "country", "timezone", "notes",
        }
        cleaned = {key: self._clean(value) for key, value in values.items() if key in text_fields}

        if "lifecycle_stage" in values and values["lifecycle_stage"] is not None:
            stage = str(values["lifecycle_stage"]).strip().lower()
            if stage not in LIFECYCLE_STAGES:
                raise ValueError(
                    f'"{stage}" is not a valid lifecycle stage. Choose one of: {", ".join(LIFECYCLE_STAGES)}.'
                )
            cleaned["lifecycle_stage"] = stage

        if "tags" in values and values["tags"] is not None:
            cleaned["tags_json"] = json.dumps(self._normalize_tags(values["tags"]))

        if "custom_fields" in values and values["custom_fields"] is not None:
            cleaned["custom_fields_json"] = json.dumps(
                self._normalize_custom_fields(values["custom_fields"]), ensure_ascii=False
            )

        if "documents" in values and values["documents"] is not None:
            cleaned["documents_json"] = json.dumps(
                self._normalize_documents(values["documents"]), ensure_ascii=False
            )

        assign_requested = "assigned_user_id" in values
        assigned_user_id = values.get("assigned_user_id")

        if not cleaned and not assign_requested:
            return self.get_customer(company_id=company_id, customer_id=customer_id)
        now = utc_now_iso()
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM customers WHERE id = ? AND company_id = ?",
                (customer_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Customer not found")

            if assign_requested and assigned_user_id is not None:
                is_company_employee = conn.execute(
                    "SELECT 1 FROM company_users WHERE company_id = ? AND user_id = ? AND status = 'active'",
                    (company_id, assigned_user_id),
                ).fetchone()
                if not is_company_employee:
                    raise ValueError("Assigned user must be an active employee of this company.")
            if assign_requested:
                cleaned["assigned_user_id"] = assigned_user_id

            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE customers SET {assignments}, updated_at = ? WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, customer_id, company_id],
            )
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

    # ---------------------------------------------------------------
    # Segments — saved filter combinations (lifecycle stage, tag,
    # channel, search) reused across the Contacts list and, later,
    # Broadcast/Reports. Filters are stored as opaque JSON so new
    # filter dimensions can be added without a schema migration.
    # ---------------------------------------------------------------
    _SEGMENT_FILTER_KEYS = {"search", "lifecycle_stage", "tag", "channel"}

    def _normalize_filters(self, filters: dict[str, Any] | None) -> dict[str, Any]:
        filters = filters or {}
        normalized: dict[str, Any] = {}
        for key in self._SEGMENT_FILTER_KEYS:
            value = filters.get(key)
            if value is None:
                continue
            value = str(value).strip()
            if value:
                normalized[key] = value
        if "lifecycle_stage" in normalized and normalized["lifecycle_stage"].lower() not in LIFECYCLE_STAGES:
            raise ValueError(
                f'"{normalized["lifecycle_stage"]}" is not a valid lifecycle stage. '
                f'Choose one of: {", ".join(LIFECYCLE_STAGES)}.'
            )
        return normalized

    def list_segments(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM customer_segments WHERE company_id = ? ORDER BY name",
                (company_id,),
            ).fetchall()
        return [self._segment_row_to_dict(row) for row in rows]

    def get_segment(self, *, company_id: int, segment_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM customer_segments WHERE id = ? AND company_id = ?",
                (segment_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Segment not found")
        return self._segment_row_to_dict(row)

    @staticmethod
    def _segment_row_to_dict(row: Any) -> dict[str, Any]:
        result = dict(row)
        try:
            result["filters"] = json.loads(result.pop("filters_json", "{}") or "{}")
        except (TypeError, ValueError):
            result["filters"] = {}
        return result

    def create_segment(
        self, *, company_id: int, name: str, filters: dict[str, Any] | None, actor_user_id: int | None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("Segment name is required.")
        normalized_filters = self._normalize_filters(filters)
        now = utc_now_iso()
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM customer_segments WHERE company_id = ? AND lower(name) = lower(?)",
                (company_id, name),
            ).fetchone()
            if existing:
                raise ValueError(f'A segment named "{name}" already exists.')
            cursor = conn.execute(
                """
                INSERT INTO customer_segments (
                    company_id, name, filters_json, created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (company_id, name, json.dumps(normalized_filters), actor_user_id, now, now),
            )
            segment_id = int(cursor.lastrowid)
            conn.commit()
        return self.get_segment(company_id=company_id, segment_id=segment_id)

    def delete_segment(self, *, company_id: int, segment_id: int) -> None:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM customer_segments WHERE id = ? AND company_id = ?",
                (segment_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Segment not found")


customer_service = CustomerService()
