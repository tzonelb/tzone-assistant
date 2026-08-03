"""Lookup helpers for the `channel_accounts` table.

This table (see database/database.py's CREATE TABLE for the authoritative
schema: company_id, channel, page_id, instagram_business_id,
access_token_encrypted, status, ...) is populated by the Facebook OAuth
connect flow. It maps a real Facebook Page / Instagram Business Account to
the T-ZONE company that connected it.

Every query here is defensive: if the table/columns are missing or the
query otherwise fails (e.g. a stale schema from an earlier init-order
issue), callers get `None` back instead of an exception, so multi-tenant
resolution can never take down the current single-company flow. Callers
are expected to fall back to conversation_control_service.resolve_default_company_id()
when this returns None.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from database.database import db


# Which channel_accounts column identifies the account for a given channel.
# For an incoming Meta webhook, parser.py's "recipient_id" is the Facebook
# Page id (messenger) or Instagram Business Account id (instagram) that
# actually received the message.
_ACCOUNT_ID_COLUMN_BY_CHANNEL = {
    "messenger": "page_id",
    "instagram": "instagram_business_id",
}


class ChannelAccountService:
    def get_company_id_for_account(
        self,
        channel: str,
        external_account_id: str | None,
    ) -> int | None:
        """Return the company_id that owns the given page/IG account, or None.

        `channel` should be "messenger" or "instagram" (as produced by
        channels/meta/parser.py). `external_account_id` is the incoming
        webhook's recipient_id (the Page id or IG Business Account id that
        received the message). Only rows with status='active' match.
        """
        normalized_channel = (channel or "").strip().lower()
        column = _ACCOUNT_ID_COLUMN_BY_CHANNEL.get(normalized_channel)

        if not column or not external_account_id:
            return None

        try:
            with db.connect() as conn:
                row = conn.execute(
                    f"""
                    SELECT company_id
                    FROM channel_accounts
                    WHERE channel = ?
                      AND {column} = ?
                      AND status = 'active'
                    ORDER BY id
                    LIMIT 1
                    """,
                    (normalized_channel, str(external_account_id)),
                ).fetchone()
        except sqlite3.Error:
            # Schema not ready / table missing / column missing — never let
            # a lookup failure break inbound message processing.
            return None

        if not row or row["company_id"] is None:
            return None

        return int(row["company_id"])

    def get_active_account(
        self,
        company_id: int,
        channel: str,
    ) -> dict[str, Any] | None:
        """Return the active channel_accounts row for a company + channel.

        Used on the outbound side to pick which Page/IG token to send a
        reply through. Returns None (never raises) when no row is
        configured yet — which is the case for every company today.
        """
        normalized_channel = (channel or "").strip().lower()

        if not company_id or not normalized_channel:
            return None

        try:
            with db.connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        id, company_id, channel, page_id,
                        instagram_business_id, access_token_encrypted, status
                    FROM channel_accounts
                    WHERE company_id = ?
                      AND channel = ?
                      AND status = 'active'
                    ORDER BY id
                    LIMIT 1
                    """,
                    (company_id, normalized_channel),
                ).fetchone()
        except sqlite3.Error:
            return None

        if not row:
            return None

        return dict(row)


channel_account_service = ChannelAccountService()
