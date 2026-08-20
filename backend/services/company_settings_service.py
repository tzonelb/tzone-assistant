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
from zoneinfo import ZoneInfo

from database.manager import database_manager
from database.schema_tenant import DEFAULT_SETTINGS


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Distinguishes "leave the pinned value alone" from "pin the value None".
# `None` is a legitimate thing to pin, so it cannot double as the sentinel.
_UNSET = object()

# One catalogue, owned by the layer that seeds it. This module used to declare
# its own, and the two had drifted so far apart that the keys seeded into every
# company's database — `working_hours`, `reply_language` and three `notify_on_*`
# preferences — were not merely unread but unreachable: `get_section` never
# returned them and `update_section` dropped any write that named them.
#
# Importing it is what stops that recurring. Seeding and serving can no longer
# disagree without one of them failing to import.


# Settings that used to exist and no longer decide anything, by section. Listed
# so a company that stored one before it was retired stops being shown it, and
# so retiring one is a line somebody writes rather than a key quietly vanishing
# from a literal.
RETIRED_SETTINGS: dict[str, frozenset[str]] = {
    "ai_behavior": frozenset(
        {
            # Duplicated `fallback_to_human` in the reply policy, where the
            # decision is made per channel and per department and is enforced.
            "escalate_on_low_confidence",
            # Duplicated `welcome_enabled` and `welcome_mode` in the reply
            # policy.
            "welcome_immediate",
            # Duplicated `collect_message_delay_seconds` two keys above it,
            # which is the buffer that waits for a customer to stop typing.
            "reply_only_when_customer_stops_typing",
        }
    ),
    # Browser preferences that the browser already keeps per user, in
    # `frontend/src/utils/notificationPreferences.js`. Whether a sound plays is
    # one person's choice on one machine, not a company-wide setting, and the
    # server copies were read by nothing.
    "notifications": frozenset(
        {"ai_replied", "employee_replied", "in_app_popup", "desktop", "sound"}
    ),
}


def _assert_working_hours_are_usable(values: dict[str, Any]) -> None:
    """Refuse working hours the engine cannot act on.

    The engine treats an unreadable timezone as **open** — deliberately, so a
    bad row can never silence a company's assistant. That is right for data
    already stored, and it is exactly why the *write* has to be strict: a
    mistyped zone was accepted with a 200, shown back on the screen as saved,
    and left the assistant answering customers at three in the morning for
    ever. The only trace was one line in a log nobody reads.

    Refuse at the write, tolerate at the read — the same shape as the branch
    and section checks.
    """
    timezone_name = str(values.get("timezone") or "").strip()

    if timezone_name:
        try:
            ZoneInfo(timezone_name)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"{timezone_name!r} is not a timezone this server knows. Use an "
                "IANA name such as Asia/Beirut or Europe/Paris."
            ) from exc

    days = values.get("days")

    if days is None:
        return

    if not isinstance(days, dict):
        raise ValueError("Working hours must name each day.")

    for day, window in days.items():
        if not isinstance(window, dict):
            raise ValueError(f"The hours for {day} are not readable.")

        for edge in ("open", "close"):
            clock = window.get(edge)

            if clock in (None, ""):
                continue

            try:
                hour, _, minute = str(clock).partition(":")
                hour, minute = int(hour), int(minute)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"The {edge} time for {day} must look like 09:00."
                ) from exc

            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(
                    f"The {edge} time for {day} is not a time of day."
                )


# Settings whose value is a number with a meaningful range, and what that range
# is. Each of these was accepted at any value and then clamped when it was
# read, so an owner could save -5, see -5 on the screen, and get the behaviour
# of 1. A setting that displays one thing and does another is the defect this
# audit has closed everywhere else; it is the same one when the disagreement is
# with itself.
_AI_BEHAVIOUR_RANGES: dict[str, tuple[int, int]] = {
    "return_to_ai_timeout_minutes": (1, 1440),
    "collect_message_delay_seconds": (0, 300),
}


def _assert_ai_behaviour_is_in_range(values: dict[str, Any]) -> None:
    for key, (low, high) in _AI_BEHAVIOUR_RANGES.items():
        if key not in values or values[key] is None:
            continue

        try:
            number = int(values[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a whole number.") from exc

        if not low <= number <= high:
            raise ValueError(
                f"{key} must be between {low} and {high}. It was {number}, "
                "which the assistant would have silently treated as "
                f"{min(max(number, low), high)}."
            )


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
        # Validated here, not only on the Super Admin override paths. The
        # section name arrives from the URL — `GET/PUT /api/settings/{section}`
        # — and `_normalize_section` existed but was wired only into
        # `set_override` and `clear_override`. The reasoning had been done and
        # applied to the neighbouring pair of methods, which is the same shape
        # as the `branch_id` leak: the check sat next to the field that needed
        # it and was not extended to it.
        normalized = self._normalize_section(section)
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
            # A retired key stays in every company's stored JSON for ever, so
            # dropping it from `DEFAULT_SETTINGS` would reach new companies
            # only and the screen would go on offering the old switch to
            # everybody already running. It is filtered out on the way past.
            #
            # By name, not by absence from the defaults. The first version of
            # this dropped every stored key the defaults did not mention, which
            # sounded equivalent and was not: `ai_behavior.channels` is sparse
            # per-channel configuration that deliberately has no default, and
            # deleting it silently turned off per-channel AI mode for every
            # company. Retiring a setting is a decision somebody makes; it is
            # named here rather than inferred.
            values = {
                **defaults,
                **{
                    key: value
                    for key, value in stored.items()
                    if key not in RETIRED_SETTINGS.get(normalized, ())
                },
            }
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
        # Unvalidated, this stored a row under whatever name was in the URL. No
        # screen ever read it back, so nothing looked wrong — the visible cost
        # was one company's settings table and its settings-history log growing
        # by a row and up to a couple of hundred kilobytes per request, from a
        # door that only needs `settings.manage`.
        normalized = self._normalize_section(section)
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

        if normalized == "working_hours":
            _assert_working_hours_are_usable(merged)

        if normalized == "ai_behavior":
            _assert_ai_behaviour_is_in_range(merged)

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

    # ------------------------------------------------------------------
    # Operator overrides
    #
    # `super_admin_setting_overrides` has been read by `get_section` since the
    # table shipped — it pins a value and can mark a key locked, and
    # `update_section` refuses to write a locked key. Nothing ever wrote a row.
    # The feature existed on the read side, was enforced on the write side, and
    # was unreachable: every company's `locked_keys` was `[]` for ever, because
    # there was no way to put anything in it.
    # ------------------------------------------------------------------

    def set_override(
        self,
        *,
        company_id: int,
        section: str,
        setting_key: str,
        value: Any = _UNSET,
        is_locked: bool | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Pin a setting for one company, lock it, or both.

        `value` and `is_locked` are independent on purpose. An operator may want
        to lock a company to whatever it has already chosen — a support
        agreement, a compliance requirement — without deciding the value for
        them; and may want to correct a value without taking the control away.
        Forcing both would make the gentler action impossible.

        Omitting `value` leaves the stored pin untouched, which is why the
        default is a sentinel rather than `None`: `None` is a legitimate value
        to pin.
        """
        company_id = int(company_id)
        normalized = self._normalize_section(section)
        key = str(setting_key or "").strip()

        if not key:
            raise ValueError("An override needs a setting key.")

        defaults = self._defaults_for(company_id, normalized)

        # Refused rather than stored. A key no section defines would sit in the
        # table for ever, pinning nothing and locking nothing, while the console
        # showed it as applied — the same shape as every "setting that saved and
        # did nothing" this codebase has been unpicking.
        if defaults and key not in defaults:
            raise ValueError(
                f"{key!r} is not a setting in the {normalized!r} section. "
                f"Valid keys are: {', '.join(sorted(defaults))}."
            )

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            existing = conn.execute(
                """
                SELECT value_json, is_locked FROM super_admin_setting_overrides
                WHERE company_id = ? AND section = ? AND setting_key = ?
                LIMIT 1
                """,
                (company_id, normalized, key),
            ).fetchone()

            value_json = (
                json.dumps(value, ensure_ascii=False, default=str)
                if value is not _UNSET
                else (existing["value_json"] if existing else None)
            )
            locked = (
                int(bool(is_locked))
                if is_locked is not None
                else (int(existing["is_locked"]) if existing else 0)
            )

            conn.execute(
                """
                INSERT INTO super_admin_setting_overrides (
                    company_id, section, setting_key, value_json, is_locked,
                    updated_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, section, setting_key)
                DO UPDATE SET
                    value_json = excluded.value_json,
                    is_locked = excluded.is_locked,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    company_id,
                    normalized,
                    key,
                    value_json,
                    locked,
                    actor_user_id,
                    now,
                    now,
                ),
            )
            conn.commit()

        logger.info(
            "Override set company id=%s section=%s key=%s locked=%s actor id=%s",
            company_id,
            normalized,
            key,
            bool(locked),
            actor_user_id,
        )

        return self.get_section(company_id, normalized)

    def clear_override(
        self,
        *,
        company_id: int,
        section: str,
        setting_key: str,
    ) -> dict[str, Any]:
        """Hand the setting back to the company.

        Deleting the row rather than setting `is_locked = 0` and blanking the
        value: a row that pins nothing and locks nothing is a row that says an
        override exists when none does, and `list_overrides` would keep
        reporting it.
        """
        company_id = int(company_id)
        normalized = self._normalize_section(section)

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                """
                DELETE FROM super_admin_setting_overrides
                WHERE company_id = ? AND section = ? AND setting_key = ?
                """,
                (company_id, normalized, str(setting_key or "").strip()),
            )
            conn.commit()

        return self.get_section(company_id, normalized)

    def list_overrides(self, company_id: int) -> list[dict[str, Any]]:
        """Every override on this company, for the console to show and undo."""
        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                """
                SELECT section, setting_key, value_json, is_locked,
                       updated_by_user_id, updated_at
                FROM super_admin_setting_overrides
                WHERE company_id = ?
                ORDER BY section, setting_key
                """,
                (int(company_id),),
            ).fetchall()

        overrides = []

        for row in rows:
            entry = dict(row)
            entry["value"] = self._loads(entry.pop("value_json", None), None)
            entry["is_locked"] = bool(entry["is_locked"])
            overrides.append(entry)

        return overrides

    def _normalize_section(self, section: str) -> str:
        normalized = str(section or "").strip().lower()

        if normalized not in DEFAULT_SETTINGS:
            raise ValueError(
                f"{normalized!r} is not a settings section. "
                f"Valid sections are: {', '.join(sorted(DEFAULT_SETTINGS))}."
            )

        return normalized


company_settings_service = CompanySettingsService()
