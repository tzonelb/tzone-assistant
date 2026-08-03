from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TeamChatRoomsService:
    """DMs and named groups, additive to team_chat_service.py's existing
    single flat company-wide stream (team_messages) — that table and its
    routes are untouched. A room is either kind='dm' (exactly 2 members,
    reused rather than duplicated on repeat requests between the same pair)
    or kind='group' (named, membership fixed at creation — either an
    explicit list of employees or a snapshot of everyone currently in a
    department; department membership is NOT tracked live, matching how
    the rest of this codebase treats department scoping as a point-in-time
    assignment, not a dynamic query)."""

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS team_chat_rooms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('dm', 'group')),
                    name TEXT,
                    created_by_user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS team_chat_room_members (
                    room_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY(room_id, user_id),
                    FOREIGN KEY(room_id) REFERENCES team_chat_rooms(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS team_room_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id INTEGER NOT NULL,
                    sender_user_id INTEGER NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    mentioned_user_ids_json TEXT NOT NULL DEFAULT '[]',
                    attachment_url TEXT,
                    attachment_type TEXT,
                    attachment_filename TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(room_id) REFERENCES team_chat_rooms(id) ON DELETE CASCADE,
                    FOREIGN KEY(sender_user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.commit()

    def _member_display(self, conn, *, company_id: int, user_id: int) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id, full_name, email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return {"id": user_id, "display_name": f"User {user_id}"}
        return {
            "id": row["id"],
            "display_name": row["full_name"] or row["email"] or f"User {user_id}",
        }

    def _validate_company_users(self, conn, *, company_id: int, user_ids: list[int]) -> list[int]:
        if not user_ids:
            return []
        placeholders = ",".join("?" for _ in user_ids)
        rows = conn.execute(
            f"""
            SELECT users.id FROM users
            JOIN company_users ON company_users.user_id = users.id
            WHERE company_users.company_id = ? AND company_users.status = 'active'
              AND users.id IN ({placeholders})
            """,
            (company_id, *user_ids),
        ).fetchall()
        return [int(row["id"]) for row in rows]

    def get_or_create_dm(self, *, company_id: int, user_a: int, user_b: int) -> dict[str, Any]:
        if user_a == user_b:
            raise ValueError("Cannot start a direct message with yourself.")

        with db.connect() as conn:
            valid = set(self._validate_company_users(conn, company_id=company_id, user_ids=[user_a, user_b]))
            if user_a not in valid or user_b not in valid:
                raise ValueError("Both people must be active employees of this company.")

            existing = conn.execute(
                """
                SELECT trm.room_id
                FROM team_chat_room_members trm
                JOIN team_chat_rooms tcr ON tcr.id = trm.room_id
                WHERE tcr.company_id = ? AND tcr.kind = 'dm'
                  AND trm.room_id IN (
                      SELECT room_id FROM team_chat_room_members WHERE user_id = ?
                  )
                  AND trm.user_id = ?
                """,
                (company_id, user_a, user_b),
            ).fetchone()
            if existing:
                return self.get_room(company_id=company_id, room_id=existing["room_id"], viewer_user_id=user_a)

            now = utc_now_iso()
            cursor = conn.execute(
                "INSERT INTO team_chat_rooms (company_id, kind, name, created_by_user_id, created_at) VALUES (?, 'dm', NULL, ?, ?)",
                (company_id, user_a, now),
            )
            room_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO team_chat_room_members (room_id, user_id, joined_at) VALUES (?, ?, ?), (?, ?, ?)",
                (room_id, user_a, now, room_id, user_b, now),
            )
            conn.commit()
            return self.get_room(company_id=company_id, room_id=room_id, viewer_user_id=user_a)

    def create_group(
        self, *, company_id: int, created_by_user_id: int, name: str,
        member_user_ids: list[int] | None = None, department: str | None = None,
    ) -> dict[str, Any]:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Group name is required.")

        with db.connect() as conn:
            members: set[int] = {created_by_user_id}

            if department:
                # Strict membership on purpose — unlike _company_employees()'s
                # picker semantics (which also surface unscoped staff so admins
                # can still hand-pick them), auto-adding someone to a persistent
                # group just because they haven't been assigned a department
                # yet would be a real, sticky mistake, not a harmless default.
                department_rows = conn.execute(
                    "SELECT user_id, departments_json FROM company_users WHERE company_id = ? AND status = 'active'",
                    (company_id,),
                ).fetchall()
                for row in department_rows:
                    try:
                        departments = json.loads(row["departments_json"] or "[]")
                    except (TypeError, ValueError):
                        departments = []
                    if department in departments:
                        members.add(int(row["user_id"]))
            if member_user_ids:
                members.update(self._validate_company_users(conn, company_id=company_id, user_ids=member_user_ids))

            if len(members) < 2:
                raise ValueError("Pick at least one other employee, or a department with members, for this group.")

            now = utc_now_iso()
            cursor = conn.execute(
                "INSERT INTO team_chat_rooms (company_id, kind, name, created_by_user_id, created_at) VALUES (?, 'group', ?, ?, ?)",
                (company_id, clean_name, created_by_user_id, now),
            )
            room_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO team_chat_room_members (room_id, user_id, joined_at) VALUES (?, ?, ?)",
                [(room_id, user_id, now) for user_id in members],
            )
            conn.commit()
            return self.get_room(company_id=company_id, room_id=room_id, viewer_user_id=created_by_user_id)

    def get_room(self, *, company_id: int, room_id: int, viewer_user_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            room_row = conn.execute(
                "SELECT * FROM team_chat_rooms WHERE id = ? AND company_id = ?",
                (room_id, company_id),
            ).fetchone()
            if not room_row:
                raise KeyError("Room not found")

            member_rows = conn.execute(
                "SELECT user_id FROM team_chat_room_members WHERE room_id = ?",
                (room_id,),
            ).fetchall()
            members = [self._member_display(conn, company_id=company_id, user_id=row["user_id"]) for row in member_rows]

        room = dict(room_row)
        room["members"] = members
        if room["kind"] == "dm":
            other = next((member for member in members if member["id"] != viewer_user_id), None)
            room["display_name"] = other["display_name"] if other else "Direct message"
        else:
            room["display_name"] = room["name"]
        return room

    def list_rooms_for_user(self, *, company_id: int, user_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            room_ids = [
                row["room_id"] for row in conn.execute(
                    """
                    SELECT trm.room_id FROM team_chat_room_members trm
                    JOIN team_chat_rooms tcr ON tcr.id = trm.room_id
                    WHERE trm.user_id = ? AND tcr.company_id = ?
                    """,
                    (user_id, company_id),
                ).fetchall()
            ]
        rooms = [self.get_room(company_id=company_id, room_id=room_id, viewer_user_id=user_id) for room_id in room_ids]
        rooms.sort(key=lambda room: room["created_at"], reverse=True)
        return rooms

    def _assert_member(self, conn, *, room_id: int, user_id: int) -> None:
        row = conn.execute(
            "SELECT 1 FROM team_chat_room_members WHERE room_id = ? AND user_id = ?",
            (room_id, user_id),
        ).fetchone()
        if not row:
            raise PermissionError("You are not a member of this room.")

    def send_room_message(
        self, *, company_id: int, room_id: int, sender_user_id: int, text: str,
        mentioned_user_ids: list[int] | None = None,
        attachment_url: str | None = None, attachment_type: str | None = None, attachment_filename: str | None = None,
    ) -> dict[str, Any]:
        clean_text = (text or "").strip()
        if not clean_text and not attachment_url:
            raise ValueError("Message text cannot be empty.")
        if len(clean_text) > 4000:
            raise ValueError("Message is too long (max 4000 characters).")

        with db.connect() as conn:
            room_row = conn.execute(
                "SELECT id FROM team_chat_rooms WHERE id = ? AND company_id = ?", (room_id, company_id),
            ).fetchone()
            if not room_row:
                raise KeyError("Room not found")
            self._assert_member(conn, room_id=room_id, user_id=sender_user_id)

            valid_mentions = []
            if mentioned_user_ids:
                placeholders = ",".join("?" for _ in mentioned_user_ids)
                valid_mentions = [
                    int(row["id"]) for row in conn.execute(
                        f"SELECT user_id AS id FROM team_chat_room_members WHERE room_id = ? AND user_id IN ({placeholders})",
                        (room_id, *mentioned_user_ids),
                    ).fetchall()
                ]

            now = utc_now_iso()
            cursor = conn.execute(
                """
                INSERT INTO team_room_messages (
                    room_id, sender_user_id, text, mentioned_user_ids_json,
                    attachment_url, attachment_type, attachment_filename, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (room_id, sender_user_id, clean_text, json.dumps(valid_mentions),
                 attachment_url, attachment_type, attachment_filename, now),
            )
            message_id = int(cursor.lastrowid)
            conn.commit()
        return self.get_room_message(company_id=company_id, room_id=room_id, message_id=message_id)

    def _row_to_message(self, row) -> dict[str, Any]:
        item = dict(row)
        item["mentioned_user_ids"] = json.loads(item.pop("mentioned_user_ids_json") or "[]")
        return item

    def get_room_message(self, *, company_id: int, room_id: int, message_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT trm.*, COALESCE(u.full_name, u.email) AS sender_name
                FROM team_room_messages trm
                JOIN users u ON u.id = trm.sender_user_id
                JOIN team_chat_rooms tcr ON tcr.id = trm.room_id
                WHERE trm.id = ? AND trm.room_id = ? AND tcr.company_id = ?
                """,
                (message_id, room_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Message not found")
        return self._row_to_message(row)

    def list_room_messages(self, *, company_id: int, room_id: int, viewer_user_id: int, limit: int = 100) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        with db.connect() as conn:
            room_row = conn.execute(
                "SELECT id FROM team_chat_rooms WHERE id = ? AND company_id = ?", (room_id, company_id),
            ).fetchone()
            if not room_row:
                raise KeyError("Room not found")
            self._assert_member(conn, room_id=room_id, user_id=viewer_user_id)

            rows = conn.execute(
                """
                SELECT trm.*, COALESCE(u.full_name, u.email) AS sender_name
                FROM team_room_messages trm
                JOIN users u ON u.id = trm.sender_user_id
                WHERE trm.room_id = ?
                ORDER BY trm.id DESC
                LIMIT ?
                """,
                (room_id, limit),
            ).fetchall()
        items = [self._row_to_message(row) for row in rows]
        items.reverse()
        return {"items": items, "total": len(items)}

    def delete_room_message(self, *, company_id: int, room_id: int, message_id: int, actor_user_id: int) -> None:
        with db.connect() as conn:
            room_row = conn.execute(
                "SELECT id FROM team_chat_rooms WHERE id = ? AND company_id = ?", (room_id, company_id),
            ).fetchone()
            if not room_row:
                raise KeyError("Room not found")
            row = conn.execute(
                "SELECT sender_user_id FROM team_room_messages WHERE id = ? AND room_id = ?",
                (message_id, room_id),
            ).fetchone()
            if not row:
                raise KeyError("Message not found")
            if int(row["sender_user_id"]) != int(actor_user_id):
                raise PermissionError("You can only delete your own messages.")
            conn.execute("DELETE FROM team_room_messages WHERE id = ? AND room_id = ?", (message_id, room_id))
            conn.commit()


team_chat_rooms_service = TeamChatRoomsService()
