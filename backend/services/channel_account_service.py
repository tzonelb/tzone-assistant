from datetime import datetime, timezone
from typing import Any

import requests

from backend.services.crypto_utils import encrypt_secret, decrypt_secret
from backend.services.platform_admin_service import platform_admin_service
from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChannelAccountError(Exception):
    pass


class ChannelAccountService:
    def list_for_company(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, company_id, branch_id, channel, name, external_account_id,
                       phone_number_id, page_id, instagram_business_id, status,
                       ai_enabled, voice_ai_enabled, image_ai_enabled, created_at, updated_at
                FROM channel_accounts
                WHERE company_id = ?
                ORDER BY created_at DESC
                """,
                (company_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _count_active(self, *, company_id: int) -> int:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM channel_accounts WHERE company_id = ? AND status = 'active'",
                (company_id,),
            ).fetchone()
        return row["total"]

    def _assert_within_channel_limit(self, *, company_id: int) -> None:
        limits = platform_admin_service.get_active_subscription_limits(company_id=company_id)
        if limits is None:
            return  # No plan configured yet — super admin hasn't set one, don't block.
        current = self._count_active(company_id=company_id)
        if current >= limits["max_channel_accounts"]:
            raise ChannelAccountError(
                f"This company's plan ({limits['name']}) allows up to "
                f"{limits['max_channel_accounts']} connected channels. "
                f"Contact your platform administrator to upgrade."
            )

    @staticmethod
    def _assert_not_owned_by_another_company(
        conn, *, company_id: int, channel: str, column: str, value: str
    ) -> None:
        """SECURITY: a page/phone/IG/bot identity may only be connected to
        ONE company. Without this check, any company could 'connect' an
        identity that already belongs to another tenant — and because
        inbound webhook routing resolves the owning company by this very
        identity (resolve_meta_account), that would silently reroute or
        split the other company's customer messages. Cross-tenant
        takeover, not a convenience issue. Checks the identity globally
        (any status except disabled) rather than within the caller's own
        company like the duplicate checks below."""
        row = conn.execute(
            f"SELECT company_id FROM channel_accounts "
            f"WHERE channel = ? AND {column} = ? AND status != 'disabled' "
            f"LIMIT 1",
            (channel, value),
        ).fetchone()
        if row and int(row["company_id"]) != int(company_id):
            raise ChannelAccountError(
                "This account is already connected to another company on "
                "this platform. If you believe this is a mistake, contact "
                "your platform administrator."
            )

    def connect_telegram(self, *, company_id: int, bot_token: str, name: str | None = None) -> dict[str, Any]:
        bot_token = (bot_token or "").strip()
        if not bot_token:
            raise ChannelAccountError("Bot token is required.")

        # Validate the token actually works before storing anything —
        # this is the "connect page" experience: instant feedback, not a
        # silent save that might be wrong.
        try:
            response = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
            data = response.json()
        except requests.RequestException as exc:
            raise ChannelAccountError(f"Could not reach Telegram to verify this token: {exc}")

        if not data.get("ok"):
            raise ChannelAccountError(
                f"Telegram rejected this token: {data.get('description', 'unknown error')}"
            )

        bot_info = data["result"]
        external_account_id = str(bot_info["id"])
        bot_username = bot_info.get("username", "")

        self._assert_within_channel_limit(company_id=company_id)

        with db.connect() as conn:
            self._assert_not_owned_by_another_company(
                conn, company_id=company_id, channel="telegram",
                column="external_account_id", value=external_account_id,
            )
            existing = conn.execute(
                "SELECT id FROM channel_accounts WHERE company_id = ? AND channel = 'telegram' "
                "AND external_account_id = ?",
                (company_id, external_account_id),
            ).fetchone()
            if existing:
                raise ChannelAccountError("This Telegram bot is already connected to this company.")

            now = utc_now_iso()
            display_name = name or (f"@{bot_username}" if bot_username else "Telegram Bot")
            cursor = conn.execute(
                """
                INSERT INTO channel_accounts (
                    company_id, channel, name, external_account_id,
                    access_token_encrypted, status, ai_enabled, created_at, updated_at
                ) VALUES (?, 'telegram', ?, ?, ?, 'active', 1, ?, ?)
                """,
                (
                    company_id,
                    display_name,
                    external_account_id,
                    encrypt_secret(bot_token),
                    now,
                    now,
                ),
            )
            channel_account_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_account(account_id=channel_account_id)

    def get_account(self, *, account_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT id, company_id, branch_id, channel, name, external_account_id, "
                "phone_number_id, page_id, instagram_business_id, status, "
                "ai_enabled, voice_ai_enabled, image_ai_enabled, created_at, updated_at "
                "FROM channel_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        if not row:
            raise KeyError("Channel account not found")
        return dict(row)

    def get_decrypted_token(self, *, account_id: int) -> str:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT access_token_encrypted FROM channel_accounts WHERE id = ?", (account_id,),
            ).fetchone()
        if not row or not row["access_token_encrypted"]:
            raise KeyError("No token stored for this channel account")
        return decrypt_secret(row["access_token_encrypted"])

    def list_active_telegram_accounts(self) -> list[dict[str, Any]]:
        """Used at app startup / on new connection to know which bots to
        run polling for — across every company."""
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, company_id, name, external_account_id FROM channel_accounts "
                "WHERE channel = 'telegram' AND status = 'active'"
            ).fetchall()
        return [dict(row) for row in rows]

    def connect_whatsapp(self, *, company_id: int, phone_number_id: str, access_token: str, name: str | None = None) -> dict[str, Any]:
        phone_number_id = (phone_number_id or "").strip()
        access_token = (access_token or "").strip()
        if not phone_number_id or not access_token:
            raise ChannelAccountError("Phone number ID and access token are both required.")

        try:
            response = requests.get(
                f"https://graph.facebook.com/v19.0/{phone_number_id}",
                params={"fields": "display_phone_number,verified_name", "access_token": access_token},
                timeout=10,
            )
            data = response.json()
        except requests.RequestException as exc:
            raise ChannelAccountError(f"Could not reach WhatsApp/Meta to verify this: {exc}")

        if "error" in data:
            raise ChannelAccountError(f"Meta rejected this: {data['error'].get('message', 'unknown error')}")

        self._assert_within_channel_limit(company_id=company_id)

        with db.connect() as conn:
            self._assert_not_owned_by_another_company(
                conn, company_id=company_id, channel="whatsapp",
                column="phone_number_id", value=phone_number_id,
            )
            existing = conn.execute(
                "SELECT id FROM channel_accounts WHERE company_id = ? AND channel = 'whatsapp' AND phone_number_id = ?",
                (company_id, phone_number_id),
            ).fetchone()
            if existing:
                raise ChannelAccountError("This WhatsApp number is already connected to this company.")

            now = utc_now_iso()
            display_name = name or data.get("verified_name") or data.get("display_phone_number") or "WhatsApp"
            cursor = conn.execute(
                """
                INSERT INTO channel_accounts (
                    company_id, channel, name, phone_number_id,
                    access_token_encrypted, status, ai_enabled, created_at, updated_at
                ) VALUES (?, 'whatsapp', ?, ?, ?, 'active', 1, ?, ?)
                """,
                (company_id, display_name, phone_number_id, encrypt_secret(access_token), now, now),
            )
            channel_account_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_account(account_id=channel_account_id)

    def connect_messenger(self, *, company_id: int, page_id: str, access_token: str, name: str | None = None) -> dict[str, Any]:
        page_id = (page_id or "").strip()
        access_token = (access_token or "").strip()
        if not page_id or not access_token:
            raise ChannelAccountError("Page ID and access token are both required.")

        try:
            response = requests.get(
                f"https://graph.facebook.com/v19.0/{page_id}",
                params={"fields": "id,name", "access_token": access_token},
                timeout=10,
            )
            data = response.json()
        except requests.RequestException as exc:
            raise ChannelAccountError(f"Could not reach Messenger/Meta to verify this: {exc}")

        if "error" in data:
            raise ChannelAccountError(f"Meta rejected this: {data['error'].get('message', 'unknown error')}")

        self._assert_within_channel_limit(company_id=company_id)

        with db.connect() as conn:
            self._assert_not_owned_by_another_company(
                conn, company_id=company_id, channel="messenger",
                column="page_id", value=page_id,
            )
            existing = conn.execute(
                "SELECT id FROM channel_accounts WHERE company_id = ? AND channel = 'messenger' AND page_id = ?",
                (company_id, page_id),
            ).fetchone()
            if existing:
                raise ChannelAccountError("This Facebook Page is already connected to this company.")

            now = utc_now_iso()
            display_name = name or data.get("name") or "Messenger"
            cursor = conn.execute(
                """
                INSERT INTO channel_accounts (
                    company_id, channel, name, page_id,
                    access_token_encrypted, status, ai_enabled, created_at, updated_at
                ) VALUES (?, 'messenger', ?, ?, ?, 'active', 1, ?, ?)
                """,
                (company_id, display_name, page_id, encrypt_secret(access_token), now, now),
            )
            channel_account_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_account(account_id=channel_account_id)

    def connect_instagram(self, *, company_id: int, page_id: str, access_token: str, name: str | None = None) -> dict[str, Any]:
        page_id = (page_id or "").strip()
        access_token = (access_token or "").strip()
        if not page_id or not access_token:
            raise ChannelAccountError("Page ID and access token are both required.")

        try:
            response = requests.get(
                f"https://graph.facebook.com/v19.0/{page_id}",
                params={"fields": "id,name,instagram_business_account", "access_token": access_token},
                timeout=10,
            )
            data = response.json()
        except requests.RequestException as exc:
            raise ChannelAccountError(f"Could not reach Instagram/Meta to verify this: {exc}")

        if "error" in data:
            raise ChannelAccountError(f"Meta rejected this: {data['error'].get('message', 'unknown error')}")

        instagram_business_id = (data.get("instagram_business_account") or {}).get("id")
        if not instagram_business_id:
            raise ChannelAccountError(
                "This Page doesn't have an Instagram professional account connected to it in Meta yet."
            )

        self._assert_within_channel_limit(company_id=company_id)

        with db.connect() as conn:
            self._assert_not_owned_by_another_company(
                conn, company_id=company_id, channel="instagram",
                column="instagram_business_id", value=instagram_business_id,
            )
            existing = conn.execute(
                "SELECT id FROM channel_accounts WHERE company_id = ? AND channel = 'instagram' AND instagram_business_id = ?",
                (company_id, instagram_business_id),
            ).fetchone()
            if existing:
                raise ChannelAccountError("This Instagram account is already connected to this company.")

            now = utc_now_iso()
            display_name = name or data.get("name") or "Instagram"
            cursor = conn.execute(
                """
                INSERT INTO channel_accounts (
                    company_id, channel, name, page_id, instagram_business_id,
                    access_token_encrypted, status, ai_enabled, created_at, updated_at
                ) VALUES (?, 'instagram', ?, ?, ?, ?, 'active', 1, ?, ?)
                """,
                (company_id, display_name, page_id, instagram_business_id, encrypt_secret(access_token), now, now),
            )
            channel_account_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_account(account_id=channel_account_id)

    def resolve_meta_account(self, *, recipient_id: str, channel: str) -> dict[str, Any] | None:
        """At webhook-receive time: which company owns this page/phone/IG
        account? recipient_id is OUR OWN page/phone/IG id from the
        incoming payload (who the message was sent to), not the customer."""
        if not recipient_id:
            return None
        column = {
            "messenger": "page_id",
            "instagram": "instagram_business_id",
            "whatsapp": "phone_number_id",
        }.get(channel)
        if not column:
            return None

        with db.connect() as conn:
            row = conn.execute(
                f"SELECT company_id, access_token_encrypted FROM channel_accounts "
                f"WHERE channel = ? AND {column} = ? AND status = 'active' LIMIT 1",
                (channel, recipient_id),
            ).fetchone()
        if not row:
            return None
        return {
            "company_id": row["company_id"],
            "access_token": decrypt_secret(row["access_token_encrypted"]) if row["access_token_encrypted"] else None,
        }

    def get_active_token(self, *, company_id: int, channel: str) -> str | None:
        """At send time: which token should we use to reply as this
        company on this channel?"""
        with db.connect() as conn:
            row = conn.execute(
                "SELECT access_token_encrypted FROM channel_accounts "
                "WHERE company_id = ? AND channel = ? AND status = 'active' "
                "ORDER BY created_at DESC LIMIT 1",
                (company_id, channel),
            ).fetchone()
        if not row or not row["access_token_encrypted"]:
            return None
        return decrypt_secret(row["access_token_encrypted"])

    def disconnect(self, *, company_id: int, account_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM channel_accounts WHERE id = ? AND company_id = ?",
                (account_id, company_id),
            ).fetchone()
            if not existing:
                raise KeyError("Channel account not found")
            conn.execute(
                "UPDATE channel_accounts SET status = 'disabled', updated_at = ? WHERE id = ?",
                (utc_now_iso(), account_id),
            )
            conn.commit()
        return self.get_account(account_id=account_id)


channel_account_service = ChannelAccountService()
