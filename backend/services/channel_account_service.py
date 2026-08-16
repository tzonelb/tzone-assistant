"""Connecting a Facebook Page, Instagram account or WhatsApp number to a company.

This is what makes the platform genuinely multi-company. Inbound routing looks a
message up by the account it arrived on, and outbound sending uses that
account's own token — so two companies on the same server answer their own
customers from their own pages.

Records live in the control database because a webhook must be routed before we
know which company it belongs to. The credentials on them are sealed under the
owning company's database key, so the control database holds no usable secret.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.security import keyring
from backend.security.keyring import CorruptedKeyMaterial
from database.manager import database_manager


logger = logging.getLogger(__name__)


SUPPORTED_CHANNELS = ("messenger", "instagram", "whatsapp")

# Which identifier each channel is routed by. Getting this wrong sends one
# company's customers to another, so it is declared once here.
ROUTING_FIELD = {
    "messenger": "page_id",
    "instagram": "instagram_business_id",
    "whatsapp": "phone_number_id",
}

SECRET_FIELDS = {
    "access_token": "access_token_sealed",
    "verify_token": "verify_token_sealed",
    "app_secret": "app_secret_sealed",
}


class ChannelAccountError(RuntimeError):
    """A channel account could not be created or updated."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChannelAccountService:
    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_accounts(self, company_id: int) -> list[dict[str, Any]]:
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT channel_accounts.*, branches.name AS branch_name
                FROM channel_accounts
                LEFT JOIN branches ON branches.id = channel_accounts.branch_id
                WHERE channel_accounts.company_id = ?
                ORDER BY channel_accounts.id ASC
                """,
                (int(company_id),),
            ).fetchall()

        return [self._public(row) for row in rows]

    def get_account(self, company_id: int, account_id: int) -> dict[str, Any] | None:
        row = self._row(company_id, account_id)
        return self._public(row) if row else None

    def _row(self, company_id: int, account_id: int):
        with database_manager.control() as conn:
            return conn.execute(
                """
                SELECT * FROM channel_accounts
                WHERE id = ? AND company_id = ? LIMIT 1
                """,
                (int(account_id), int(company_id)),
            ).fetchone()

    def _public(self, row: Any) -> dict[str, Any]:
        """Shape a record for the browser.

        Sealed values never leave the server, not even encrypted. The screen
        only needs to know whether a credential is present.
        """
        data = dict(row)

        for field, column in SECRET_FIELDS.items():
            data[f"has_{field}"] = bool(data.pop(column, None))

        return data

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def _validate(self, channel: str, values: dict[str, Any]) -> str:
        normalized = str(channel or "").strip().lower()

        if normalized not in SUPPORTED_CHANNELS:
            raise ChannelAccountError(
                f"Channel must be one of: {', '.join(SUPPORTED_CHANNELS)}."
            )

        routing_field = ROUTING_FIELD[normalized]

        if not values.get(routing_field):
            raise ChannelAccountError(
                f"A {normalized} account needs a {routing_field.replace('_', ' ')} "
                "so inbound messages can be routed to this company."
            )

        return normalized

    def _assert_routing_id_is_free(
        self,
        conn,
        *,
        channel: str,
        routing_field: str,
        routing_value: str,
        exclude_id: int | None = None,
    ) -> None:
        """Refuse to point one account id at two companies.

        Without this, connecting a page already claimed elsewhere would make
        routing depend on row order — and silently deliver a company's customers
        to whichever record happened to be found first.
        """
        query = f"""
            SELECT id, company_id FROM channel_accounts
            WHERE {routing_field} = ? AND channel = ?
        """
        params: list[Any] = [str(routing_value), channel]

        if exclude_id is not None:
            query += " AND id != ?"
            params.append(int(exclude_id))

        existing = conn.execute(query + " LIMIT 1", params).fetchone()

        if existing:
            raise ChannelAccountError(
                "This account is already connected to another company on this "
                "platform. Disconnect it there first."
            )

    def create_account(
        self,
        *,
        company_id: int,
        channel: str,
        name: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        company_id = int(company_id)
        normalized_channel = self._validate(channel, values)
        routing_field = ROUTING_FIELD[normalized_channel]
        now = utc_now_iso()

        # Fails fast when the company has no provisioned database, rather than
        # writing a record whose secrets could never be sealed.
        company_key = database_manager.company_key(company_id)

        with database_manager.control() as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                self._assert_routing_id_is_free(
                    conn,
                    channel=normalized_channel,
                    routing_field=routing_field,
                    routing_value=values[routing_field],
                )

                cursor = conn.execute(
                    """
                    INSERT INTO channel_accounts (
                        company_id, branch_id, channel, name,
                        external_account_id, phone_number_id, page_id,
                        instagram_business_id, status,
                        ai_enabled, flow_enabled, voice_ai_enabled, image_ai_enabled,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        values.get("branch_id"),
                        normalized_channel,
                        str(name).strip(),
                        values.get("external_account_id") or values.get(routing_field),
                        values.get("phone_number_id"),
                        values.get("page_id"),
                        values.get("instagram_business_id"),
                        values.get("status", "active"),
                        1 if values.get("ai_enabled", True) else 0,
                        1 if values.get("flow_enabled", True) else 0,
                        1 if values.get("voice_ai_enabled", False) else 0,
                        1 if values.get("image_ai_enabled", False) else 0,
                        now,
                        now,
                    ),
                )

                account_id = int(cursor.lastrowid)

                for field, column in SECRET_FIELDS.items():
                    secret = values.get(field)

                    if secret:
                        conn.execute(
                            f"UPDATE channel_accounts SET {column} = ? WHERE id = ?",
                            (
                                keyring.seal_secret(
                                    secret, company_key, company_id, field
                                ),
                                account_id,
                            ),
                        )

                conn.commit()

            except Exception:
                conn.rollback()
                raise

        logger.info(
            "Connected %s account id=%s to company %s",
            normalized_channel,
            account_id,
            company_id,
        )

        return self.get_account(company_id, account_id)

    def update_account(
        self,
        *,
        company_id: int,
        account_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        company_id = int(company_id)
        account_id = int(account_id)

        existing = self._row(company_id, account_id)

        if not existing:
            raise ChannelAccountError("Channel account not found.")

        company_key = database_manager.company_key(company_id)
        channel = str(existing["channel"])
        routing_field = ROUTING_FIELD.get(channel, "external_account_id")

        plain_columns = (
            "name",
            "branch_id",
            "status",
            "external_account_id",
            "phone_number_id",
            "page_id",
            "instagram_business_id",
            "ai_enabled",
            "flow_enabled",
            "voice_ai_enabled",
            "image_ai_enabled",
        )

        assignments: list[str] = []
        params: list[Any] = []

        for column in plain_columns:
            if column not in values:
                continue

            value = values[column]

            if column.endswith("_enabled"):
                value = 1 if value else 0

            assignments.append(f"{column} = ?")
            params.append(value)

        for field, column in SECRET_FIELDS.items():
            if field not in values:
                continue

            secret = values[field]
            assignments.append(f"{column} = ?")
            params.append(
                keyring.seal_secret(secret, company_key, company_id, field)
                if secret
                else None
            )

        if not assignments:
            return self.get_account(company_id, account_id)

        with database_manager.control() as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                if values.get(routing_field):
                    self._assert_routing_id_is_free(
                        conn,
                        channel=channel,
                        routing_field=routing_field,
                        routing_value=values[routing_field],
                        exclude_id=account_id,
                    )

                assignments.append("updated_at = ?")
                params.extend([utc_now_iso(), account_id, company_id])

                conn.execute(
                    f"""
                    UPDATE channel_accounts
                    SET {', '.join(assignments)}
                    WHERE id = ? AND company_id = ?
                    """,
                    params,
                )
                conn.commit()

            except Exception:
                conn.rollback()
                raise

        return self.get_account(company_id, account_id)

    def delete_account(self, company_id: int, account_id: int) -> bool:
        with database_manager.control() as conn:
            cursor = conn.execute(
                "DELETE FROM channel_accounts WHERE id = ? AND company_id = ?",
                (int(account_id), int(company_id)),
            )
            conn.commit()

        if cursor.rowcount:
            logger.info(
                "Disconnected channel account id=%s from company %s",
                account_id,
                company_id,
            )

        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Credentials for the sending path
    # ------------------------------------------------------------------

    def credentials_for(
        self,
        *,
        company_id: int,
        channel: str,
    ) -> dict[str, Any] | None:
        """Return the sending credentials for a company's channel.

        Returns ``None`` when the company has no active account on that channel,
        which the caller must treat as "cannot send" — never as "fall back to
        someone else's token".
        """
        company_id = int(company_id)
        normalized = str(channel or "").strip().lower()

        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT * FROM channel_accounts
                WHERE company_id = ? AND channel = ? AND status = 'active'
                ORDER BY id ASC
                LIMIT 1
                """,
                (company_id, normalized),
            ).fetchone()

        if not row:
            return None

        credentials: dict[str, Any] = {
            "id": int(row["id"]),
            "channel": normalized,
            "page_id": row["page_id"],
            "phone_number_id": row["phone_number_id"],
            "instagram_business_id": row["instagram_business_id"],
            "access_token": None,
        }

        sealed = row["access_token_sealed"]

        if sealed:
            try:
                credentials["access_token"] = keyring.unseal_secret(
                    sealed,
                    database_manager.company_key(company_id),
                    company_id,
                    "access_token",
                )
            except CorruptedKeyMaterial:
                logger.error(
                    "Access token for company %s channel %s could not be "
                    "unsealed; refusing to send rather than using a stale value",
                    company_id,
                    normalized,
                )
                return None

        return credentials


channel_account_service = ChannelAccountService()
