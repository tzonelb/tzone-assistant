"""Whether the assistant answers on a channel, and how — per company.

This file decided, for the whole platform at once, which channels the AI
replies on. `config/automation_policy.json` shipped WhatsApp as
`meta_agent_only` and Telegram as `flow_only`, so on those two channels
`should_auto_reply_with_ai` was False for **every company** — the engine skipped
the assistant and fell through to the scripted flow, which was T-ZONE's own IPTV
menu. Every company's WhatsApp and Telegram customers were answered with
somebody else's business.

Two separate faults produced that, and both are fixed:

* the flow was shared (see `core/flow_loader.py`);
* this decision was shared, and it is the one that sent those channels to the
  flow in the first place.

A company now decides for itself, in its own encrypted database, exactly as it
already decides its reply policy and its assistant's persona. The shipped file
is the platform's starting point and nothing more — the same relationship
`config/response_policy.json` has to `reply_policy_service`.

### Where a company's own answer lives

In the `ai_behavior` settings section, under `channels`:

    {"channels": {"telegram": {"ai_mode": "auto_reply"}}}

An absent company, an absent section or an unreadable database all fall back to
the shipped values, so nothing here can stop a company being answered.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


AI_MODES = ("auto_reply", "router_only", "human_assist", "flow_only", "meta_agent_only")


class AutomationPolicy:
    POLICY_FILE = Path("config") / "automation_policy.json"

    DEFAULT_POLICY = {
        "bot_enabled": True,
        "ai_enabled": True,
        "ai_mode": "auto_reply",
        "voice_ai_enabled": False,
        "image_ai_enabled": False,
    }

    def load_policy(self) -> dict[str, Any]:
        if not self.POLICY_FILE.exists():
            return {"default": self.DEFAULT_POLICY, "channels": {}}

        try:
            with open(self.POLICY_FILE, "r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read %s; using the built-in default", self.POLICY_FILE)

            return {"default": self.DEFAULT_POLICY, "channels": {}}

    def shipped_channel_policy(self, channel: str) -> dict[str, Any]:
        """The platform's starting point for a channel, with no company applied."""
        policy = self.load_policy()

        merged = dict(policy.get("default", self.DEFAULT_POLICY))
        merged.update(policy.get("channels", {}).get(channel, {}))

        return merged

    def get_channel_policy(
        self, channel: str, company_id: Any = None
    ) -> dict[str, Any]:
        """What this company has decided for this channel.

        The shipped values first, then the company's own overrides. A company
        that has chosen nothing gets the platform's defaults, which is what
        every company got before — the difference is that a company can now
        change it, and changing it affects only them.
        """
        merged = self.shipped_channel_policy(channel)

        if company_id is None:
            return merged

        merged.update(self._company_overrides(company_id, channel))

        return merged

    @staticmethod
    def _company_overrides(company_id: Any, channel: str) -> dict[str, Any]:
        """This company's stored automation choices for one channel.

        Never raises. A settings read that could fail a reply would cost a
        customer their answer over a preference, so an unreadable section
        resolves to "no overrides" and the shipped values stand.

        Imported inside the function because `company_settings_service` reaches
        the database, and `core/` is imported by tooling that has none.
        """
        try:
            from backend.services.company_settings_service import (
                company_settings_service,
            )

            values = company_settings_service.get_section(
                int(company_id), "ai_behavior"
            )["values"]
        except Exception:  # noqa: BLE001
            logger.debug(
                "No automation overrides readable for company %s", company_id
            )

            return {}

        channels = values.get("channels")

        if not isinstance(channels, dict):
            return {}

        overrides = channels.get(str(channel).strip().lower())

        if not isinstance(overrides, dict):
            return {}

        clean: dict[str, Any] = {}

        for key in ("bot_enabled", "ai_enabled", "voice_ai_enabled", "image_ai_enabled"):
            if key in overrides:
                clean[key] = bool(overrides[key])

        # Validated rather than passed through: an unrecognised mode would make
        # `is_ai_enabled` false and silence the assistant on that channel, which
        # is a company answering nobody because of a typo.
        mode = str(overrides.get("ai_mode") or "").strip().lower()

        if mode in AI_MODES:
            clean["ai_mode"] = mode

        return clean

    def is_bot_enabled(self, channel: str, company_id: Any = None) -> bool:
        return bool(
            self.get_channel_policy(channel, company_id).get("bot_enabled", True)
        )

    def is_ai_enabled(self, channel: str, company_id: Any = None) -> bool:
        channel_policy = self.get_channel_policy(channel, company_id)

        return (
            bool(channel_policy.get("bot_enabled", True))
            and bool(channel_policy.get("ai_enabled", False))
            and channel_policy.get("ai_mode")
            in ["auto_reply", "router_only", "human_assist"]
        )

    def should_auto_reply_with_ai(self, channel: str, company_id: Any = None) -> bool:
        channel_policy = self.get_channel_policy(channel, company_id)

        return (
            self.is_ai_enabled(channel, company_id)
            and channel_policy.get("ai_mode") == "auto_reply"
        )

    def get_ai_mode(self, channel: str, company_id: Any = None) -> str:
        return str(
            self.get_channel_policy(channel, company_id).get("ai_mode", "flow_only")
        )


automation_policy = AutomationPolicy()
