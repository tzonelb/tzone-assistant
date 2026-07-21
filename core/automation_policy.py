import json
from pathlib import Path


class AutomationPolicy:
    POLICY_FILE = Path("config") / "automation_policy.json"

    DEFAULT_POLICY = {
        "bot_enabled": True,
        "ai_enabled": True,
        "ai_mode": "auto_reply",
        "voice_ai_enabled": False,
        "image_ai_enabled": False,
    }

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

        return merged

    def is_bot_enabled(self, channel: str) -> bool:
        return bool(self.get_channel_policy(channel).get("bot_enabled", True))

    def is_ai_enabled(self, channel: str) -> bool:
        channel_policy = self.get_channel_policy(channel)

        return (
            bool(channel_policy.get("bot_enabled", True))
            and bool(channel_policy.get("ai_enabled", False))
            and channel_policy.get("ai_mode") in ["auto_reply", "router_only", "human_assist"]
        )

    def should_auto_reply_with_ai(self, channel: str) -> bool:
        channel_policy = self.get_channel_policy(channel)

        return (
            self.is_ai_enabled(channel)
            and channel_policy.get("ai_mode") == "auto_reply"
        )

    def get_ai_mode(self, channel: str) -> str:
        return str(self.get_channel_policy(channel).get("ai_mode", "flow_only"))


automation_policy = AutomationPolicy()