import json
from pathlib import Path


class ResponsePolicy:
    POLICY_FILE = Path("config") / "response_policy.json"

    DEFAULT_POLICY = {
        "welcome_enabled": True,
        "welcome_mode": "once_per_conversation",
        "welcome_message_ar": "أهلاً وسهلاً بك في T-ZONE 💙",
        "welcome_message_en": "Welcome to T-ZONE 💙",
        "reply_mode": "knowledge_then_ai",
        "allow_ai_free_reply": False,
        "fallback_to_human": True,
        "show_buttons": True
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

    def get_welcome_message(self, channel: str, language: str) -> str:
        policy = self.get_channel_policy(channel)

        if language == "ar":
            return policy.get("welcome_message_ar") or self.DEFAULT_POLICY["welcome_message_ar"]

        return policy.get("welcome_message_en") or self.DEFAULT_POLICY["welcome_message_en"]

    def should_send_welcome(self, channel: str, user_session: dict, language: str) -> bool:
        policy = self.get_channel_policy(channel)

        if not policy.get("welcome_enabled", True):
            return False

        mode = policy.get("welcome_mode", "once_per_conversation")

        if mode == "always":
            return True

        if mode == "never":
            return False

        return not bool(user_session.get("welcome_sent"))

    def compose_reply(
        self,
        channel: str,
        user_session: dict,
        ai_result: dict,
    ) -> tuple[str, list]:
        language = ai_result.get("language") or "ar"
        reply = ai_result.get("reply") or ""
        buttons = ai_result.get("buttons") or []

        if self.should_send_welcome(channel, user_session, language):
            welcome = self.get_welcome_message(channel, language)
            reply = f"{welcome}\n\n{reply}".strip()
            user_session["welcome_sent"] = True

        policy = self.get_channel_policy(channel)

        if not policy.get("show_buttons", True):
            buttons = []

        return reply, buttons


response_policy = ResponsePolicy()