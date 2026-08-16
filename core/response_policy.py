"""When and how a reply is wrapped before it leaves for the customer.

The behavioural half of this — whether a welcome is sent at all, how often, and
whether buttons are shown — is per channel and still comes from
``config/response_policy.json``.

The *words* of the welcome do not. They used to: this module shipped a
``DEFAULT_POLICY`` containing one company's greeting ("Welcome to T-ZONE 💙"),
read from a single shared file, and prefixed it to the replies of every company
on the platform. A customer messaging any business on this platform was greeted
by somebody else's.

The greeting now comes from the asking company's own assistant profile
(``bot_profiles.welcome_message_ar`` / ``welcome_message_en`` /
``welcome_enabled``) inside that company's encrypted database, which is what the
AI TEACHING screen already edits.

When there is no company, or the company wrote no welcome, **no greeting is
sent**. Substituting a generic one would put words in a business's mouth that
nobody there ever wrote, which is the same defect as the T-ZONE greeting with a
blander disguise.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.services.bot_profile_service import bot_profile_service


logger = logging.getLogger(__name__)


class ResponsePolicy:
    POLICY_FILE = Path("config") / "response_policy.json"

    # Behaviour only. This file is shared by the whole platform, so nothing
    # customer-facing may live in it.
    DEFAULT_POLICY = {
        "welcome_enabled": True,
        "welcome_mode": "once_per_conversation",
        "reply_mode": "knowledge_then_ai",
        "allow_ai_free_reply": False,
        "fallback_to_human": True,
        "show_buttons": True,
    }

    # Keys that may never survive a read of the shared file, whatever a
    # deployment happens to have left in it. The merged policy is not only used
    # to decide behaviour — ``core/engine.py`` hands it to ``ai_router.route``,
    # which serializes it into the model's user payload on every message. A
    # greeting left in the file therefore reached every company's model as well
    # as every company's customer, so it is dropped on the way out rather than
    # trusted to go unread.
    IDENTITY_KEYS = (
        "welcome_message_ar",
        "welcome_message_en",
    )

    def load_policy(self):
        if not self.POLICY_FILE.exists():
            return {
                "default": self.DEFAULT_POLICY,
                "channels": {}
            }

        with open(self.POLICY_FILE, "r", encoding="utf-8") as file:
            return json.load(file)

    def get_channel_policy(self, channel: str) -> dict:
        policy = self.load_policy()
        default_policy = policy.get("default", self.DEFAULT_POLICY)
        channel_policy = policy.get("channels", {}).get(channel, {})

        merged = default_policy.copy()
        merged.update(channel_policy)

        for key in self.IDENTITY_KEYS:
            merged.pop(key, None)

        return merged

    # ------------------------------------------------------------------
    # The welcome
    # ------------------------------------------------------------------

    def company_profile(
        self,
        company_id: int | None,
        channel_account_id: int | None = None,
    ) -> dict[str, Any] | None:
        """The asking company's assistant profile, or nothing.

        Never raises: this is the customer reply path, and a profile that will
        not load must cost the customer a greeting, not the answer.
        """
        if not company_id:
            return None

        return bot_profile_service.prompt_profile(company_id, channel_account_id)

    def get_welcome_message(
        self,
        channel: str,
        language: str,
        company_id: int | None = None,
        channel_account_id: int | None = None,
    ) -> str:
        """This company's own welcome, or an empty string.

        ``channel`` is still accepted because the channel policy decides whether
        a welcome is wanted at all; it no longer supplies any text.
        """
        profile = self.company_profile(company_id, channel_account_id)

        if not profile:
            return ""

        if not bool(profile.get("welcome_enabled", True)):
            return ""

        key = "welcome_message_ar" if language == "ar" else "welcome_message_en"

        return str(profile.get(key) or "").strip()

    def should_send_welcome(
        self,
        channel: str,
        user_session: dict,
        language: str,
    ) -> bool:
        policy = self.get_channel_policy(channel)

        if not policy.get("welcome_enabled", True):
            return False

        mode = policy.get("welcome_mode", "once_per_conversation")

        if mode == "always":
            return True

        if mode == "never":
            return False

        return not bool(user_session.get("welcome_sent"))

    # ------------------------------------------------------------------
    # Composing
    # ------------------------------------------------------------------

    def compose_reply(
        self,
        channel: str,
        user_session: dict,
        ai_result: dict,
        company_id: int | None = None,
        channel_account_id: int | None = None,
    ) -> tuple[str, list]:
        language = ai_result.get("language") or "ar"
        reply = ai_result.get("reply") or ""
        buttons = ai_result.get("buttons") or []

        if self.should_send_welcome(channel, user_session, language):
            welcome = self.get_welcome_message(
                channel,
                language,
                company_id=company_id,
                channel_account_id=channel_account_id,
            )

            # No welcome written means no welcome sent. The reply goes out
            # unprefixed rather than carrying an invented greeting.
            if welcome:
                reply = f"{welcome}\n\n{reply}".strip()
                user_session["welcome_sent"] = True

        policy = self.get_channel_policy(channel)

        if not policy.get("show_buttons", True):
            buttons = []

        return reply, buttons


response_policy = ResponsePolicy()
