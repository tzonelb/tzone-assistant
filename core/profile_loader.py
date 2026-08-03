import json
from pathlib import Path

from database.database import db

PROFILE_PATH = Path("config/bot_profile.json")


class ProfileLoader:
    """Loads the bot profile (company name, channel roles, AI style).

    Historically this only read a single static config/bot_profile.json
    shared by every company. Every getter now accepts an optional
    company_id and, when given one, looks up that company's row in the
    `bot_profiles` DB table first. A company with no row configured yet
    (or company_id=None, for callers that can't resolve one) gets exactly
    the static file's values -- the original, pre-DB behavior -- so
    nothing regresses for companies that haven't been migrated/configured.
    """

    def __init__(self):
        self.reload()

    def reload(self):
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            self.profile = json.load(f)

    def _db_profile(self, company_id):
        if company_id is None:
            return None

        try:
            return db.get_bot_profile(company_id)
        except Exception:
            # DB unavailable/not migrated yet -- behave like no row exists.
            return None

    def get_company(self, company_id=None):
        default_company = self.profile.get("company", {})
        db_profile = self._db_profile(company_id)

        if db_profile and db_profile.get("name"):
            company = dict(default_company)
            company["name"] = db_profile["name"]
            return company

        return default_company

    def get_modules(self, company_id=None):
        # business_modules has no company-scoped DB column yet; static
        # file remains the source of truth for this field.
        return self.profile.get("business_modules", [])

    def get_channel_role(self, channel, company_id=None):
        # channel roles have no company-scoped DB column yet; static file
        # remains the source of truth for this field.
        return (
            self.profile
            .get("channels", {})
            .get(channel, {})
            .get("role")
        )

    def get_ai_style(self, company_id=None):
        default_ai_style = self.profile.get("ai", {})
        db_profile = self._db_profile(company_id)

        if not db_profile:
            return default_ai_style

        ai_style = dict(default_ai_style)

        if db_profile.get("ai_model"):
            ai_style["model"] = db_profile["ai_model"]

        if db_profile.get("ai_reply_mode"):
            ai_style["reply_mode"] = db_profile["ai_reply_mode"]

        if db_profile.get("system_prompt"):
            ai_style["system_prompt"] = db_profile["system_prompt"]

        return ai_style


profile_loader = ProfileLoader()
