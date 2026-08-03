import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class AutomationPolicy:
    POLICY_FILE = Path("config") / "automation_policy.json"

    DEFAULT_POLICY = {
        "bot_enabled": True,
        "ai_enabled": True,
        "ai_mode": "auto_reply",
        "voice_ai_enabled": False,
        "image_ai_enabled": False,
    }

    # ai_mode values automation_policy actually knows how to act on. This is
    # also used to validate the company-scoped "ai_behavior.mode" override
    # (see _company_overrides) before ever letting it replace the per-channel
    # file default -- an unrecognized value (e.g. company_settings_service's
    # own DEFAULT_SETTINGS["ai_behavior"]["mode"] of "ai_first", which predates
    # this integration and isn't one of these) is ignored rather than applied,
    # so it can never silently disable auto-reply for a channel.
    AI_MODES = {
        "auto_reply",
        "router_only",
        "human_assist",
        "meta_agent_only",
        "flow_only",
    }

    def load_policy(self):
        if not self.POLICY_FILE.exists():
            return {
                "default": self.DEFAULT_POLICY,
                "channels": {}
            }

        with open(self.POLICY_FILE, "r", encoding="utf-8") as file:
            return json.load(file)

    def _company_overrides(self, company_id: int | None) -> dict:
        """Company-scoped layer on top of the static per-channel file policy.

        Reads backend.services.company_settings_service's "ai_behavior"
        section -- the same company-scoped DB table the Company Settings UI
        already writes to via PUT /api/company-settings/ai_behavior -- and
        translates the two fields that map onto this module's vocabulary:

          - ai_behavior.enabled (bool) -> bot_enabled + ai_enabled. This is
            deliberately a single company-wide on/off switch: enabled=False
            forces both flags False (disables AI/bot auto-reply on every
            channel for that company); enabled=True forces both flags True,
            which is a no-op against every channel's current file default
            (all of them already default bot_enabled/ai_enabled=True) so it
            can never *widen* what a channel does -- a channel still gated
            off by its own ai_mode (e.g. telegram's "flow_only", whatsapp's
            "meta_agent_only") stays gated off regardless of this flag.

          - ai_behavior.mode (str) -> ai_mode, but ONLY when it is one of
            AutomationPolicy.AI_MODES. company_settings_service's own
            DEFAULT_SETTINGS ships "mode": "ai_first", which is not in that
            set, so an unconfigured company's default value is silently
            ignored here and the per-channel file default is used instead --
            this is what keeps an unconfigured company byte-for-byte
            identical to today's static-file behavior.

        Any failure (no DB yet, invalid company_id, section not migrated,
        etc.) is swallowed and treated as "no override" so a company with no
        usable configuration always falls back to the file default exactly
        as before this integration existed.
        """
        if not company_id:
            return {}

        try:
            # Local import: mirrors the pattern already used by
            # backend/services/conversation_control_service.py's
            # _takeover_timeout_minutes -- avoids import-time coupling
            # between core/ (imported very early, e.g. by tests that only
            # touch automation_policy directly) and backend/services'
            # schema-initializing singletons.
            from backend.services.company_settings_service import (
                company_settings_service,
            )

            values = company_settings_service.get_section(
                company_id, "ai_behavior"
            )["values"]
        except Exception:
            logger.exception(
                "Failed to load company ai_behavior settings for "
                "company_id=%s; falling back to static automation_policy "
                "defaults.",
                company_id,
            )
            return {}

        overrides: dict = {}

        if "enabled" in values:
            enabled = bool(values.get("enabled"))
            overrides["bot_enabled"] = enabled
            overrides["ai_enabled"] = enabled

        mode = values.get("mode")

        if isinstance(mode, str) and mode in self.AI_MODES:
            overrides["ai_mode"] = mode

        return overrides

    def get_channel_policy(self, channel: str, company_id: int | None = None) -> dict:
        policy = self.load_policy()

        default_policy = policy.get("default", self.DEFAULT_POLICY)
        channel_policy = policy.get("channels", {}).get(channel, {})

        merged = default_policy.copy()
        merged.update(channel_policy)
        merged.update(self._company_overrides(company_id))

        return merged

    def is_bot_enabled(self, channel: str, company_id: int | None = None) -> bool:
        return bool(
            self.get_channel_policy(channel, company_id).get("bot_enabled", True)
        )

    def is_ai_enabled(self, channel: str, company_id: int | None = None) -> bool:
        channel_policy = self.get_channel_policy(channel, company_id)

        return (
            bool(channel_policy.get("bot_enabled", True))
            and bool(channel_policy.get("ai_enabled", False))
            and channel_policy.get("ai_mode") in ["auto_reply", "router_only", "human_assist"]
        )

    def should_auto_reply_with_ai(self, channel: str, company_id: int | None = None) -> bool:
        channel_policy = self.get_channel_policy(channel, company_id)

        return (
            self.is_ai_enabled(channel, company_id)
            and channel_policy.get("ai_mode") == "auto_reply"
        )

    def get_ai_mode(self, channel: str, company_id: int | None = None) -> str:
        return str(
            self.get_channel_policy(channel, company_id).get("ai_mode", "flow_only")
        )


automation_policy = AutomationPolicy()