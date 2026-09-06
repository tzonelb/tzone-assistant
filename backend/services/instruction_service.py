"""The company's own behaviour rules for its assistant.

Each rule is a line of guidance -- tone, a thing never to say, when to escalate
-- optionally scoped to some departments and/or channels through its tags, and
ordered by ``position`` so an owner can say which rule wins when two conflict.

The rules are stored in the company's own encrypted database and appended to the
system prompt the model is given (see ``for_prompt``). Nothing here is shared
across companies: one company's instructions never reach another's assistant.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)

# A ceiling so one company cannot grow an unbounded prompt (which every reply
# would then pay for). Generous -- a real rule set is a handful of lines.
MAX_INSTRUCTIONS = 100
MAX_TEXT_LENGTH = 2000


class InstructionError(Exception):
    """A rule that cannot be stored, with a reason a person may read."""


def _row(row: Any) -> dict[str, Any]:
    data = dict(row)
    try:
        data["tags"] = json.loads(data.pop("tags_json") or "[]")
    except (TypeError, ValueError):
        data["tags"] = []
    return data


class InstructionService:
    def list(self, company_id: int) -> list[dict[str, Any]]:
        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                "SELECT id, text, tags_json, position, created_at, updated_at "
                "FROM ai_instructions WHERE company_id = ? "
                "ORDER BY position ASC, id ASC",
                (int(company_id),),
            ).fetchall()
        return [_row(r) for r in rows]

    def create(self, *, company_id: int, text: str, tags: list[str]) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise InstructionError("An instruction cannot be empty.")
        if len(text) > MAX_TEXT_LENGTH:
            raise InstructionError(
                f"An instruction must be under {MAX_TEXT_LENGTH} characters."
            )

        now = utc_now_iso()
        with database_manager.tenant(int(company_id)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM ai_instructions WHERE company_id = ?",
                (int(company_id),),
            ).fetchone()["n"]
            if int(count) >= MAX_INSTRUCTIONS:
                raise InstructionError(
                    f"A company can have at most {MAX_INSTRUCTIONS} instructions."
                )

            # New rule goes last, so adding one never silently reorders the rest.
            next_pos = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS p "
                "FROM ai_instructions WHERE company_id = ?",
                (int(company_id),),
            ).fetchone()["p"]

            cursor = conn.execute(
                "INSERT INTO ai_instructions "
                "(company_id, text, tags_json, position, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (int(company_id), text, json.dumps(list(tags or [])), int(next_pos), now, now),
            )
            conn.commit()
            new_id = int(cursor.lastrowid)

            row = conn.execute(
                "SELECT id, text, tags_json, position, created_at, updated_at "
                "FROM ai_instructions WHERE id = ? AND company_id = ?",
                (new_id, int(company_id)),
            ).fetchone()
        return _row(row)

    def update(
        self, *, company_id: int, instruction_id: int, text: str, tags: list[str]
    ) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise InstructionError("An instruction cannot be empty.")
        if len(text) > MAX_TEXT_LENGTH:
            raise InstructionError(
                f"An instruction must be under {MAX_TEXT_LENGTH} characters."
            )

        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                "UPDATE ai_instructions SET text = ?, tags_json = ?, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                (text, json.dumps(list(tags or [])), utc_now_iso(),
                 int(instruction_id), int(company_id)),
            )
            conn.commit()
            if cursor.rowcount != 1:
                raise InstructionError("That instruction does not exist.")

            row = conn.execute(
                "SELECT id, text, tags_json, position, created_at, updated_at "
                "FROM ai_instructions WHERE id = ? AND company_id = ?",
                (int(instruction_id), int(company_id)),
            ).fetchone()
        return _row(row)

    def delete(self, *, company_id: int, instruction_id: int) -> bool:
        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                "DELETE FROM ai_instructions WHERE id = ? AND company_id = ?",
                (int(instruction_id), int(company_id)),
            )
            conn.commit()
            return cursor.rowcount > 0

    def reorder(self, *, company_id: int, ordered_ids: list[int]) -> None:
        """Set the position of each id to its place in the given order.

        Only rows that belong to this company are touched; an id from another
        company (or one that does not exist) moves nothing.
        """
        with database_manager.tenant(int(company_id)) as conn:
            for position, instruction_id in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE ai_instructions SET position = ?, updated_at = ? "
                    "WHERE id = ? AND company_id = ?",
                    (int(position), utc_now_iso(), int(instruction_id), int(company_id)),
                )
            conn.commit()

    # ---------------------------------------------------------- the AI reads it

    def for_prompt(
        self,
        company_id: int,
        department: str | None = None,
        channel: str | None = None,
    ) -> list[str]:
        """The instruction texts that apply to this reply, in priority order.

        A rule with no scope tags applies always. A rule tagged with departments
        applies only when the conversation is in one of them; likewise channels.
        The match is deliberately forgiving: tags are compared case-folded, and a
        rule scoped to a department the message is not in is simply skipped, not
        an error.
        """
        try:
            rows = self.list(int(company_id))
        except Exception:  # noqa: BLE001
            logger.exception("Could not read AI instructions for company %s", company_id)
            return []

        dept = str(department or "").strip().lower()
        chan = str(channel or "").strip().lower()

        applicable: list[str] = []
        for item in rows:
            tags = [str(t).strip().lower() for t in (item.get("tags") or [])]
            dept_tags = [t for t in tags if t.startswith("dept:")]
            chan_tags = [t for t in tags if t.startswith("channel:")]

            if dept_tags and (not dept or f"dept:{dept}" not in dept_tags):
                # Also accept a bare department name tag, which is how the
                # current screen stores them.
                if not any(dept == t for t in tags):
                    continue
            if chan_tags and (not chan or f"channel:{chan}" not in chan_tags):
                if not any(chan == t for t in tags):
                    continue

            text = str(item.get("text") or "").strip()
            if text:
                applicable.append(text)

        return applicable


instruction_service = InstructionService()
