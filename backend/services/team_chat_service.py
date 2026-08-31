"""Internal team chat: channels, messages, membership and mentions.

This is staff-only discussion. It is stored inside the owning company's
encrypted database like every other record, and it is read through a single
visibility rule rather than a filter applied at the edges:

    a private channel does not exist for anyone who is not a member.

Every read and every write resolves the channel through
``_visible_channel``, which returns nothing for a non-member of a private
channel. Callers therefore cannot list it, open it, read its messages, post to
it, invite themselves into it, or learn that it exists at all — a 404 rather
than a 403, because "you may not read this channel about you" is itself the
leak. Mentions obey the same rule: a colleague who is not in the channel is
never notified, because the notification carries the message text.

Table creation belongs to ``database/schema_tenant.py`` alone.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from backend.services.auth_service import auth_service
from backend.services.notification_service import notification_service
from backend.services.ownership import assert_employees
from database.manager import database_manager


logger = logging.getLogger(__name__)


MAX_CHANNEL_NAME = 60
MAX_TOPIC = 300
MAX_BODY = 8000
DEFAULT_PAGE = 50
MAX_PAGE = 200


# The three shapes a `team_channels` row can take. They differ only in how a
# client titles them: a `channel` is the named discussion anyone in the company
# can join, a `dm` is the two-person conversation between a fixed pair, and a
# `group` is a private discussion with a member list chosen when it was made.
# Membership and the privacy rule at the top of this file apply identically to
# all three, which is why one table holds them.
KIND_CHANNEL = "channel"
KIND_DM = "dm"
KIND_GROUP = "group"

# The company-wide stream every employee shares. It is an ordinary public
# channel — created on first use rather than at provisioning, so a company that
# never opens team chat never gets a row it did not ask for.
COMPANY_STREAM_NAME = "general"


def dm_channel_name(user_a: int, user_b: int) -> str:
    """The one name a pair's direct message can have.

    Derived from the two ids in a fixed order, so the second person to open the
    conversation lands in the first person's, and `UNIQUE(company_id, name)`
    makes a duplicate impossible even if two requests race.
    """
    low, high = sorted((int(user_a), int(user_b)))
    return f"dm-{low}-{high}"


# ``@`` followed by up to three words. The longest run that resolves to an
# employee wins, so both "@sara.nasr" and "@Sara Nasr" reach the same person.
MENTION_PATTERN = re.compile(r"@([\w.\-']+(?:[ \t][\w.\-']+){0,2})", re.UNICODE)

_ALIAS_TRIM = " .,;:!?'\"()[]{}"


class TeamChatError(Exception):
    """Base class for team chat failures the API turns into a response."""


class ChannelNotFound(TeamChatError):
    """Raised for a missing channel and for a private one the caller may not see.

    Deliberately the same failure for both: telling a non-member that a private
    channel exists already discloses that colleagues are talking somewhere they
    were not invited to.
    """


class ChannelNameTaken(TeamChatError):
    """A company already has a channel with this name."""


class NotChannelMember(TeamChatError):
    """The caller must join this (public) channel before acting."""


class NotMessageAuthor(TeamChatError):
    """Only the author may edit their own message."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_channel_name(name: str) -> str:
    """Channel names are compared as the team types them, minus the noise."""
    cleaned = re.sub(r"\s+", "-", str(name or "").strip().lower())
    cleaned = re.sub(r"[^\w\-]", "", cleaned).strip("-")
    return cleaned[:MAX_CHANNEL_NAME]


def build_mention_aliases(employees: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Map every unambiguous way of writing a colleague's name to their id.

    An alias that two employees answer to is dropped rather than guessed:
    delivering a message about a customer to the wrong "@sara" is the same
    disclosure problem as a private channel leaking.
    """
    candidates: dict[str, set[int]] = {}

    def add(alias: str, user_id: int) -> None:
        key = str(alias or "").strip().lower()
        if len(key) < 2:
            return
        candidates.setdefault(key, set()).add(user_id)

    for employee in employees:
        try:
            user_id = int(employee["id"])
        except (KeyError, TypeError, ValueError):
            continue

        names = {
            str(employee.get("display_name") or "").strip(),
            str(employee.get("full_name") or "").strip(),
        }
        email = str(employee.get("email") or "").strip()

        for name in names:
            if not name:
                continue
            add(name, user_id)
            for separator in ("", ".", "_", "-"):
                add(name.replace(" ", separator), user_id)
            add(name.split()[0], user_id)

        if "@" in email:
            add(email.split("@", 1)[0], user_id)

    return {
        alias: next(iter(user_ids))
        for alias, user_ids in candidates.items()
        if len(user_ids) == 1
    }


def extract_mentions(
    body: str, employees: Iterable[dict[str, Any]]
) -> list[int]:
    """Resolve ``@name`` tokens in a message to employee ids, in order."""
    aliases = build_mention_aliases(employees)
    resolved: list[int] = []

    for match in MENTION_PATTERN.finditer(str(body or "")):
        words = match.group(1).split()

        for size in range(len(words), 0, -1):
            alias = " ".join(words[:size]).strip(_ALIAS_TRIM).lower()
            user_id = aliases.get(alias)

            if user_id is not None:
                if user_id not in resolved:
                    resolved.append(user_id)
                break

    return resolved


class TeamChatService:
    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    @staticmethod
    def _visible_channel(
        conn: Any, *, company_id: int, channel_id: int, user_id: int
    ) -> Any | None:
        """The one place channel access is decided.

        Returns ``None`` when the channel belongs to another company, does not
        exist, or is private and the caller is not a member. Every public method
        below goes through this before touching a message.
        """
        row = conn.execute(
            """
            SELECT
                c.*,
                (m.user_id IS NOT NULL) AS is_member,
                m.last_read_at AS last_read_at,
                m.joined_at AS joined_at
            FROM team_channels c
            LEFT JOIN team_channel_members m
                ON m.channel_id = c.id AND m.user_id = ?
            WHERE c.id = ? AND c.company_id = ?
            LIMIT 1
            """,
            (int(user_id), int(channel_id), int(company_id)),
        ).fetchone()

        if not row:
            return None

        if int(row["is_private"]) and not int(row["is_member"]):
            return None

        return row

    def _require_channel(
        self, conn: Any, *, company_id: int, channel_id: int, user_id: int
    ) -> Any:
        row = self._visible_channel(
            conn, company_id=company_id, channel_id=channel_id, user_id=user_id
        )

        if row is None:
            raise ChannelNotFound("Channel not found.")

        return row

    @staticmethod
    def _channel_dict(row: Any) -> dict[str, Any]:
        channel = dict(row)
        channel["is_private"] = bool(channel.get("is_private"))
        channel["is_member"] = bool(channel.get("is_member"))
        channel["unread_count"] = int(channel.get("unread_count") or 0)
        channel["member_count"] = int(channel.get("member_count") or 0)
        channel["kind"] = str(channel.get("kind") or KIND_CHANNEL)
        # Older rows have no `display_name`; the normalised name is what they
        # were always shown under, so it stays the label rather than a blank.
        channel["display_name"] = channel.get("display_name") or channel.get("name")
        channel.pop("joined_at", None)
        return channel

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    def create_channel(
        self,
        *,
        company_id: int,
        user_id: int,
        name: str,
        topic: str | None = None,
        is_private: bool = False,
        member_user_ids: Iterable[int] | None = None,
        kind: str = KIND_CHANNEL,
        display_name: str | None = None,
        stored_name: str | None = None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        user_id = int(user_id)
        # `stored_name` is for the two callers that own the key themselves: a
        # direct message, whose name is derived from the pair, and a group,
        # whose typed name may already be taken by another group. Everything
        # else keys on the name a person typed.
        clean_name = normalize_channel_name(stored_name or name)

        if not clean_name:
            raise ValueError("Give the channel a name.")

        label = str(display_name or name).strip()[:MAX_CHANNEL_NAME] or clean_name
        now = utc_now_iso()
        clean_topic = (str(topic).strip()[:MAX_TOPIC] if topic else None)

        # Before the transaction opens, not inside it: this reads the control
        # database, and a read issued from inside an open write transaction is
        # how the platform once stalled for fifteen seconds (see
        # `DatabaseManager.after_release`).
        invitees = set(assert_employees(company_id, member_user_ids or []))

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                existing = conn.execute(
                    """
                    SELECT id FROM team_channels
                    WHERE company_id = ? AND name = ?
                    LIMIT 1
                    """,
                    (company_id, clean_name),
                ).fetchone()

                if existing:
                    conn.rollback()
                    raise ChannelNameTaken(
                        f"A channel named '{clean_name}' already exists."
                    )

                cursor = conn.execute(
                    """
                    INSERT INTO team_channels (
                        company_id, name, display_name, kind, topic, is_private,
                        created_by_user_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        clean_name,
                        label,
                        str(kind or KIND_CHANNEL),
                        clean_topic,
                        1 if is_private else 0,
                        user_id,
                        now,
                        now,
                    ),
                )
                channel_id = int(cursor.lastrowid)

                # The creator is always a member. A private channel with no
                # members would be unreachable even by the person who made it.
                #
                # The invitees are checked against this company before they are
                # inserted. `add_member` had this check from the start, with a
                # comment saying a caller could otherwise name any user id on
                # the platform; this door takes the same list and did not, so a
                # channel could be created with an employee of another company
                # in it. Same list, same check, one definition — see
                # `services/ownership.py` for why it is not written inline.
                members = {user_id, *invitees}

                for member_id in sorted(members):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO team_channel_members (
                            company_id, channel_id, user_id, last_read_at, joined_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (company_id, channel_id, member_id, now, now),
                    )

                conn.commit()

            except ChannelNameTaken:
                raise
            except Exception:
                conn.rollback()
                raise

        logger.info(
            "Team channel created id=%s company id=%s private=%s by user id=%s",
            channel_id,
            company_id,
            bool(is_private),
            user_id,
        )

        return self.get_channel(
            company_id=company_id, user_id=user_id, channel_id=channel_id
        )

    def list_channels(self, *, company_id: int, user_id: int) -> list[dict[str, Any]]:
        """Channels this user may see: every public one, plus their private ones."""
        company_id = int(company_id)
        user_id = int(user_id)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT
                    c.*,
                    (m.user_id IS NOT NULL) AS is_member,
                    m.last_read_at AS last_read_at,
                    (
                        SELECT COUNT(*) FROM team_channel_members mm
                        WHERE mm.channel_id = c.id
                    ) AS member_count,
                    (
                        SELECT COUNT(*) FROM team_messages t
                        WHERE t.channel_id = c.id
                    ) AS message_count,
                    (
                        SELECT MAX(t.created_at) FROM team_messages t
                        WHERE t.channel_id = c.id
                    ) AS last_message_at,
                    CASE WHEN m.user_id IS NULL THEN 0 ELSE (
                        SELECT COUNT(*) FROM team_messages t
                        WHERE t.channel_id = c.id
                          AND t.author_user_id != ?
                          AND (
                              m.last_read_at IS NULL
                              OR t.created_at > m.last_read_at
                          )
                    ) END AS unread_count
                FROM team_channels c
                LEFT JOIN team_channel_members m
                    ON m.channel_id = c.id AND m.user_id = ?
                WHERE c.company_id = ?
                  AND (c.is_private = 0 OR m.user_id IS NOT NULL)
                ORDER BY c.name ASC
                """,
                (user_id, user_id, company_id),
            ).fetchall()

        return [self._channel_dict(row) for row in rows]

    def get_channel(
        self, *, company_id: int, user_id: int, channel_id: int
    ) -> dict[str, Any]:
        company_id = int(company_id)
        user_id = int(user_id)

        with database_manager.tenant(company_id) as conn:
            row = self._require_channel(
                conn, company_id=company_id, channel_id=channel_id, user_id=user_id
            )

            member_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM team_channel_members WHERE channel_id = ?",
                    (int(channel_id),),
                ).fetchone()["n"]
            )
            unread = self._unread_for_channel(
                conn,
                channel_id=int(channel_id),
                user_id=user_id,
                is_member=bool(row["is_member"]),
                last_read_at=row["last_read_at"],
            )

        channel = self._channel_dict(row)
        channel["member_count"] = member_count
        channel["unread_count"] = unread
        return channel

    def list_members(
        self, *, company_id: int, user_id: int, channel_id: int
    ) -> list[dict[str, Any]]:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            self._require_channel(
                conn,
                company_id=company_id,
                channel_id=channel_id,
                user_id=int(user_id),
            )
            rows = conn.execute(
                """
                SELECT user_id, joined_at, last_read_at
                FROM team_channel_members
                WHERE channel_id = ? AND company_id = ?
                ORDER BY joined_at ASC, id ASC
                """,
                (int(channel_id), company_id),
            ).fetchall()

        return [dict(row) for row in rows]

    def is_member(self, *, company_id: int, channel_id: int, user_id: int) -> bool:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM team_channel_members
                WHERE channel_id = ? AND user_id = ? AND company_id = ?
                LIMIT 1
                """,
                (int(channel_id), int(user_id), int(company_id)),
            ).fetchone()

        return row is not None

    def join_channel(
        self, *, company_id: int, user_id: int, channel_id: int
    ) -> dict[str, Any]:
        """Join a public channel.

        A private channel is invisible here, so this raises ``ChannelNotFound``
        for a non-member: nobody adds themselves to a private conversation.
        """
        company_id = int(company_id)
        user_id = int(user_id)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            self._require_channel(
                conn, company_id=company_id, channel_id=channel_id, user_id=user_id
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO team_channel_members (
                    company_id, channel_id, user_id, last_read_at, joined_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (company_id, int(channel_id), user_id, now, now),
            )
            conn.commit()

        return self.get_channel(
            company_id=company_id, user_id=user_id, channel_id=channel_id
        )

    def add_member(
        self, *, company_id: int, actor_user_id: int, channel_id: int, user_id: int
    ) -> dict[str, Any]:
        """Invite a colleague. Only a member of the channel may do this."""
        company_id = int(company_id)
        actor_user_id = int(actor_user_id)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            row = self._require_channel(
                conn,
                company_id=company_id,
                channel_id=channel_id,
                user_id=actor_user_id,
            )

            if not int(row["is_member"]):
                raise NotChannelMember(
                    "Join the channel before inviting other people to it."
                )

            conn.execute(
                """
                INSERT OR IGNORE INTO team_channel_members (
                    company_id, channel_id, user_id, last_read_at, joined_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (company_id, int(channel_id), int(user_id), now, now),
            )
            conn.commit()

        return self.get_channel(
            company_id=company_id, user_id=actor_user_id, channel_id=channel_id
        )

    def leave_channel(
        self, *, company_id: int, user_id: int, channel_id: int
    ) -> bool:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            # Resolved before the delete, because leaving a private channel is
            # the one action that removes the caller's own visibility.
            self._require_channel(
                conn,
                company_id=company_id,
                channel_id=channel_id,
                user_id=int(user_id),
            )
            cursor = conn.execute(
                """
                DELETE FROM team_channel_members
                WHERE channel_id = ? AND user_id = ? AND company_id = ?
                """,
                (int(channel_id), int(user_id), company_id),
            )
            conn.commit()

        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    @staticmethod
    def _message_dict(row: Any) -> dict[str, Any]:
        message = dict(row)
        try:
            message["mentions"] = json.loads(message.pop("mentions_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            message["mentions"] = []
            message.pop("mentions_json", None)
        return message

    def post_message(
        self,
        *,
        company_id: int,
        user_id: int,
        channel_id: int,
        body: str,
        linked_conversation_id: int | None = None,
        employees: Iterable[dict[str, Any]] | None = None,
        author_name: str | None = None,
        mentioned_user_ids: Iterable[int] | None = None,
        attachment_url: str | None = None,
        attachment_type: str | None = None,
        attachment_filename: str | None = None,
    ) -> dict[str, Any]:
        """Store one message and notify the people it mentions."""
        company_id = int(company_id)
        user_id = int(user_id)
        text = str(body or "").strip()[:MAX_BODY]

        # A message is empty only when it carries neither words nor a file. A
        # photo with no caption is a message; refusing it would be refusing the
        # attachment button its own composer offers.
        if not text and not attachment_url:
            raise ValueError("Write something before sending.")

        directory = list(employees) if employees is not None else self._employees(company_id)
        mentioned = extract_mentions(text, directory)

        # The composer resolves each `@name` to an id as it is picked, which is
        # the only way a name two people answer to reaches the right one. Those
        # ids are checked against the directory this company actually has, so
        # an id typed into the payload by hand names nobody, and then merged
        # with what the text itself resolves to — a mention that survived a
        # rename still counts.
        if mentioned_user_ids:
            known = {int(employee["id"]) for employee in directory}
            for value in mentioned_user_ids:
                try:
                    candidate = int(value)
                except (TypeError, ValueError):
                    continue

                if candidate in known and candidate not in mentioned:
                    mentioned.append(candidate)

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            channel = self._require_channel(
                conn, company_id=company_id, channel_id=channel_id, user_id=user_id
            )

            if not int(channel["is_member"]):
                if int(channel["is_private"]):
                    # Unreachable: a non-member cannot see a private channel.
                    raise ChannelNotFound("Channel not found.")

                conn.execute(
                    """
                    INSERT OR IGNORE INTO team_channel_members (
                        company_id, channel_id, user_id, last_read_at, joined_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (company_id, int(channel_id), user_id, now, now),
                )

            if int(channel["is_private"]):
                mentioned = self._members_only(
                    conn, channel_id=int(channel_id), user_ids=mentioned
                )

            cursor = conn.execute(
                """
                INSERT INTO team_messages (
                    company_id, channel_id, author_user_id, body,
                    mentions_json, linked_conversation_id,
                    attachment_url, attachment_type, attachment_filename,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    int(channel_id),
                    user_id,
                    text,
                    json.dumps(mentioned),
                    linked_conversation_id,
                    attachment_url,
                    attachment_type,
                    attachment_filename,
                    now,
                ),
            )
            message_id = int(cursor.lastrowid)

            conn.execute(
                """
                UPDATE team_channels SET updated_at = ? WHERE id = ? AND company_id = ?
                """,
                (now, int(channel_id), company_id),
            )
            # The author has, by definition, read their own message.
            conn.execute(
                """
                UPDATE team_channel_members SET last_read_at = ?
                WHERE channel_id = ? AND user_id = ? AND company_id = ?
                """,
                (now, int(channel_id), user_id, company_id),
            )
            conn.commit()

            row = conn.execute(
                "SELECT * FROM team_messages WHERE id = ?", (message_id,)
            ).fetchone()

        self._notify_mentions(
            company_id=company_id,
            channel_name=str(self._channel_dict(channel)["display_name"]),
            channel_id=int(channel_id),
            message_id=message_id,
            body=text or (attachment_filename or "Attachment"),
            author_user_id=user_id,
            author_name=author_name,
            mentioned_user_ids=mentioned,
            directory=directory,
        )

        return self._message_dict(row)

    def list_messages(
        self,
        *,
        company_id: int,
        user_id: int,
        channel_id: int,
        limit: int = DEFAULT_PAGE,
        before_id: int | None = None,
    ) -> dict[str, Any]:
        """One page of a channel, oldest first, newest page by default."""
        company_id = int(company_id)
        user_id = int(user_id)
        limit = max(1, min(int(limit), MAX_PAGE))

        with database_manager.tenant(company_id) as conn:
            self._require_channel(
                conn, company_id=company_id, channel_id=channel_id, user_id=user_id
            )

            params: list[Any] = [company_id, int(channel_id)]
            cursor_clause = ""

            if before_id:
                cursor_clause = "AND id < ?"
                params.append(int(before_id))

            rows = conn.execute(
                f"""
                SELECT * FROM team_messages
                WHERE company_id = ? AND channel_id = ? {cursor_clause}
                ORDER BY id DESC
                LIMIT ?
                """,
                [*params, limit + 1],
            ).fetchall()

            total = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM team_messages
                    WHERE company_id = ? AND channel_id = ?
                    """,
                    (company_id, int(channel_id)),
                ).fetchone()["n"]
            )

        has_more = len(rows) > limit
        page = list(rows[:limit])[::-1]
        items = [self._message_dict(row) for row in page]

        return {
            "items": items,
            "total": total,
            "has_more": has_more,
            "next_before_id": int(items[0]["id"]) if items and has_more else None,
        }

    def edit_message(
        self,
        *,
        company_id: int,
        user_id: int,
        message_id: int,
        body: str,
        employees: Iterable[dict[str, Any]] | None = None,
        author_name: str | None = None,
    ) -> dict[str, Any]:
        """Edit one's own message. Newly added mentions are notified once."""
        company_id = int(company_id)
        user_id = int(user_id)
        text = str(body or "").strip()[:MAX_BODY]

        if not text:
            raise ValueError("An edited message still needs text.")

        directory = list(employees) if employees is not None else self._employees(company_id)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                "SELECT * FROM team_messages WHERE id = ? AND company_id = ? LIMIT 1",
                (int(message_id), company_id),
            ).fetchone()

            if not existing:
                raise ChannelNotFound("Message not found.")

            channel = self._require_channel(
                conn,
                company_id=company_id,
                channel_id=int(existing["channel_id"]),
                user_id=user_id,
            )

            if int(existing["author_user_id"]) != user_id:
                raise NotMessageAuthor("You can only edit your own messages.")

            mentioned = extract_mentions(text, directory)

            if int(channel["is_private"]):
                mentioned = self._members_only(
                    conn, channel_id=int(channel["id"]), user_ids=mentioned
                )

            try:
                previous = set(json.loads(existing["mentions_json"] or "[]"))
            except (TypeError, json.JSONDecodeError):
                previous = set()

            conn.execute(
                """
                UPDATE team_messages
                SET body = ?, mentions_json = ?, edited_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (text, json.dumps(mentioned), now, int(message_id), company_id),
            )
            conn.commit()

            row = conn.execute(
                "SELECT * FROM team_messages WHERE id = ?", (int(message_id),)
            ).fetchone()

        self._notify_mentions(
            company_id=company_id,
            channel_name=str(channel["name"]),
            channel_id=int(channel["id"]),
            message_id=int(message_id),
            body=text,
            author_user_id=user_id,
            author_name=author_name,
            mentioned_user_ids=[uid for uid in mentioned if uid not in previous],
            directory=directory,
        )

        return self._message_dict(row)

    def get_message(
        self, *, company_id: int, user_id: int, message_id: int
    ) -> dict[str, Any]:
        """One message, through the same visibility rule as its channel."""
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT * FROM team_messages WHERE id = ? AND company_id = ? LIMIT 1",
                (int(message_id), company_id),
            ).fetchone()

            if not row:
                raise ChannelNotFound("Message not found.")

            self._require_channel(
                conn,
                company_id=company_id,
                channel_id=int(row["channel_id"]),
                user_id=int(user_id),
            )

        return self._message_dict(row)

    def delete_message(
        self, *, company_id: int, user_id: int, message_id: int
    ) -> None:
        """Withdraw one's own message.

        Only the author, and only through the visibility rule: a message in a
        private channel the caller is not in is "not found", never "not yours",
        so a probe cannot confirm that a message id exists.
        """
        company_id = int(company_id)
        user_id = int(user_id)

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                "SELECT * FROM team_messages WHERE id = ? AND company_id = ? LIMIT 1",
                (int(message_id), company_id),
            ).fetchone()

            if not existing:
                raise ChannelNotFound("Message not found.")

            self._require_channel(
                conn,
                company_id=company_id,
                channel_id=int(existing["channel_id"]),
                user_id=user_id,
            )

            if int(existing["author_user_id"]) != user_id:
                raise NotMessageAuthor("You can only delete your own messages.")

            conn.execute(
                "DELETE FROM team_messages WHERE id = ? AND company_id = ?",
                (int(message_id), company_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # The company stream, direct messages and groups
    # ------------------------------------------------------------------

    def company_stream_id(self, *, company_id: int, user_id: int) -> int:
        """The id of the one channel the whole company shares.

        Created on first use and joined by whoever asked. It is an ordinary
        public channel, so every rule above already applies to it; this exists
        only so a client does not have to know its name.
        """
        company_id = int(company_id)
        user_id = int(user_id)
        now = utc_now_iso()

        # The common case by a wide margin: the channel exists and the caller is
        # already in it. The screen asks for this every few seconds while it is
        # open, so it is answered with one read and no write lock — taking a
        # `BEGIN IMMEDIATE` on every poll would serialise every employee's
        # refresh against every other one's.
        with database_manager.tenant(company_id) as conn:
            settled = conn.execute(
                """
                SELECT c.id FROM team_channels c
                JOIN team_channel_members m
                    ON m.channel_id = c.id AND m.user_id = ?
                WHERE c.company_id = ? AND c.name = ?
                LIMIT 1
                """,
                (user_id, company_id, COMPANY_STREAM_NAME),
            ).fetchone()

        if settled:
            return int(settled["id"])

        with database_manager.tenant(company_id) as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                row = conn.execute(
                    """
                    SELECT id FROM team_channels
                    WHERE company_id = ? AND name = ?
                    LIMIT 1
                    """,
                    (company_id, COMPANY_STREAM_NAME),
                ).fetchone()

                if row:
                    channel_id = int(row["id"])
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO team_channels (
                            company_id, name, display_name, kind, topic,
                            is_private, created_by_user_id, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, NULL, 0, ?, ?, ?)
                        """,
                        (
                            company_id,
                            COMPANY_STREAM_NAME,
                            "Team Chat",
                            KIND_CHANNEL,
                            user_id,
                            now,
                            now,
                        ),
                    )
                    channel_id = int(cursor.lastrowid)

                conn.execute(
                    """
                    INSERT OR IGNORE INTO team_channel_members (
                        company_id, channel_id, user_id, last_read_at, joined_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (company_id, channel_id, user_id, now, now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return channel_id

    def get_or_create_dm(
        self, *, company_id: int, user_id: int, other_user_id: int
    ) -> dict[str, Any]:
        """The two-person conversation between the caller and one colleague.

        `assert_employees` first: the colleague's id arrives in a request body,
        and an id in a request body is a global number. Without the check a
        caller could open a private channel naming an employee of another
        company and then write into it.
        """
        company_id = int(company_id)
        user_id = int(user_id)
        other_user_id = int(other_user_id)

        if other_user_id == user_id:
            raise ValueError("Pick a colleague to message.")

        assert_employees(company_id, [other_user_id])

        name = dm_channel_name(user_id, other_user_id)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT id FROM team_channels WHERE company_id = ? AND name = ? LIMIT 1",
                (company_id, name),
            ).fetchone()

        if row:
            channel_id = int(row["id"])
        else:
            try:
                created = self.create_channel(
                    company_id=company_id,
                    user_id=user_id,
                    name=name,
                    is_private=True,
                    member_user_ids=[other_user_id],
                    kind=KIND_DM,
                    display_name=name,
                    stored_name=name,
                )
                channel_id = int(created["id"])
            except ChannelNameTaken:
                # Two requests for the same pair at once. The name is derived
                # from the pair, so the winner's row is the one this caller
                # wanted; there is nothing to reconcile.
                with database_manager.tenant(company_id) as conn:
                    channel_id = int(
                        conn.execute(
                            """
                            SELECT id FROM team_channels
                            WHERE company_id = ? AND name = ? LIMIT 1
                            """,
                            (company_id, name),
                        ).fetchone()["id"]
                    )

        return self.get_room(
            company_id=company_id, user_id=user_id, room_id=channel_id
        )

    def create_group(
        self,
        *,
        company_id: int,
        user_id: int,
        name: str,
        member_user_ids: Iterable[int] | None = None,
        department: str | None = None,
    ) -> dict[str, Any]:
        """A private discussion whose membership is fixed when it is made.

        Two groups may carry the same name — people name them after the work,
        and the work repeats — so the typed name is kept as the label and the
        unique key gets a suffix. `UNIQUE(company_id, name)` is what makes the
        suffix search terminate rather than a loop that hopes.
        """
        company_id = int(company_id)
        user_id = int(user_id)
        label = str(name or "").strip()[:MAX_CHANNEL_NAME]

        if not label:
            raise ValueError("Give the group a name.")

        members = set(assert_employees(company_id, list(member_user_ids or [])))

        if department:
            # Membership of a department is not something this platform
            # records: an employee has a role and a branch, and a department is
            # a line in the customer's menu (`business_departments`), not a
            # roster. Inventing one here — everybody, or everybody with a
            # matching role — would put people in a private discussion on a
            # guess, which is the kind of mistake a group makes sticky.
            raise ValueError(
                "This platform does not record which employees belong to a "
                "department, so a group cannot be built from one. Pick the "
                "colleagues for this group instead."
            )

        members.add(user_id)

        if len(members) < 2:
            raise ValueError("Pick at least one other employee for this group.")

        base = normalize_channel_name(label) or "group"
        candidate = base

        for attempt in range(2, 100):
            try:
                created = self.create_channel(
                    company_id=company_id,
                    user_id=user_id,
                    name=label,
                    is_private=True,
                    member_user_ids=sorted(members - {user_id}),
                    kind=KIND_GROUP,
                    display_name=label,
                    stored_name=candidate,
                )
            except ChannelNameTaken:
                candidate = f"{base}-{attempt}"[:MAX_CHANNEL_NAME]
                continue

            return self.get_room(
                company_id=company_id, user_id=user_id, room_id=int(created["id"])
            )

        raise ValueError("Too many groups already share that name.")

    def _room_dict(
        self, row: Any, *, viewer_user_id: int, members: list[dict[str, Any]]
    ) -> dict[str, Any]:
        room = self._channel_dict(row)
        room["members"] = members

        if room["kind"] == KIND_DM:
            # A direct message is titled by whoever is on the other end of it,
            # so the same row reads "Sara Nasr" to one person and "Omar Hadi"
            # to the other. The stored name is a key, never a label.
            others = [
                member
                for member in members
                if int(member["id"]) != int(viewer_user_id)
            ]
            room["display_name"] = (
                others[0]["display_name"] if others else room["display_name"]
            )

        return room

    def _room_members(
        self, *, company_id: int, channel_ids: list[int]
    ) -> dict[int, list[dict[str, Any]]]:
        """Every room's roster, in one tenant read and one control read.

        A name per member would be one control-plane query per member per room;
        the roster of a company is small and read whole instead.
        """
        if not channel_ids:
            return {}

        placeholders = ",".join("?" for _ in channel_ids)

        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                f"""
                SELECT channel_id, user_id FROM team_channel_members
                WHERE company_id = ? AND channel_id IN ({placeholders})
                ORDER BY joined_at ASC, id ASC
                """,
                (int(company_id), *[int(value) for value in channel_ids]),
            ).fetchall()

        directory = {
            int(employee["id"]): employee
            for employee in self._employees(int(company_id))
        }
        names = auth_service.user_display_names(
            int(company_id), [int(row["user_id"]) for row in rows]
        )

        rosters: dict[int, list[dict[str, Any]]] = {
            int(value): [] for value in channel_ids
        }

        for row in rows:
            member_id = int(row["user_id"])
            employee = directory.get(member_id) or {}
            rosters.setdefault(int(row["channel_id"]), []).append(
                {
                    "id": member_id,
                    "display_name": (
                        employee.get("display_name")
                        or names.get(member_id)
                        or f"User {member_id}"
                    ),
                    "role_name": employee.get("role_name"),
                }
            )

        return rosters

    def list_rooms(self, *, company_id: int, user_id: int) -> list[dict[str, Any]]:
        """The caller's direct messages and groups, most recent first."""
        company_id = int(company_id)
        user_id = int(user_id)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT
                    c.*,
                    1 AS is_member,
                    m.last_read_at AS last_read_at,
                    (
                        SELECT MAX(t.created_at) FROM team_messages t
                        WHERE t.channel_id = c.id
                    ) AS last_message_at,
                    (
                        SELECT COUNT(*) FROM team_messages t
                        WHERE t.channel_id = c.id
                          AND t.author_user_id != ?
                          AND (
                              m.last_read_at IS NULL
                              OR t.created_at > m.last_read_at
                          )
                    ) AS unread_count
                FROM team_channels c
                JOIN team_channel_members m
                    ON m.channel_id = c.id AND m.user_id = ?
                WHERE c.company_id = ? AND c.kind IN (?, ?)
                ORDER BY c.updated_at DESC, c.id DESC
                """,
                (user_id, user_id, company_id, KIND_DM, KIND_GROUP),
            ).fetchall()

        rosters = self._room_members(
            company_id=company_id, channel_ids=[int(row["id"]) for row in rows]
        )

        return [
            self._room_dict(
                row,
                viewer_user_id=user_id,
                members=rosters.get(int(row["id"]), []),
            )
            for row in rows
        ]

    def get_room(
        self, *, company_id: int, user_id: int, room_id: int
    ) -> dict[str, Any]:
        company_id = int(company_id)
        user_id = int(user_id)

        with database_manager.tenant(company_id) as conn:
            row = self._require_channel(
                conn, company_id=company_id, channel_id=room_id, user_id=user_id
            )

        rosters = self._room_members(
            company_id=company_id, channel_ids=[int(row["id"])]
        )

        return self._room_dict(
            row,
            viewer_user_id=user_id,
            members=rosters.get(int(row["id"]), []),
        )

    # ------------------------------------------------------------------
    # Unread state
    # ------------------------------------------------------------------

    @staticmethod
    def _unread_for_channel(
        conn: Any,
        *,
        channel_id: int,
        user_id: int,
        is_member: bool,
        last_read_at: str | None,
    ) -> int:
        if not is_member:
            return 0

        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM team_messages
            WHERE channel_id = ?
              AND author_user_id != ?
              AND (? IS NULL OR created_at > ?)
            """,
            (int(channel_id), int(user_id), last_read_at, last_read_at),
        ).fetchone()

        return int(row["n"])

    def mark_read(
        self, *, company_id: int, user_id: int, channel_id: int
    ) -> dict[str, Any]:
        company_id = int(company_id)
        user_id = int(user_id)
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            self._require_channel(
                conn, company_id=company_id, channel_id=channel_id, user_id=user_id
            )
            conn.execute(
                """
                UPDATE team_channel_members SET last_read_at = ?
                WHERE channel_id = ? AND user_id = ? AND company_id = ?
                """,
                (now, int(channel_id), user_id, company_id),
            )
            conn.commit()

        return {"channel_id": int(channel_id), "last_read_at": now, "unread_count": 0}

    def unread_counts(self, *, company_id: int, user_id: int) -> dict[str, Any]:
        """Per-channel unread totals for one user, plus the badge total."""
        channels = self.list_channels(company_id=company_id, user_id=user_id)
        per_channel = {
            int(channel["id"]): int(channel["unread_count"]) for channel in channels
        }

        return {
            "channels": per_channel,
            "total": sum(per_channel.values()),
        }

    # ------------------------------------------------------------------
    # Mentions
    # ------------------------------------------------------------------

    @staticmethod
    def _members_only(conn: Any, *, channel_id: int, user_ids: list[int]) -> list[int]:
        """Drop mentions of people who are not in this private channel.

        The notification carries the message text, so notifying a non-member
        would hand them the contents of a channel they cannot open.
        """
        if not user_ids:
            return []

        placeholders = ",".join("?" for _ in user_ids)
        rows = conn.execute(
            f"""
            SELECT user_id FROM team_channel_members
            WHERE channel_id = ? AND user_id IN ({placeholders})
            """,
            (int(channel_id), *[int(item) for item in user_ids]),
        ).fetchall()

        allowed = {int(row["user_id"]) for row in rows}
        return [int(user_id) for user_id in user_ids if int(user_id) in allowed]

    @staticmethod
    def _employees(company_id: int) -> list[dict[str, Any]]:
        return auth_service.company_employees(int(company_id))

    def directory(self, company_id: int) -> list[dict[str, Any]]:
        """Everyone who can be mentioned, for the composer's autocomplete."""
        return [
            {
                "id": int(employee["id"]),
                "display_name": employee.get("display_name"),
                "email": employee.get("email"),
                "role_name": employee.get("role_name"),
            }
            for employee in self._employees(company_id)
        ]

    def _notify_mentions(
        self,
        *,
        company_id: int,
        channel_name: str,
        channel_id: int,
        message_id: int,
        body: str,
        author_user_id: int,
        author_name: str | None,
        mentioned_user_ids: list[int],
        directory: list[dict[str, Any]],
    ) -> int:
        recipients = [
            int(user_id)
            for user_id in mentioned_user_ids
            # Nobody wants to be told they mentioned themselves.
            if int(user_id) != int(author_user_id)
        ]

        if not recipients:
            return 0

        if not author_name:
            for employee in directory:
                if int(employee.get("id") or 0) == int(author_user_id):
                    author_name = str(
                        employee.get("display_name") or employee.get("email") or ""
                    )
                    break

        author_label = author_name or f"User {author_user_id}"
        sent = 0

        for user_id in recipients:
            try:
                notification_service.create(
                    company_id=company_id,
                    notification_type="team_mention",
                    title=f"{author_label} mentioned you in #{channel_name}",
                    body=body[:500],
                    recipient_user_id=user_id,
                    actor_user_id=int(author_user_id),
                    severity="info",
                    data={
                        "channel_id": int(channel_id),
                        "channel_name": channel_name,
                        "message_id": int(message_id),
                    },
                    dedupe_key=f"team_mention:{company_id}:{message_id}:{user_id}",
                )
                sent += 1
            except Exception:  # noqa: BLE001
                # A failed notification must not lose the message itself.
                logger.exception(
                    "Mention notification failed company id=%s message id=%s user id=%s",
                    company_id,
                    message_id,
                    user_id,
                )

        return sent

    # ------------------------------------------------------------------
    # Live stream
    # ------------------------------------------------------------------

    def live_signature(self, *, company_id: int, user_id: int) -> str:
        """Cheap change-detection value for one user's team chat view.

        Two aggregate queries over indexed columns, scoped to the channels this
        user may see, so the SSE poll never builds a page unless something the
        user is allowed to know about actually changed.
        """
        company_id = int(company_id)
        user_id = int(user_id)

        with database_manager.tenant(company_id) as conn:
            messages = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(MAX(t.id), 0) AS max_id,
                    COALESCE(MAX(t.created_at), '') AS latest,
                    COALESCE(MAX(COALESCE(t.edited_at, '')), '') AS latest_edit
                FROM team_messages t
                JOIN team_channels c ON c.id = t.channel_id
                LEFT JOIN team_channel_members m
                    ON m.channel_id = c.id AND m.user_id = ?
                WHERE t.company_id = ?
                  AND (c.is_private = 0 OR m.user_id IS NOT NULL)
                """,
                (user_id, company_id),
            ).fetchone()

            channels = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(MAX(c.updated_at), '') AS latest,
                    COALESCE(SUM(CASE WHEN m.user_id IS NULL THEN 0 ELSE 1 END), 0) AS mine,
                    COALESCE(MAX(COALESCE(m.last_read_at, '')), '') AS latest_read
                FROM team_channels c
                LEFT JOIN team_channel_members m
                    ON m.channel_id = c.id AND m.user_id = ?
                WHERE c.company_id = ?
                  AND (c.is_private = 0 OR m.user_id IS NOT NULL)
                """,
                (user_id, company_id),
            ).fetchone()

        parts = [str(messages[key]) for key in messages.keys()]
        parts.extend(str(channels[key]) for key in channels.keys())
        return "|".join(parts)


team_chat_service = TeamChatService()
