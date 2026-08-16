"""Conversation messages and the inbox listing.

Replaces the previous per-conversation ``.jsonl`` files. Messages now live in
the company's own encrypted database, which changes three things at once:

* they are inside the company's encryption boundary instead of a shared folder,
* listing the inbox is one indexed query instead of reading every file on disk,
* the message counter on the dashboard reflects reality.

The listing query deliberately returns everything the inbox needs in a single
round trip. Employee names are the one exception: they live in the control-plane
database, so they are resolved once per page rather than once per conversation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(raw: Any, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


class MessageService:
    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def save_message(
        self,
        *,
        company_id: int,
        channel: str,
        external_user_id: str,
        direction: str,
        text: str,
        conversation_id: int | None = None,
        sender_type: str = "customer",
        sender_user_id: int | None = None,
        provider_message_id: str | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one message and advance the conversation's last-activity time.

        Returns ``{"duplicate": True, ...}`` without writing when the provider
        message id has already been stored. Meta retries webhooks, so without
        this the same customer message would appear several times in the inbox.
        """
        company_id = int(company_id)
        channel = str(channel).strip().lower()
        external_user_id = str(external_user_id).strip()
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                resolved_id = conversation_id

                if resolved_id is None:
                    row = conn.execute(
                        """
                        SELECT id FROM conversations
                        WHERE company_id = ? AND channel = ? AND external_user_id = ?
                        LIMIT 1
                        """,
                        (company_id, channel, external_user_id),
                    ).fetchone()

                    if row:
                        resolved_id = int(row["id"])
                    else:
                        cursor = conn.execute(
                            """
                            INSERT INTO conversations (
                                company_id, channel, external_user_id,
                                created_at, updated_at, last_message_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (company_id, channel, external_user_id, now, now, now),
                        )
                        resolved_id = int(cursor.lastrowid)

                if provider_message_id:
                    existing = conn.execute(
                        "SELECT id FROM messages WHERE provider_message_id = ? LIMIT 1",
                        (str(provider_message_id),),
                    ).fetchone()

                    if existing:
                        conn.rollback()
                        return {
                            "duplicate": True,
                            "id": int(existing["id"]),
                            "conversation_id": resolved_id,
                        }

                cursor = conn.execute(
                    """
                    INSERT INTO messages (
                        company_id, conversation_id, channel, external_user_id,
                        direction, sender_type, sender_user_id, body,
                        provider_message_id, source, metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        resolved_id,
                        channel,
                        external_user_id,
                        direction,
                        sender_type,
                        sender_user_id,
                        text or "",
                        str(provider_message_id) if provider_message_id else None,
                        source,
                        json.dumps(metadata or {}, ensure_ascii=False),
                        now,
                    ),
                )

                conn.execute(
                    """
                    UPDATE conversations
                    SET last_message_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, resolved_id),
                )

                conn.commit()

            except Exception:
                conn.rollback()
                raise

            return {
                "duplicate": False,
                "id": int(cursor.lastrowid),
                "conversation_id": resolved_id,
                "company_id": company_id,
                "channel": channel,
                "user_id": external_user_id,
                "direction": direction,
                "text": text or "",
                "time": now,
                "metadata": metadata or {},
            }

    # ------------------------------------------------------------------
    # Reading one conversation
    # ------------------------------------------------------------------

    def is_duplicate(self, company_id: int, provider_message_id: str | None) -> bool:
        """Whether this provider message has already been stored.

        Checked before any state is touched. Meta retries deliveries, and
        counting a retry as new inflates the unread badge for a message the
        employee has already seen.
        """
        if not provider_message_id:
            return False

        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                "SELECT 1 FROM messages WHERE provider_message_id = ? LIMIT 1",
                (str(provider_message_id),),
            ).fetchone()

        return row is not None

    def list_messages(
        self,
        *,
        company_id: int,
        channel: str,
        external_user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the most recent messages, oldest first for display."""
        company_id = int(company_id)
        limit = max(1, min(int(limit), 500))

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM messages
                WHERE company_id = ? AND channel = ? AND external_user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (
                    company_id,
                    str(channel).strip().lower(),
                    str(external_user_id).strip(),
                    limit,
                ),
            ).fetchall()

        messages = [self._public_message(row) for row in rows]
        messages.reverse()
        return messages

    def message_count(self, company_id: int) -> int:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM messages").fetchone()
        return int(row["total"] if row else 0)

    def _public_message(self, row: Any) -> dict[str, Any]:
        metadata = _loads(row["metadata_json"], {})

        return {
            "id": int(row["id"]),
            "conversation_id": int(row["conversation_id"]),
            "time": row["created_at"],
            "channel": row["channel"],
            "user_id": row["external_user_id"],
            "direction": row["direction"],
            "sender_type": row["sender_type"],
            "sender_user_id": row["sender_user_id"],
            "text": row["body"],
            "provider_message_id": row["provider_message_id"],
            "source": row["source"],
            "metadata": metadata,
        }

    # ------------------------------------------------------------------
    # Inbox listing
    # ------------------------------------------------------------------

    def list_conversations(
        self,
        *,
        company_id: int,
        search: str = "",
        channel: str = "all",
        status: str = "all",
        department: str = "all",
        assigned_user_id: int | None = None,
        folder: str = "inbox",
        tag: str = "",
        read_status: str = "all",
        current_user_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Return one page of the inbox plus the counters the sidebar needs."""
        company_id = int(company_id)
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))

        where: list[str] = ["c.company_id = ?"]
        params: list[Any] = [company_id]

        if channel and channel != "all":
            where.append("c.channel = ?")
            params.append(channel)

        if status and status != "all":
            where.append("c.status = ?")
            params.append(status)

        if department and department != "all":
            where.append("c.department = ?")
            params.append(department)

        if assigned_user_id is not None:
            where.append("c.assigned_user_id = ?")
            params.append(int(assigned_user_id))

        if folder == "starred":
            where.append("c.is_starred = 1")
        elif folder == "pinned":
            where.append("c.is_pinned = 1")
        elif folder == "unread":
            where.append("c.unread_count > 0")
        elif folder == "assigned_to_me":
            where.append("c.assigned_user_id = ?")
            params.append(int(current_user_id or 0))
        elif folder and folder != "all":
            where.append("c.folder = ?")
            params.append(folder)

        if read_status == "unread":
            where.append("c.unread_count > 0")
        elif read_status == "read":
            where.append("c.unread_count = 0")

        normalized_tag = str(tag or "").strip().lower()
        if normalized_tag:
            where.append("LOWER(c.tags_json) LIKE ?")
            params.append(f"%{normalized_tag}%")

        normalized_search = str(search or "").strip().lower()
        if normalized_search:
            pattern = f"%{normalized_search}%"
            # Searching message bodies through EXISTS keeps the scan on the
            # indexed messages table instead of loading every conversation.
            where.append(
                """
                (
                    LOWER(COALESCE(c.official_customer_name, '')) LIKE ?
                    OR LOWER(COALESCE(c.customer_alias, '')) LIKE ?
                    OR LOWER(c.external_user_id) LIKE ?
                    OR LOWER(COALESCE(c.department, '')) LIKE ?
                    OR LOWER(COALESCE(c.topic, '')) LIKE ?
                    OR EXISTS (
                        SELECT 1 FROM messages m
                        WHERE m.conversation_id = c.id
                          AND LOWER(m.body) LIKE ?
                    )
                )
                """
            )
            params.extend([pattern] * 6)

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM conversations c WHERE {clause}",
                    params,
                ).fetchone()["total"]
            )

            rows = conn.execute(
                f"""
                SELECT
                    c.*,
                    (
                        SELECT m.body FROM messages m
                        WHERE m.conversation_id = c.id
                        ORDER BY m.created_at DESC, m.id DESC LIMIT 1
                    ) AS last_message,
                    (
                        SELECT m.direction FROM messages m
                        WHERE m.conversation_id = c.id
                        ORDER BY m.created_at DESC, m.id DESC LIMIT 1
                    ) AS last_direction,
                    (
                        SELECT COUNT(*) FROM messages m
                        WHERE m.conversation_id = c.id
                    ) AS message_count
                FROM conversations c
                WHERE {clause}
                ORDER BY COALESCE(c.last_message_at, c.updated_at) DESC, c.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()

            counter_rows = conn.execute(
                """
                SELECT channel, COUNT(*) AS unread
                FROM conversations
                WHERE company_id = ? AND unread_count > 0
                GROUP BY channel
                """,
                (company_id,),
            ).fetchall()

            channel_rows = conn.execute(
                "SELECT DISTINCT channel FROM conversations WHERE company_id = ?",
                (company_id,),
            ).fetchall()

        counts: dict[str, int] = {"all": 0}
        for row in counter_rows:
            counts[str(row["channel"])] = int(row["unread"])
            counts["all"] += int(row["unread"])

        items = [self._public_conversation(row) for row in rows]

        return {
            "items": items,
            "channel_counts": counts,
            "available_channels": sorted(
                str(row["channel"]) for row in channel_rows if row["channel"]
            ),
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size),
            },
        }

    def _public_conversation(self, row: Any) -> dict[str, Any]:
        handled_by_ai = bool(row["handled_by_ai"])
        ai_enabled = bool(row["ai_enabled"])

        display_name = (
            row["official_customer_name"]
            or row["customer_alias"]
            or f"{str(row['channel']).title()} Customer"
        )

        return {
            "id": f"{row['channel']}:{row['external_user_id']}",
            "conversation_id": int(row["id"]),
            "channel": row["channel"],
            "external_user_id": row["external_user_id"],
            "customer_id": row["customer_id"],
            "customer_name": display_name,
            "customer_alias": row["customer_alias"],
            "customer_profile_picture": row["customer_profile_picture"],
            "folder": row["folder"] or "inbox",
            "is_starred": bool(row["is_starred"]),
            "is_pinned": bool(row["is_pinned"]),
            "tags": _loads(row["tags_json"], []),
            "department": row["department"] or "Unassigned",
            "topic": row["topic"] or "General",
            "status": row["status"] or "open",
            "priority": row["priority"] or "normal",
            "handled_by_ai": handled_by_ai,
            "ai_enabled": ai_enabled,
            "ai_status": "active" if (handled_by_ai and ai_enabled) else "human",
            "assigned_user_id": row["assigned_user_id"],
            "unread_count": int(row["unread_count"] or 0),
            "takeover_expires_at": row["takeover_expires_at"],
            "last_message": row["last_message"] or "",
            "last_direction": row["last_direction"] or "",
            "updated_at": row["last_message_at"] or row["updated_at"] or "",
            "message_count": int(row["message_count"] or 0),
            "branch_id": row["branch_id"],
            "channel_account_id": row["channel_account_id"],
        }

    def live_signature(self, company_id: int) -> str:
        """Cheap change-detection value for the live inbox stream.

        One aggregate query over indexed columns, so the stream can poll often
        without the full-table read the previous implementation performed.
        """
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(MAX(updated_at), '') AS latest_update,
                    COALESCE(MAX(last_message_at), '') AS latest_message,
                    COALESCE(SUM(unread_count), 0) AS unread,
                    COALESCE(SUM(is_starred), 0) AS starred,
                    COALESCE(SUM(is_pinned), 0) AS pinned,
                    COALESCE(SUM(handled_by_ai), 0) AS ai_handled,
                    COALESCE(SUM(COALESCE(assigned_user_id, 0)), 0) AS assignment_sum
                FROM conversations
                """
            ).fetchone()

        return "|".join(str(row[key]) for key in row.keys())


message_service = MessageService()
