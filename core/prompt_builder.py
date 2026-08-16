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

If neither is set, the prompt falls back to the shared file and says nothing
company-specific, because guessing a company here is exactly the leak this
replaces.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from backend.services.bot_profile_service import bot_profile_service
from core.automation_policy import automation_policy
from core.profile_loader import profile_loader


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
            "channel_role": profile_loader.get_channel_role(channel),
            "business_modules": profile_loader.get_modules(),
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
            self._required_json(),
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
        context = {
            "channel": channel,
            "channel_role": profile_loader.get_channel_role(channel),
            "business_modules": profile_loader.get_modules(),
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

    @staticmethod
    def _required_json() -> str:
        return """
Required JSON:
{
  "department": "sales|iptv|maintenance|accounting|telecom|information|human_support|unknown",
  "intent": "short_intent_name",
  "topic": "short_conversation_topic",
  "language": "ar|en|unknown",
  "confidence": 0.0,
  "reply": "safe customer-facing reply",
  "buttons": ["button1", "button2"],
  "needs_human": false,
  "notes": "internal note"
}
""".strip()

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
