import json
from pathlib import Path

PROFILE_PATH = Path("config/bot_profile.json")


class ProfileLoader:

    def __init__(self):
        self.reload()

    def reload(self):
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            self.profile = json.load(f)

    def get_company(self):
        return self.profile.get("company", {})

    def get_modules(self):
        return self.profile.get("business_modules", [])

    def get_channel_role(self, channel):
        return (
            self.profile
            .get("channels", {})
            .get(channel, {})
            .get("role")
        )

    def get_ai_style(self):
        return self.profile.get("ai", {})


profile_loader = ProfileLoader()