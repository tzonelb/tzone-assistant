"""Per-company settings, Super Admin overrides and the change audit.

All three tables live in the company's own encrypted database. `actor_user_id`
and `updated_by_user_id` point at control-plane users and are stored as plain
integers; names are resolved through `auth_service.user_display_names` when they
are needed, because SQLite cannot join across two files.

Table creation belongs to `database/schema_tenant.py` alone.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from database.manager import database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_SETTINGS: dict[str, Any] = {
    "company_profile": {
        "company_name": "T-ZONE",
        "workspace_code": "tzone",
        "timezone": "Asia/Beirut",
        "default_language": "ar",
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
    "modules": {
        "appointments": False,
        "scheduler": True,
        "catalogue": True,
        "team_chat": True,
        "comments": True,
    },
}


class CompanySettingsService:
    @staticmethod
    def _loads(value: str | None, fallback: Any) -> Any:
        try:
            parsed = json.loads(value or "")
            return parsed
        except (json.JSONDecodeError, TypeError):
            return fallback

    def get_section(self, company_id: int, section: str) -> dict[str, Any]:
        company_id = int(company_id)
        normalized = section.strip().lower()
        defaults = DEFAULT_SETTINGS.get(normalized, {})

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
        values = {**defaults, **stored}
        locked: list[str] = []

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
