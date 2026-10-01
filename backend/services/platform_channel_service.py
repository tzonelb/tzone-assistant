"""Channel credentials the platform itself holds, not any one company.

The Super Admin's own "Channels" page: a Meta developer account's keys,
pasted once here rather than by every company individually. Sealed under
the platform's own master key -- there is no company to seal them under,
the same reason `keyring.seal_user_secret` exists at all (a Super Admin's
own TOTP secret is the other thing sealed that way).

Two separate decisions, deliberately not one:

* **Whether a channel is configured at all** -- `has_credentials` below,
  which is what the operator's own words ("their presence activates the
  platform and it goes online") mean in this codebase. `channels/
  credentials.py`'s `resolve()` is the one place that acts on it.
* **Which companies may reach it** -- `company_channel_access`, checked by
  `company_has_access` below. Configuring a credential here grants no
  company anything by itself; a company sees nothing until the Super Admin
  explicitly turns it on for that company, the same default-closed shape
  `company_platform_config`'s own module flags take.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.security import keyring
from backend.security.keyring import CorruptedKeyMaterial
from database.manager import database_manager


logger = logging.getLogger(__name__)

# Only the official, Meta-developer-app-keyed channels take a platform-level
# credential. The unofficial ones (instagram_direct, facebook_direct,
# whatsapp_qr) authenticate with one company's own session, never a shared
# developer app, so there is nothing here for them to hold.
#
# "instagram" has no row of its own: a Facebook app's OAuth flow is what
# connects both Messenger Pages and the Instagram accounts linked to them
# in one approval (see `channel_oauth.py`'s own docstring) -- one Meta app,
# one credential, filed under "messenger". Listing "instagram" here too
# would just be the same app id and secret typed a second time.
PLATFORM_KEYED_CHANNELS = ("messenger", "whatsapp")

# A reserved id -- never a real user's, since those start at 1 -- that
# binds every platform-level credential's sealing to "the platform itself"
# rather than to any one account. `context` (the channel name) is what
# tells two channels' sealed secrets apart under this same binding, the
# same role `context` plays in `channel_accounts`' own sealed columns.
_PLATFORM_SEAL_ID = 0

# Deliberately just the app's own identity -- an access token is never one
# of these fields, for either channel, and that is a real design choice,
# not an oversight: a Messenger/Instagram access token is issued per Page
# through the OAuth flow this credential enables (see `meta_oauth_service.
# _app_credentials`), so there is no single platform-wide one that would
# mean anything. A WhatsApp access token is scoped per phone number the
# same way -- each company still connects its own, on the Channels screen,
# exactly as it does today. What a platform-level credential is *for* is
# turning the "Log in with Facebook" button on at all, and, per company,
# for whom -- see `company_channel_access` below.
FIELD_SPECS: dict[str, dict[str, tuple[str, ...]]] = {
    "messenger": {
        "secret_fields": ("app_secret",),
        "config_fields": ("app_id",),
    },
    "whatsapp": {
        "secret_fields": ("app_secret",),
        "config_fields": ("app_id",),
    },
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlatformChannelError(RuntimeError):
    """A platform-level channel credential or access grant could not be saved."""


class PlatformChannelService:
    @staticmethod
    def _validate_channel(channel: str) -> str:
        normalized = str(channel or "").strip().lower()

        if normalized not in PLATFORM_KEYED_CHANNELS:
            raise PlatformChannelError(
                f"'{channel}' has no platform-level credential. "
                f"Only {', '.join(PLATFORM_KEYED_CHANNELS)} do."
            )

        return normalized

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    def set_credentials(
        self, *, channel: str, values: dict[str, Any], actor_user_id: int
    ) -> dict[str, Any]:
        """Replace this channel's platform-level credential whole.

        Every secret field is required together, not merged with whatever
        was stored before -- pasting a fresh Meta developer app's keys is a
        complete replacement, and a partial one would leave an old access
        token paired with a new app secret with no way for the operator to
        see that mismatch happened.
        """
        normalized = self._validate_channel(channel)
        spec = FIELD_SPECS[normalized]

        secret_payload = {
            field: str(values[field]).strip()
            for field in spec["secret_fields"]
            if values.get(field)
        }

        missing = [f for f in spec["secret_fields"] if not secret_payload.get(f)]

        if missing:
            raise PlatformChannelError(
                f"A {normalized} credential needs: {', '.join(spec['secret_fields'])}."
            )

        config_payload = {
            field: str(values[field]).strip()
            for field in spec["config_fields"]
            if values.get(field)
        }

        sealed = keyring.seal_user_secret(
            json.dumps(secret_payload),
            keyring.load_master_key(),
            _PLATFORM_SEAL_ID,
            normalized,
        )
        now = utc_now_iso()

        with database_manager.control() as conn:
            conn.execute(
                """
                INSERT INTO platform_channel_credentials (
                    channel, config_json, sealed_secret, updated_by_user_id,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel) DO UPDATE SET
                    config_json = excluded.config_json,
                    sealed_secret = excluded.sealed_secret,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized,
                    json.dumps(config_payload),
                    sealed,
                    int(actor_user_id),
                    now,
                    now,
                ),
            )
            conn.commit()

        logger.info("Platform credentials set for channel %s", normalized)

        return self.status_for(normalized)

    def clear_credentials(self, *, channel: str, actor_user_id: int) -> None:
        normalized = self._validate_channel(channel)

        with database_manager.control() as conn:
            conn.execute(
                "DELETE FROM platform_channel_credentials WHERE channel = ?",
                (normalized,),
            )
            conn.commit()

        logger.info(
            "Platform credentials for channel %s cleared by user %s",
            normalized,
            actor_user_id,
        )

    def has_credentials(self, channel: str) -> bool:
        normalized = str(channel or "").strip().lower()

        if normalized not in PLATFORM_KEYED_CHANNELS:
            return False

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT 1 FROM platform_channel_credentials WHERE channel = ?",
                (normalized,),
            ).fetchone()

        return row is not None

    def get_credentials(self, channel: str) -> dict[str, Any] | None:
        """The unsealed credential, shaped like `channel_account_service.
        credentials_for()`'s own return -- for `channels/credentials.py`
        alone. Never sent to a browser: see `status_for` for the public,
        secret-free view this backs the Channels page with instead.
        """
        normalized = self._validate_channel(channel)

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT config_json, sealed_secret FROM platform_channel_credentials "
                "WHERE channel = ?",
                (normalized,),
            ).fetchone()

        if not row:
            return None

        try:
            config = json.loads(row["config_json"] or "{}")
        except (TypeError, ValueError):
            config = {}

        try:
            secret_json = keyring.unseal_user_secret(
                row["sealed_secret"], keyring.load_master_key(), _PLATFORM_SEAL_ID, normalized
            )
            secrets = json.loads(secret_json)
        except (CorruptedKeyMaterial, TypeError, ValueError):
            logger.error(
                "Platform credentials for channel %s could not be unsealed",
                normalized,
            )
            return None

        return {**config, **secrets}

    def status_for(self, channel: str) -> dict[str, Any]:
        """The Channels page's own view of one channel: whether it is
        configured, and its non-secret fields only."""
        normalized = self._validate_channel(channel)

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT config_json, updated_at FROM platform_channel_credentials "
                "WHERE channel = ?",
                (normalized,),
            ).fetchone()

        if not row:
            return {"channel": normalized, "configured": False, "config": {}, "updated_at": None}

        try:
            config = json.loads(row["config_json"] or "{}")
        except (TypeError, ValueError):
            config = {}

        return {
            "channel": normalized,
            "configured": True,
            "config": config,
            "updated_at": row["updated_at"],
        }

    def list_status(self) -> list[dict[str, Any]]:
        return [self.status_for(channel) for channel in PLATFORM_KEYED_CHANNELS]

    # ------------------------------------------------------------------
    # Per-company access
    # ------------------------------------------------------------------

    def set_company_access(
        self, *, company_id: int, channel: str, enabled: bool, actor_user_id: int
    ) -> None:
        normalized = self._validate_channel(channel)
        now = utc_now_iso()

        with database_manager.control() as conn:
            company = conn.execute(
                "SELECT id FROM companies WHERE id = ?", (int(company_id),)
            ).fetchone()

            if not company:
                raise PlatformChannelError(f"No company with id {company_id}.")

            conn.execute(
                """
                INSERT INTO company_channel_access (
                    company_id, channel, enabled, updated_by_user_id,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, channel) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (int(company_id), normalized, 1 if enabled else 0, int(actor_user_id), now, now),
            )
            conn.commit()

    def company_has_access(self, company_id: int, channel: str) -> bool:
        normalized = str(channel or "").strip().lower()

        if normalized not in PLATFORM_KEYED_CHANNELS:
            return False

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT enabled FROM company_channel_access WHERE company_id = ? AND channel = ?",
                (int(company_id), normalized),
            ).fetchone()

        return bool(row and row["enabled"])

    def access_grid_for_channel(self, channel: str) -> list[dict[str, Any]]:
        """Every company on the platform, with whether this channel is
        turned on for it -- what the Channels page's own toggle grid is
        built from. Every company appears, including ones with no row yet
        in `company_channel_access`: a missing row is "not granted", the
        same default-closed reading `company_has_access` gives it."""
        normalized = self._validate_channel(channel)

        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT
                    companies.id AS company_id,
                    companies.name AS company_name,
                    companies.status AS company_status,
                    COALESCE(company_channel_access.enabled, 0) AS enabled
                FROM companies
                LEFT JOIN company_channel_access
                       ON company_channel_access.company_id = companies.id
                      AND company_channel_access.channel = ?
                ORDER BY companies.name COLLATE NOCASE
                """,
                (normalized,),
            ).fetchall()

        return [
            {
                "company_id": int(row["company_id"]),
                "company_name": row["company_name"],
                "company_status": row["company_status"],
                "enabled": bool(row["enabled"]),
            }
            for row in rows
        ]


platform_channel_service = PlatformChannelService()
