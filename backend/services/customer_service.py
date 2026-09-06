"""Customer records and the channel identities that map onto them.

Customers, their identities and their audit trail all live in the company's own
encrypted database. `actor_user_id` on an audit row points at a user in the
control-plane database; it is stored as a plain integer and resolved through
`auth_service.user_display_names` when a name is actually needed, because SQLite
cannot join across two files.

Table creation belongs to `database/schema_tenant.py` alone. This service only
reads and writes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.services.auth_service import auth_service
from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# A fixed, ordered pipeline rather than a free-form list like Departments: a
# stage only means something if every company's "vip" sits at the same rung,
# and a board view sorts by position. Every contact starts at "lead" — the ones
# created for us by an inbound message included.
LIFECYCLE_STAGES = ["lead", "active", "customer", "vip", "churned"]
DEFAULT_LIFECYCLE_STAGE = "lead"


class CustomerService:
    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

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
        """Free-form key/value fields the employee defines per contact — an ID
        number, an insurance plan, a preferred branch. Deliberately schemaless,
        so a clinic and a delivery company can each record what they need
        without a column being added for either of them.
        """
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
    def _parse_tags(raw: str | None) -> list[str]:
        try:
            parsed = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

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
        company_id = int(company_id)
        normalized_channel = channel.strip().lower()
        normalized_external_id = external_user_id.strip()
        display_name = self._clean(display_name)
        profile_picture = self._clean(profile_picture)
        username = self._clean(username)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            # The lookup below and the insert underneath it are one decision —
            # "is this person already known, and if not, record them" — so they
            # are one transaction.
            #
            # Without this each statement was its own, and two messages arriving
            # at the same instant from the same *new* customer both found
            # nothing and both inserted. The unique index on
            # (company_id, channel, external_user_id) caught the second, which
            # kept the data right, but the `IntegrityError` came straight back
            # out here — and this runs on the inbound path, where
            # `_process_events` logs the failure and moves on. The customer's
            # message was dropped: not stored, not answered, not notified.
            # Somebody sending "hi" and then their question is the ordinary way
            # to reach that.
            #
            # `BEGIN IMMEDIATE` takes the write lock before the lookup, so the
            # second arrival waits and then finds the row the first one wrote.
            conn.execute("BEGIN IMMEDIATE")

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
                logger.info(
                    "Created customer id=%s company id=%s channel=%s",
                    customer_id,
                    company_id,
                    normalized_channel,
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
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
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
        # The channels this person can be reached on, derived from the
        # identities rather than stored twice.
        result["channels"] = sorted({item["channel"] for item in identities})
        result["conversation_count"] = int(conversation_count or 0)
        result["tags"] = self._parse_tags(result.pop("tags_json", "[]"))
        result["custom_fields"] = self._parse_json_object(
            result.pop("custom_fields_json", "{}")
        )
        result["documents"] = self._parse_json_list(result.pop("documents_json", "[]"))
        # `users` is in the control plane, so the name is a second query rather
        # than a join — the same resolution the inbox does for its own rows.
        result["assigned_user_name"] = self._user_name(
            company_id, result.get("assigned_user_id")
        )
        return result

    @staticmethod
    def _user_name(company_id: int, user_id: Any) -> str | None:
        if user_id is None:
            return None
        return auth_service.user_display_names(company_id, [int(user_id)]).get(
            int(user_id)
        )

    def create_customer(
        self,
        *,
        company_id: int,
        display_name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        """A contact entered by hand — a walk-in, a phone lead, anyone who has
        not messaged in on a connected channel yet. Unlike
        `upsert_from_channel` this writes no `customer_identities` row, because
        there is no channel identity to attach.
        """
        company_id = int(company_id)
        display_name = self._clean(display_name)
        phone = self._clean(phone)
        email = self._clean(email)

        if not display_name and not phone and not email:
            raise ValueError(
                "Provide at least a name, phone, or email to create a contact."
            )

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                INSERT INTO customers (
                    company_id, display_name, phone, email, lifecycle_stage,
                    first_seen_at, last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id, display_name, phone, email,
                    DEFAULT_LIFECYCLE_STAGE, now, now, now, now,
                ),
            )
            customer_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO customer_audit (
                    company_id, customer_id, actor_user_id, action, data_json, created_at
                ) VALUES (?, ?, ?, 'customer_created', ?, ?)
                """,
                (
                    company_id,
                    customer_id,
                    actor_user_id,
                    # Field names only, for the same reason as `update_customer`
                    # below: the values are the contact's own details and the
                    # customer record already holds them.
                    json.dumps(
                        {
                            "display_name": display_name,
                            "phone": phone,
                            "email": email,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            conn.commit()

        logger.info(
            "Created customer id=%s company id=%s by hand actor id=%s",
            customer_id,
            company_id,
            actor_user_id,
        )
        return self.get_customer(company_id=company_id, customer_id=customer_id)

    def bulk_update_customers(
        self,
        *,
        company_id: int,
        customer_ids: list[int],
        lifecycle_stage: str | None = None,
        add_tag: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        """One stage change and/or one added tag across many contacts, applied
        through `update_customer` per id so the validation lives in one place.

        An id this company does not own is skipped rather than failing the
        batch: from here it is indistinguishable from a stale id, and a
        selection made just before somebody else deleted a row should not lose
        the other forty changes.
        """
        company_id = int(company_id)
        add_tag = self._clean(add_tag)
        updated = 0

        for customer_id in customer_ids:
            try:
                existing = self.get_customer(
                    company_id=company_id, customer_id=int(customer_id)
                )
            except KeyError:
                continue

            values: dict[str, Any] = {}

            if lifecycle_stage is not None:
                values["lifecycle_stage"] = lifecycle_stage

            if add_tag:
                current_tags = existing.get("tags") or []
                values["tags"] = (
                    current_tags
                    if add_tag in current_tags
                    else [*current_tags, add_tag]
                )

            if not values:
                continue

            self.update_customer(
                company_id=company_id,
                customer_id=int(customer_id),
                values=values,
                actor_user_id=actor_user_id,
            )
            updated += 1

        return {"updated": updated}

    def get_timeline(
        self, *, company_id: int, customer_id: int
    ) -> list[dict[str, Any]]:
        """The client file's history: profile edits from `customer_audit`
        merged with every conversation this contact has had, newest first.

        Derived and read-only. Nothing new is written to keep it — it reads
        history that two other features already record.
        """
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            exists = conn.execute(
                "SELECT id FROM customers WHERE id = ? AND company_id = ?",
                (customer_id, company_id),
            ).fetchone()
            if not exists:
                raise KeyError("Customer not found")

            audit_rows = conn.execute(
                """
                SELECT action, actor_user_id, data_json, created_at
                FROM customer_audit
                WHERE customer_id = ? AND company_id = ?
                """,
                (customer_id, company_id),
            ).fetchall()

            conversation_rows = conn.execute(
                """
                SELECT channel, external_user_id, department, topic, status,
                       assigned_user_id, created_at
                FROM conversations
                WHERE customer_id = ? AND company_id = ?
                """,
                (customer_id, company_id),
            ).fetchall()

        # One control-plane query for every name on the page, not one per row.
        names = auth_service.user_display_names(
            company_id,
            [
                *(row["actor_user_id"] for row in audit_rows),
                *(row["assigned_user_id"] for row in conversation_rows),
            ],
        )

        events: list[dict[str, Any]] = []

        for row in audit_rows:
            try:
                data = json.loads(row["data_json"] or "{}")
            except (TypeError, ValueError):
                data = {}
            events.append(
                {
                    "type": "profile_updated",
                    "actor_name": names.get(row["actor_user_id"]),
                    "changes": data,
                    "created_at": row["created_at"],
                }
            )

        for row in conversation_rows:
            events.append(
                {
                    "type": "conversation_started",
                    "channel": row["channel"],
                    "external_user_id": row["external_user_id"],
                    "department": row["department"],
                    "topic": row["topic"],
                    "status": row["status"],
                    "handled_by_name": names.get(row["assigned_user_id"]),
                    "created_at": row["created_at"],
                }
            )

        events.sort(key=lambda item: item["created_at"] or "", reverse=True)
        return events

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
        company_id = int(company_id)

        # A segment supplies the starting filters; anything named explicitly in
        # the request wins over what the segment saved, so a saved segment can
        # be narrowed further without being edited.
        filters: dict[str, Any] = {}
        if segment_id is not None:
            filters = dict(
                self.get_segment(company_id=company_id, segment_id=segment_id)["filters"]
            )
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
            # Tags are stored as a JSON array; the quotes around the term keep
            # "vip" from matching a tag called "vip-2024".
            where.append("c.tags_json LIKE ?")
            params.append(f'%"{str(tag_value).strip()}"%')

        effective_assigned_user_id = (
            assigned_user_id
            if assigned_user_id is not None
            else filters.get("assigned_user_id")
        )
        if effective_assigned_user_id is not None:
            where.append("c.assigned_user_id = ?")
            params.append(int(effective_assigned_user_id))

        channel_value = filters.get("channel")
        if channel_value:
            where.append(
                "EXISTS (SELECT 1 FROM customer_identities ci "
                "WHERE ci.customer_id = c.id AND ci.channel = ?)"
            )
            params.append(str(channel_value).strip().lower())

        clause = " AND ".join(where)
        with database_manager.tenant(company_id) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM customers c WHERE {clause}", params
            ).fetchone()["total"]
            rows = conn.execute(
                f"""
                SELECT c.*,
                       (SELECT COUNT(*) FROM customer_identities ci WHERE ci.customer_id = c.id) AS identity_count,
                       (SELECT GROUP_CONCAT(DISTINCT ci.channel) FROM customer_identities ci WHERE ci.customer_id = c.id) AS channels_concat,
                       (SELECT COUNT(*) FROM conversations cv WHERE cv.customer_id = c.id) AS conversation_count
                FROM customers c
                WHERE {clause}
                ORDER BY c.last_seen_at DESC, c.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(500, limit)), max(0, offset)],
            ).fetchall()

        items = [dict(row) for row in rows]
        names = auth_service.user_display_names(
            company_id, [item.get("assigned_user_id") for item in items]
        )

        for item in items:
            item["tags"] = self._parse_tags(item.pop("tags_json", "[]"))
            channels_concat = item.pop("channels_concat", None)
            item["channels"] = (
                sorted(channels_concat.split(",")) if channels_concat else []
            )
            item["assigned_user_name"] = (
                names.get(int(item["assigned_user_id"]))
                if item.get("assigned_user_id") is not None
                else None
            )
            # The list carries the same two free-form stores as the detail read,
            # so a row does not arrive holding raw JSON under a `_json` name.
            item["custom_fields"] = self._parse_json_object(
                item.pop("custom_fields_json", "{}")
            )
            item["documents"] = self._parse_json_list(item.pop("documents_json", "[]"))

        return {"items": items, "total": int(total or 0)}

    def update_customer(
        self,
        *,
        company_id: int,
        customer_id: int,
        values: dict[str, Any],
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        allowed = {
            "display_name", "internal_name", "phone", "email",
            "language", "country", "timezone", "notes",
        }
        cleaned = {key: self._clean(value) for key, value in values.items() if key in allowed}

        if values.get("lifecycle_stage") is not None:
            stage = str(values["lifecycle_stage"]).strip().lower()
            if stage not in LIFECYCLE_STAGES:
                raise ValueError(
                    f'"{stage}" is not a valid lifecycle stage. '
                    f'Choose one of: {", ".join(LIFECYCLE_STAGES)}.'
                )
            cleaned["lifecycle_stage"] = stage

        if values.get("tags") is not None:
            cleaned["tags_json"] = json.dumps(
                self._normalize_tags(values["tags"]), ensure_ascii=False
            )

        if values.get("custom_fields") is not None:
            cleaned["custom_fields_json"] = json.dumps(
                self._normalize_custom_fields(values["custom_fields"]), ensure_ascii=False
            )

        if values.get("documents") is not None:
            cleaned["documents_json"] = json.dumps(
                self._normalize_documents(values["documents"]), ensure_ascii=False
            )

        # Kept apart from `cleaned` until the employee has been checked: an
        # explicit `null` means "unassign", which is a value, not an absence,
        # and `if not cleaned` must not treat it as nothing to do.
        assign_requested = "assigned_user_id" in values
        assigned_user_id = values.get("assigned_user_id")

        if assign_requested and assigned_user_id is not None:
            # `company_users` lives in the control plane, so this is a lookup
            # rather than a foreign key. Without it a contact could be assigned
            # to another company's employee — an id from outside this tenant
            # that nothing in the tenant database could refuse.
            employee_ids = {
                int(employee["id"])
                for employee in auth_service.company_employees(company_id)
            }
            if int(assigned_user_id) not in employee_ids:
                raise ValueError(
                    "Assigned user must be an active employee of this company."
                )
            cleaned["assigned_user_id"] = int(assigned_user_id)
        elif assign_requested:
            cleaned["assigned_user_id"] = None

        if not cleaned:
            return self.get_customer(company_id=company_id, customer_id=customer_id)
        now = utc_now_iso()
        assignments = ", ".join(f"{key} = ?" for key in cleaned)
        with database_manager.tenant(company_id) as conn:
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
            conn.execute(
                """
                INSERT INTO customer_audit (
                    company_id, customer_id, actor_user_id, action, data_json, created_at
                ) VALUES (?, ?, ?, 'customer_updated', ?, ?)
                """,
                (company_id, customer_id, actor_user_id, json.dumps(cleaned, ensure_ascii=False), now),
            )
            conn.commit()

        # Field names only. The values are customer contact details and never
        # belong in a log line.
        logger.info(
            "Updated customer id=%s company id=%s fields=%s actor id=%s",
            customer_id,
            company_id,
            sorted(cleaned),
            actor_user_id,
        )
        return self.get_customer(company_id=company_id, customer_id=customer_id)

    # ------------------------------------------------------------------
    # Segments — a saved combination of the Contacts filters (search,
    # lifecycle stage, tag, channel, owner), so a list somebody rebuilds
    # every morning is rebuilt once. The filters are stored as opaque JSON
    # rather than as columns: a new filter dimension then costs nothing at
    # the schema level.
    # ------------------------------------------------------------------
    _SEGMENT_FILTER_KEYS = {
        "search", "lifecycle_stage", "tag", "channel", "assigned_user_id",
    }

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

        if (
            "lifecycle_stage" in normalized
            and normalized["lifecycle_stage"].lower() not in LIFECYCLE_STAGES
        ):
            raise ValueError(
                f'"{normalized["lifecycle_stage"]}" is not a valid lifecycle stage. '
                f'Choose one of: {", ".join(LIFECYCLE_STAGES)}.'
            )

        return normalized

    @staticmethod
    def _segment_row_to_dict(row: Any) -> dict[str, Any]:
        result = dict(row)
        try:
            result["filters"] = json.loads(result.pop("filters_json", "{}") or "{}")
        except (TypeError, ValueError):
            result["filters"] = {}
        return result

    def list_segments(self, *, company_id: int) -> list[dict[str, Any]]:
        company_id = int(company_id)
        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                "SELECT * FROM customer_segments WHERE company_id = ? ORDER BY name",
                (company_id,),
            ).fetchall()
        return [self._segment_row_to_dict(row) for row in rows]

    def get_segment(self, *, company_id: int, segment_id: int) -> dict[str, Any]:
        company_id = int(company_id)
        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT * FROM customer_segments WHERE id = ? AND company_id = ?",
                (segment_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Segment not found")
        return self._segment_row_to_dict(row)

    def create_segment(
        self,
        *,
        company_id: int,
        name: str,
        filters: dict[str, Any] | None,
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        name = (name or "").strip()
        if not name:
            raise ValueError("Segment name is required.")

        normalized_filters = self._normalize_filters(filters)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
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
                (
                    company_id,
                    name,
                    json.dumps(normalized_filters, ensure_ascii=False),
                    actor_user_id,
                    now,
                    now,
                ),
            )
            segment_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_segment(company_id=company_id, segment_id=segment_id)

    def delete_segment(self, *, company_id: int, segment_id: int) -> None:
        company_id = int(company_id)
        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                "DELETE FROM customer_segments WHERE id = ? AND company_id = ?",
                (segment_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Segment not found")


customer_service = CustomerService()
