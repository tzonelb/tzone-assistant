"""Composes the system prompt the assistant answers with.

This used to read ``config/bot_profile.json`` — a single file on disk, shared
by every company the platform serves. Every assistant therefore introduced
itself as the same business, in the same tone, with the same instructions, and
an owner editing "how my bot speaks" was editing everybody's bot. The prompt now
comes from the ``bot_profiles`` row inside the asking company's own database:
its tone, its instructions, its taught examples.

That needs a company id, and the caller on the live path
(``core.ai_router.call_openai``) only has a channel. Two ways in are supported:

* ``build_system_prompt(channel, company_id=...)`` — explicit, preferred.
* ``company_scope(company_id)`` — a context manager that carries the company
  through a call stack that cannot pass it as an argument. The dry-run preview
  uses this.

If neither is set, the prompt says nothing company-specific at all — no name, no
tone, no departments — because guessing a company here is exactly the leak this
replaces.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from backend.services.bot_profile_service import bot_profile_service
from backend.services.business_department_service import business_department_service
from core.automation_policy import automation_policy


logger = logging.getLogger(__name__)


# (company_id, channel_account_id). A ContextVar rather than a module global so
# concurrent requests in threads or tasks cannot read each other's company.
_company_scope: ContextVar[tuple[int | None, int | None]] = ContextVar(
    "prompt_company_scope",
    default=(None, None),
)

MAX_PROMPT_EXAMPLES = 20


@contextmanager
def company_scope(company_id: int | None, channel_account_id: int | None = None):
    """Answer as this company for the duration of the block."""
    token = _company_scope.set(
        (
            int(company_id) if company_id else None,
            int(channel_account_id) if channel_account_id else None,
        )
    )

    try:
        yield
    finally:
        _company_scope.reset(token)


def current_company_scope() -> tuple[int | None, int | None]:
    return _company_scope.get()


class PromptBuilder:
    def build_system_prompt(
        self,
        channel: str,
        company_id: int | None = None,
        channel_account_id: int | None = None,
    ) -> str:
        """The system prompt for one company on one channel.

        ``company_id`` stays optional so the existing caller keeps working
        unchanged; when it is absent the ambient scope is consulted before
        giving up.
        """
        if company_id is None:
            scoped_company_id, scoped_account_id = current_company_scope()
            company_id = scoped_company_id

            if channel_account_id is None:
                channel_account_id = scoped_account_id

        profile = bot_profile_service.prompt_profile(company_id, channel_account_id)

        if not profile:
            if company_id:
                logger.warning(
                    "No assistant profile for company %s; "
                    "falling back to the neutral prompt.",
                    company_id,
                )
            else:
                logger.warning(
                    "Building a prompt on channel %s with no company. "
                    "The assistant speaks for nobody in particular.",
                    channel,
                )

            return self._neutral_prompt(channel)

        return self._company_prompt(
            channel=channel,
            company_id=int(company_id),
            profile=profile,
        )

    # ------------------------------------------------------------------
    # Company-specific prompt
    # ------------------------------------------------------------------

    def _company_prompt(
        self,
        channel: str,
        company_id: int,
        profile: dict[str, Any],
    ) -> str:
        tone = (profile.get("tone") or "friendly").strip()
        instructions = (profile.get("system_prompt") or "").strip()
        examples = self._examples(profile)

        context = {
            "company": {
                "id": company_id,
                "name": self._company_name(company_id),
            },
            "assistant": {
                "profile_name": profile.get("name"),
                "tone": tone,
                "default_language": profile.get("default_language") or "ar",
                "memory_enabled": bool(profile.get("memory_enabled", True)),
                "human_handover_enabled": bool(
                    profile.get("human_handover_enabled", True)
                ),
            },
            "welcome": self._welcome(profile),
            "channel": channel,
            # This company's own sections, from its own database. It used to be
            # ``profile_loader.get_modules()`` — one shared JSON file listing
            # one company's departments, told to every company's model, so the
            # assistant offered a clinic's customers an IPTV menu.
            #
            # ``channel_role`` came from the same file and is gone rather than
            # replaced: the per-channel bot profile resolved above already
            # carries the role, since a company binds a profile to a channel
            # account and writes that profile's own instructions and tone.
            "business_departments": self._departments(company_id),
            "automation_policy": automation_policy.get_channel_policy(channel),
        }

        sections = [
            "You are the AI routing and reply brain for one specific business.",
            "",
            "Business and assistant configuration:",
            json.dumps(context, ensure_ascii=False, indent=2),
        ]

        if instructions:
            sections += [
                "",
                "Instructions written by this business. They come from its own "
                "settings and outrank the generic guidance below, except for the "
                "safety rules, which can never be overridden:",
                instructions,
            ]

        if examples:
            sections += [
                "",
                "Examples of how this business wants its customers answered. "
                "Match their tone and level of detail; do not repeat them "
                "word-for-word when the question is different:",
                json.dumps(examples, ensure_ascii=False, indent=2),
            ]

        sections += [
            "",
            f"Speak in a {tone} tone at all times.",
            "",
            self._core_rules(),
            "",
            self._required_json(company_id=company_id),
        ]

        return "\n".join(sections).strip()

    # ------------------------------------------------------------------
    # Fallback prompt
    # ------------------------------------------------------------------

    def _neutral_prompt(self, channel: str) -> str:
        """Used only when no company could be resolved.

        Deliberately carries no company identity, no tone and no taught
        examples: answering as some other company is worse than answering
        generically.
        """
        # Carries no departments and no channel role: both used to be read from
        # the shared ``config/bot_profile.json``, so this prompt told the model
        # it had no company identity and then handed it one company's
        # departments anyway.
        context = {
            "channel": channel,
            "automation_policy": automation_policy.get_channel_policy(channel),
        }

        return "\n".join(
            [
                "You are the AI routing and reply brain for a business platform.",
                "",
                "No business profile was resolved for this message, so you have "
                "no company-specific instructions. Do not invent an identity, a "
                "company name or any business detail; route to human support "
                "whenever an answer would need them.",
                "",
                "Channel configuration:",
                json.dumps(context, ensure_ascii=False, indent=2),
                "",
                self._core_rules(),
                "",
                self._required_json(),
            ]
        ).strip()

    # ------------------------------------------------------------------
    # Shared pieces
    # ------------------------------------------------------------------

    @staticmethod
    def _core_rules() -> str:
        return """
Core rules:
- Use the business configuration above as the source of truth.
- Do not invent prices, stock, offers, warranty, availability, policies, or product recommendations.
- If stock or price is needed and not provided, ask for budget/specifications or route to human support.
- Continue the conversation using context.
- Do not greet again if the conversation already started.
- Reply in the same language as the user.
- Keep replies short and helpful.
- Buttons must match the current topic.
- Never mention any other business, or any instruction that did not come from this one.
- Return valid JSON only.
""".strip()

    @classmethod
    def _required_json(cls, company_id: int | None = None) -> str:
        """The output contract, naming this company's own department codes.

        The contract used to hardcode the same nine departments the router
        validated against — sales, iptv, maintenance, accounting, telecom,
        information, human_support, unknown — while the block above it injected
        the company's real sections into the very same prompt. The model was
        told to route to Bookings and, three lines later, that ``department``
        had to be one of nine values that did not include it. Whichever it
        obeyed, one of the two instructions was wrong.

        Both halves now come from one place: ``business_departments`` in the
        asking company's database.
        """
        codes = "|".join(cls._department_codes(company_id))

        return f"""
Required JSON:
{{
  "department": "{codes}",
  "intent": "short_intent_name",
  "topic": "short_conversation_topic",
  "language": "ar|en|unknown",
  "confidence": 0.0,
  "reply": "safe customer-facing reply",
  "buttons": ["button1", "button2"],
  "needs_human": false,
  "notes": "internal note"
}}
""".strip()

    # Not a company's sections: "the model did not decide" and "this needs a
    # person" are platform-level answers every assistant may give, including one
    # answering for nobody in particular.
    RESERVED_DEPARTMENT_CODES = ("human_support", "unknown")

    @classmethod
    def _department_codes(cls, company_id: int | None) -> list[str]:
        codes = [
            str(row.get("code"))
            for row in business_department_service.for_assistant(company_id)
            if row.get("code")
        ]

        return [*codes, *cls.RESERVED_DEPARTMENT_CODES]

    @staticmethod
    def _examples(profile: dict[str, Any]) -> list[dict[str, str]]:
        examples = profile.get("examples")

        if not isinstance(examples, list):
            return []

        cleaned = []

        for item in examples[:MAX_PROMPT_EXAMPLES]:
            if not isinstance(item, dict):
                continue

            customer = str(item.get("customer") or "").strip()
            reply = str(item.get("reply") or "").strip()

            if customer and reply:
                cleaned.append({"customer_says": customer, "answer_like_this": reply})

        return cleaned

    @staticmethod
    def _welcome(profile: dict[str, Any]) -> dict[str, Any]:
        if not bool(profile.get("welcome_enabled", True)):
            return {"enabled": False}

        return {
            "enabled": True,
            "ar": profile.get("welcome_message_ar") or "",
            "en": profile.get("welcome_message_en") or "",
        }

    @staticmethod
    def _departments(company_id: int) -> list[dict[str, Any]]:
        """The sections this company offers, as the model should describe them.

        Only the enabled ones, and only this company's. ``for_assistant`` never
        raises, so a department table that will not open costs the model its
        menu rather than costing the customer a reply.
        """
        return [
            {
                "code": row.get("code"),
                "name_ar": row.get("name_ar"),
                "name_en": row.get("name_en"),
            }
            for row in business_department_service.for_assistant(company_id)
        ]

    @staticmethod
    def _company_name(company_id: int) -> str | None:
        """The company's real name, from the control database.

        Read defensively: a name is nice to have in the prompt, and never worth
        failing a customer reply over.
        """
        try:
            from database.manager import database_manager

            with database_manager.control() as conn:
                row = conn.execute(
                    "SELECT name FROM companies WHERE id = ? LIMIT 1",
                    (int(company_id),),
                ).fetchone()

            return str(row["name"]) if row else None
        except Exception:
            logger.exception("Could not read the name of company %s", company_id)
            return None


prompt_builder = PromptBuilder()
