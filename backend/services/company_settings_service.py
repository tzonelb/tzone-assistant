"""Per-company settings, Super Admin overrides and the change audit.

All three tables live in the company's own encrypted database. `actor_user_id`
and `updated_by_user_id` point at control-plane users and are stored as plain
integers; names are resolved through `auth_service.user_display_names` when they
are needed, because SQLite cannot join across two files.

Table creation belongs to `database/schema_tenant.py` alone.
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_SETTINGS: dict[str, Any] = {
    # The real values come from this company's control-plane row, resolved in
    # `_defaults_for`. The keys are listed here so the section exists and so
    # `get_all` still enumerates it.
    #
    # There is deliberately no `workspace_code` key. There used to be, holding
    # the string "tzone", and it was a label that did nothing. The workspace
    # code is now the credential that unseals this company's database: it lives
    # in the control plane, it is never returned to a browser, and it is
    # rotated from the Super Admin console. A settings field of the same name
    # that silently ignores what you type is worse than no field at all.
    "company_profile": {
        "company_name": "",
        "timezone": "Asia/Beirut",
        "default_language": "ar",
        "country": "",
        "currency": "USD",
    },
    "ai_behavior": {
        "enabled": True,
        "mode": "ai_first",
        "collect_message_delay_seconds": 20,
        "return_to_ai_timeout_minutes": 5,
        "reply_access_mode": "take_required",
        "auto_read_mode": "assigned_owner_only",
        "auto_release_to_ai": True,
        "welcome_immediate": True,
        "reply_only_when_customer_stops_typing": True,
    },
    "notifications": {
        "new_customer_message": True,
        "ai_replied": False,
        "employee_replied": False,
        "in_app_popup": True,
        "desktop": True,
        "sound": True,
    },
    "reply_flow": {
        "steps": [
            "welcome",
            "language_detection",
            "intent_detection",
            "knowledge_lookup",
            "answer",
            "escalation",
        ]
    },
    # How this company answers, per channel: whether a welcome is sent and how
    # often, whether the assistant may reply freely, how confident a knowledge
    # match must be, how many knowledge items reach the model, whether buttons
    # are shown. It used to be one shared file for the whole platform, so no
    # company could change any of it without changing it for everybody.
    #
    # Sparse on purpose, and empty by default: a key that is absent inherits
    # the platform's shipped value in `config/response_policy.json`, and a
    # channel that is absent inherits this company's default. Seeding the
    # shipped values in here would freeze a copy of them and make "clear this
    # override" impossible to tell apart from "set it to the same thing".
    #
    # `backend/services/reply_policy_service.py` owns the shape, the validation
    # and the resolution; writes to this section are validated there, including
    # writes that arrive straight at `/api/company-settings/reply_policy`.
    # Unlike `modules` below, this section is the company's to edit.
    "reply_policy": {
        "default": {},
        "channels": {},
    },
    # Read-only here. This section used to carry its own five switches that
    # nothing ever read, so a company could turn Appointments "off" and keep
    # using it. Module visibility is now one decision, made by the platform
    # administrator and enforced by `backend/services/module_access`; this
    # section reports it and refuses writes.
    "modules": {},
}


class CompanySettingsService:
    @staticmethod
    def _loads(value: str | None, fallback: Any) -> Any:
        try:
            parsed = json.loads(value or "")
            return parsed
        except (json.JSONDecodeError, TypeError):
            return fallback

    def _company_profile_defaults(self, company_id: int) -> dict[str, Any]:
        """This company's identity, taken from the control plane.

        A static default here meant every company's settings screen opened
        showing the platform owner's own company name.
        """
        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT name, timezone, default_language, country, currency
                FROM companies
                WHERE id = ?
                LIMIT 1
                """,
                (int(company_id),),
            ).fetchone()

        if not row:
            return dict(DEFAULT_SETTINGS["company_profile"])

        return {
            "company_name": row["name"],
            "timezone": row["timezone"] or "Asia/Beirut",
            "default_language": row["default_language"] or "ar",
            "country": row["country"] or "",
            "currency": row["currency"] or "USD",
        }

    def _defaults_for(self, company_id: int, section: str) -> dict[str, Any]:
        if section == "company_profile":
            return self._company_profile_defaults(company_id)

        if section == "modules":
            # The platform administrator's decision, reported as it stands.
            from backend.services.platform_service import platform_service

            return dict(platform_service.get_platform_config(company_id)["modules"])

        # Deep: `reply_policy` and `reply_flow` hold nested values, and a
        # shallow copy would hand every company the same inner dict to mutate.
        return copy.deepcopy(DEFAULT_SETTINGS.get(section, {}))

    def get_section(self, company_id: int, section: str) -> dict[str, Any]:
        company_id = int(company_id)
        normalized = section.strip().lower()
        defaults = self._defaults_for(company_id, normalized)

        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                """
                SELECT settings_json
                FROM company_settings
                WHERE company_id = ? AND section = ?
                LIMIT 1
                """,
                (company_id, normalized),
            ).fetchone()

            override_rows = conn.execute(
                """
                SELECT setting_key, value_json, is_locked
                FROM super_admin_setting_overrides
                WHERE company_id = ? AND section = ?
                """,
                (company_id, normalized),
            ).fetchall()

        stored = self._loads(row["settings_json"], {}) if row else {}

        if normalized == "modules":
            # Reported, never overridden. Any stored value here predates the
            # platform switches and would contradict what the API enforces.
            values = defaults
            locked = list(defaults)
        else:
            values = {**defaults, **stored}
            locked = []

        for override in override_rows:
            key = str(override["setting_key"])
            if override["value_json"] is not None:
                values[key] = self._loads(override["value_json"], values.get(key))
            if bool(override["is_locked"]):
                locked.append(key)

        return {
            "section": normalized,
            "values": values,
            "locked_keys": sorted(set(locked)),
        }

    def get_all(self, company_id: int) -> dict[str, Any]:
        company_id = int(company_id)
        sections = set(DEFAULT_SETTINGS)
        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                "SELECT section FROM company_settings WHERE company_id = ?",
                (company_id,),
            ).fetchall()
        sections.update(str(row["section"]) for row in rows)
        return {
            section: self.get_section(company_id, section)
            for section in sorted(sections)
        }

    def update_section(
        self,
        company_id: int,
        section: str,
        values: dict[str, Any],
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        normalized = section.strip().lower()
        current = self.get_section(company_id, normalized)
        locked = set(current["locked_keys"])
        forbidden = sorted(key for key in values if key in locked)
        if forbidden:
            raise ValueError(
                "These settings are locked by Super Admin: " + ", ".join(forbidden)
            )

        merged = {**current["values"], **values}

        if normalized == "reply_policy":
            # Validated wherever the write came from, not only from the screen
            # that usually sends it. An unknown key or an out-of-range value
            # reads back exactly like a decision that was applied and changes
            # nothing, so it is refused rather than stored. Merged rather than
            # replaced, because a write names only the part it changes.
            # Imported here because the reply policy service reads this one.
            from backend.services.reply_policy_service import reply_policy_service

            merged = reply_policy_service.merge_section(
                current["values"],
                values,
                company_id=company_id,
            )

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                """
                INSERT INTO company_settings (
                    company_id, section, settings_json,
                    updated_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, section)
                DO UPDATE SET
                    settings_json = excluded.settings_json,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    company_id,
                    normalized,
                    json.dumps(merged, ensure_ascii=False),
                    actor_user_id,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO company_setting_audit (
                    company_id, section, actor_user_id,
                    old_value_json, new_value_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    normalized,
                    actor_user_id,
                    json.dumps(current["values"], ensure_ascii=False),
                    json.dumps(merged, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()

        # Setting keys only. Values can hold workspace codes and other secrets.
        logger.info(
            "Settings updated company id=%s section=%s keys=%s actor id=%s",
            company_id,
            normalized,
            sorted(values),
            actor_user_id,
        )
        return self.get_section(company_id, normalized)


company_settings_service = CompanySettingsService()
