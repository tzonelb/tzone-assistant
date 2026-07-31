import json
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_SETTINGS: dict[str, Any] = {
    "company_profile": {
        "company_name": "T-ZONE",
        "workspace_code": "tzone",
        "timezone": "Asia/Beirut",
        "default_language": "ar",
        "logo_url": "",
        "business_hours": {
            "sunday": {"open": True, "from": "09:00", "to": "18:00"},
            "monday": {"open": True, "from": "09:00", "to": "18:00"},
            "tuesday": {"open": True, "from": "09:00", "to": "18:00"},
            "wednesday": {"open": True, "from": "09:00", "to": "18:00"},
            "thursday": {"open": True, "from": "09:00", "to": "18:00"},
            "friday": {"open": False, "from": "09:00", "to": "18:00"},
            "saturday": {"open": False, "from": "09:00", "to": "18:00"},
        },
    },
    "ai_behavior": {
        "enabled": True,
        "mode": "ai_first",
        "greeting_message": "",
        "collect_message_delay_seconds": 20,
        "return_to_ai_timeout_minutes": 5,
        "reply_access_mode": "take_required",
        # Auto-read is always on and is no longer an editable toggle. It is
        # force-defaulted to True on read (see get_section) so the underlying
        # read-marking behaviour stays enabled regardless of any stored value.
        "auto_read": True,
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
    def __init__(self) -> None:
        # Schema setup happens explicitly via main.py's lifespan (after
        # database.database.db.create_tables()), not here — see the
        # matching note in ConversationControlService.__init__.
        pass

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS company_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    section TEXT NOT NULL,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    updated_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(company_id, section),
                    FOREIGN KEY(company_id)
                        REFERENCES companies(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(updated_by_user_id)
                        REFERENCES users(id)
                        ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS company_setting_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    section TEXT NOT NULL,
                    actor_user_id INTEGER,
                    old_value_json TEXT,
                    new_value_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(company_id)
                        REFERENCES companies(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id)
                        REFERENCES users(id)
                        ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS super_admin_setting_overrides (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    section TEXT NOT NULL,
                    setting_key TEXT NOT NULL,
                    value_json TEXT,
                    is_locked INTEGER NOT NULL DEFAULT 0,
                    updated_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(company_id, section, setting_key),
                    FOREIGN KEY(company_id)
                        REFERENCES companies(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(updated_by_user_id)
                        REFERENCES users(id)
                        ON DELETE SET NULL
                )
                """
            )
            conn.commit()

    @staticmethod
    def _loads(value: str | None, fallback: Any) -> Any:
        try:
            parsed = json.loads(value or "")
            return parsed
        except (json.JSONDecodeError, TypeError):
            return fallback

    def get_section(self, company_id: int, section: str) -> dict[str, Any]:
        normalized = section.strip().lower()
        defaults = DEFAULT_SETTINGS.get(normalized, {})

        with db.connect() as conn:
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

        if normalized == "ai_behavior":
            # Auto-read is always-on: force True regardless of what is stored
            # (or if the key is absent). The visible toggle was removed; this
            # keeps the read-marking behaviour permanently enabled.
            values["auto_read"] = True
            # Drop the retired mode field so it never resurfaces in the UI.
            values.pop("auto_read_mode", None)

        return {
            "section": normalized,
            "values": values,
            "locked_keys": sorted(set(locked)),
        }

    def is_auto_read_enabled(self, company_id: int) -> bool:
        """Auto-read is a permanent, always-on behaviour.

        Consumers should call this instead of reading a stored toggle. The
        toggle was removed from the UI; the behaviour is never disabled.
        """
        return True

    def get_all(self, company_id: int) -> dict[str, Any]:
        sections = set(DEFAULT_SETTINGS)
        with db.connect() as conn:
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

        with db.connect() as conn:
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

        return self.get_section(company_id, normalized)


company_settings_service = CompanySettingsService()
