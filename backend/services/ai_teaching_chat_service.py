"""TRAIN — the manager's private teaching chat with the assistant.

The screen this serves (`frontend/src/pages/ai-teaching/TrainAndTestPage.jsx`)
has two halves. The bottom half is the dry run, which already existed here. The
top half is this: a manager talks to the assistant in plain language, and when
what they said is an actual standing instruction, the assistant confirms it and
the instruction is added to what the assistant is told on every future customer
message.

Two things make that honest rather than a chat toy:

* **The instruction is stored where the assistant actually reads from.** On this
  platform the assistant's standing instructions are the default bot profile's
  ``system_prompt`` — the one string ``core/prompt_builder`` serializes into
  every reply. Writing them anywhere else would let the screen report "Saved as
  a new instruction" while no customer reply ever changed.
* **The transcript is not a customer conversation.** It lives in its own tenant
  table, never in ``messages``: nothing here was said to or by a customer, and a
  transcript mixed into the inbox would appear in exports, in analytics and in
  retention sweeps as though it had been.

The model call is real, so it is capped and counted exactly like the dry run —
see ``bot_profile_service.preview_reply`` for why a preview that spends the
operator's model budget without moving a number anybody looks at is a defect.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.services.bot_profile_service import (
    MAX_SYSTEM_PROMPT,
    bot_profile_service,
)
from backend.services.plan_service import plan_service
from config.settings import config
from database.manager import database_manager


logger = logging.getLogger(__name__)


MAX_TEACHING_MESSAGE = 2000

# The transcript a screen loads on open. Old enough turns stop being useful and
# a chat that grows without bound is a page that gets slower every week.
TRANSCRIPT_LIMIT = 200


# Narrowly scoped on purpose: this call has exactly one job (acknowledge, and
# extract at most one standing instruction), so it stays cheap and cannot drift
# into answering as though the manager were a customer.
TEACHING_SYSTEM_PROMPT = """
You are being taught how to behave by this company's manager, in a private
training chat (the manager is not a customer — never treat their messages
as customer requests to fulfill). Your job on every message:
1. Reply briefly and naturally, acknowledging what they said.
2. If — and only if — their message contains an actual behavioral
   instruction or fact you should remember for future customer
   conversations, extract it as ONE concise, self-contained, imperative
   sentence (e.g. "Always greet customers in Arabic first" or "Mention
   the 10% loyalty discount when asked about pricing"). If they're just
   chatting, asking a question, or gave no new instruction, this must be
   null — never invent an instruction that wasn't actually given.
Respond ONLY as JSON: {"reply": "<short acknowledgment>", "instruction": "<extracted instruction, or null>"}.
""".strip()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AITeachingChatError(ValueError):
    """A teaching message that cannot be accepted as asked."""


class AITeachingChatService:
    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_messages(self, *, company_id: int) -> list[dict[str, Any]]:
        company_id = int(company_id)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT * FROM ai_teaching_messages
                WHERE company_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (company_id, TRANSCRIPT_LIMIT),
            ).fetchall()

        # Read newest-first so the limit keeps the *latest* turns, handed back
        # oldest-first because that is the order a chat log is read in.
        return [self._public(row) for row in reversed(rows)]

    @staticmethod
    def _public(row: Any) -> dict[str, Any]:
        data = dict(row)
        data["instruction_saved"] = bool(data.get("instruction_saved"))
        return data

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def send_message(
        self,
        *,
        company_id: int,
        actor_user_id: int | None,
        text: str,
    ) -> dict[str, Any]:
        """One turn: the manager's message, the assistant's answer, and — when
        the manager actually gave one — a new standing instruction.

        The manager's message is stored before the model is called. A model
        that times out must not lose what the manager typed.
        """
        company_id = int(company_id)
        text = (text or "").strip()

        if not text:
            raise AITeachingChatError("Type something to teach the assistant.")

        if not config.OPENAI_API_KEY or not getattr(config, "AI_ENABLED", False):
            raise AITeachingChatError(
                "No AI model is configured, so the assistant cannot answer here "
                "yet. Add an OpenAI key in the platform configuration first."
            )

        self._assert_preview_budget(company_id)

        manager_message = self._save_message(
            company_id=company_id,
            role="manager",
            text=text[:MAX_TEACHING_MESSAGE],
            actor_user_id=actor_user_id,
        )

        reply_text, instruction_text, error = self._ask_model(text)

        # Counted after the call, not before: a call that failed cost nothing.
        # Same reasoning, same metric and same cap as the dry run — the two are
        # the only places this platform spends model budget outside a real
        # customer reply.
        if not error:
            plan_service.record_usage(
                company_id=company_id,
                metric=plan_service.AI_PREVIEW_METRIC,
                channel="ai_teaching_chat",
            )

        saved_instruction = None

        if instruction_text:
            saved_instruction = self._remember(
                company_id=company_id, instruction=instruction_text
            )

        assistant_message = self._save_message(
            company_id=company_id,
            role="assistant",
            text=reply_text,
            instruction_saved=bool(saved_instruction),
            instruction_text=saved_instruction,
        )

        return {
            "manager_message": manager_message,
            "assistant_message": assistant_message,
            "instruction_saved": bool(saved_instruction),
            "instruction_text": saved_instruction,
            "error": error,
        }

    def _save_message(
        self,
        *,
        company_id: int,
        role: str,
        text: str,
        instruction_saved: bool = False,
        instruction_text: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()

        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO ai_teaching_messages (
                    company_id, role, text, instruction_saved, instruction_text,
                    actor_user_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(company_id),
                    role,
                    text,
                    int(bool(instruction_saved)),
                    instruction_text,
                    actor_user_id,
                    now,
                ),
            )
            conn.commit()
            post_id = int(cursor.lastrowid)

        return {
            "id": post_id,
            "company_id": int(company_id),
            "role": role,
            "text": text,
            "instruction_saved": bool(instruction_saved),
            "instruction_text": instruction_text,
            "actor_user_id": actor_user_id,
            "created_at": now,
        }

    # ------------------------------------------------------------------
    # Remembering
    # ------------------------------------------------------------------

    @staticmethod
    def _remember(*, company_id: int, instruction: str) -> str | None:
        """Append one instruction to what the assistant is told from now on.

        The design this screen came from kept instructions in a table of their
        own. This platform has exactly one place the assistant's standing
        instructions live — the default profile's ``system_prompt``, read by
        ``core/prompt_builder`` on every customer message — so that is where an
        instruction has to go for the screen's promise to be true.

        Returns the instruction that was stored, or ``None`` when it was not:
        a duplicate, or a prompt already at its limit. Never raises — a saved
        transcript with an unsaved instruction is recoverable, a 500 in the
        middle of a turn is not.
        """
        instruction = " ".join((instruction or "").split()).strip()

        if not instruction:
            return None

        try:
            profile = bot_profile_service.get_default(company_id)
            current = (profile.get("system_prompt") or "").strip()

            # Already said. Repeating it in the prompt costs tokens on every
            # customer message and teaches the model nothing new.
            if instruction.lower() in current.lower():
                return None

            updated = f"{current}\n{instruction}".strip() if current else instruction

            if len(updated) > MAX_SYSTEM_PROMPT:
                logger.warning(
                    "Company %s has no room left in its assistant instructions; "
                    "dropped a taught instruction",
                    company_id,
                )
                return None

            bot_profile_service.update_default(
                company_id=company_id, values={"system_prompt": updated}
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not store a taught instruction for company %s", company_id
            )
            return None

        return instruction

    # ------------------------------------------------------------------
    # The model
    # ------------------------------------------------------------------

    @staticmethod
    def _assert_preview_budget(company_id: int) -> None:
        """The same hard platform cap the dry run is under.

        Fails open on a counter that cannot be read, for the reason spelled out
        in ``bot_profile_service._assert_preview_budget``: one unreadable usage
        table must not take the teaching screen away from every company.
        """
        cap = int(getattr(config, "AI_PREVIEW_MAX_PER_PERIOD", 0) or 0)

        if cap <= 0:
            return

        try:
            used = int(
                plan_service.usage_total(
                    company_id=company_id, metric=plan_service.AI_PREVIEW_METRIC
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not read the preview counter for company %s", company_id
            )
            return

        if used >= cap:
            raise AITeachingChatError(
                f"This month's {cap} assistant previews have been used. The "
                "counter resets at the start of next month; the assistant "
                "itself keeps answering customers normally."
            )

    def _ask_model(self, text: str) -> tuple[str, str | None, str | None]:
        """``(reply, extracted instruction or None, error or None)``.

        Never raises. A model that is down, slow or answering with something
        other than the JSON it was asked for degrades to a plain acknowledgment
        the manager can see, rather than a 500 that loses the turn.
        """
        payload = {
            "model": config.OPENAI_MODEL,
            "input": [
                {"role": "system", "content": TEACHING_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "text": {"format": {"type": "json_object"}},
        }
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            response = self._post_to_model(payload, headers)

            if response.status_code >= 400:
                raise RuntimeError(f"OpenAI error {response.status_code}")

            output_text = self._extract_output_text(response.json())

            if not output_text:
                raise RuntimeError("OpenAI returned empty output")

            parsed = json.loads(output_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("The teaching chat could not reach the model: %s", exc)
            return (
                "I could not reach the model just now — nothing was saved. "
                "Try that again in a moment.",
                None,
                str(exc),
            )

        reply_text = str(parsed.get("reply") or "Got it.")
        instruction = parsed.get("instruction")
        instruction = (
            instruction.strip()
            if isinstance(instruction, str) and instruction.strip()
            else None
        )

        return reply_text, instruction, None

    @staticmethod
    def _post_to_model(payload: dict[str, Any], headers: dict[str, str]):
        """Isolated so a test can stand in for one HTTP call without patching
        ``httpx`` globally — which would also break the FastAPI test client,
        since that is httpx too."""
        with httpx.Client(timeout=config.OPENAI_TIMEOUT_SECONDS) as client:
            return client.post(config.OPENAI_API_URL, headers=headers, json=payload)

    @staticmethod
    def _extract_output_text(data: dict[str, Any]) -> str | None:
        if data.get("output_text"):
            return data["output_text"]

        for output_item in data.get("output", []) or []:
            for content in output_item.get("content", []) or []:
                if content.get("type") in ("output_text", "text"):
                    return content.get("text")

        return None


ai_teaching_chat_service = AITeachingChatService()
