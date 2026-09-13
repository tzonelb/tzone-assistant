"""A one-time code, emailed to the account, before a channel account can be
connected or disconnected.

Channel credentials route a company's real customer conversations, so
establishing or removing one is treated the way a password change is: proven
by something reaching a mailbox this platform does not control, not by the
session cookie alone -- a stolen or left-open browser tab is not enough to
redirect a company's WhatsApp number on its own.

Two steps, two tables, deliberately separate from the employee's own session:

1. ``request_code`` mints a 6-digit code, hashes it (never the code itself,
   the same discipline as ``auth_service.create_password_reset``), and emails
   it to the account's own address -- there is no one else to send it to, and
   no enumeration risk, since the caller is already authenticated as this
   account.
2. ``confirm_code`` spends the code -- one claim, one use, the same
   ``UPDATE ... WHERE used_at IS NULL`` pattern as
   ``auth_service.consume_password_reset`` -- and mints a short-lived elevated
   grant. The grant, not the code, is what ``require_elevated`` checks on
   every connect/disconnect call, so entering the code once covers a whole
   sitting of channel changes rather than one click.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from backend.services.auth_service import utc_now, utc_now_iso
from backend.services import mailer
from config.settings import config
from database.manager import database_manager


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _generate_code() -> str:
    # Zero-padded so "000123" is a valid, six-character code -- trimming
    # would make some codes seven digits away from the ones just below them
    # and produce a subtly smaller keyspace.
    return f"{secrets.randbelow(1_000_000):06d}"


def request_code(*, user_id: int, company_id: int, email: str, full_name: str | None) -> None:
    """Mint a code and email it. Raises ``mailer.MailerNotConfigured`` if
    delivery cannot happen, rather than reporting success for a code that will
    never arrive -- this caller is already authenticated as the account
    asking, so there is no enumeration cost in saying so plainly."""
    mailer.assert_configured()

    code = _generate_code()
    now = utc_now()
    expires_at = now + timedelta(minutes=config.CHANNEL_VERIFICATION_TTL_MINUTES)

    with database_manager.control() as conn:
        # Any earlier unused code for this account is spent first -- two live
        # codes for one sitting means the older one is a second key nobody is
        # tracking, the same reasoning as create_password_reset.
        conn.execute(
            """
            UPDATE channel_verification_codes
            SET used_at = ?
            WHERE user_id = ? AND company_id = ? AND used_at IS NULL
            """,
            (now.isoformat(), int(user_id), int(company_id)),
        )
        conn.execute(
            """
            INSERT INTO channel_verification_codes (
                user_id, company_id, code_hash, expires_at, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                int(company_id),
                _hash(code),
                expires_at.isoformat(),
                now.isoformat(),
            ),
        )
        conn.commit()

    minutes = config.CHANNEL_VERIFICATION_TTL_MINUTES

    mailer.send(
        to=email,
        subject="Your T-ZONE channel verification code",
        body=(
            f"Hello {full_name or ''},\n\n"
            f"Use this code to connect or disconnect a channel: {code}\n\n"
            f"It works once and expires in {minutes} minutes.\n\n"
            "If you did not ask for this, ignore this email -- nothing "
            "changes until the code is used.\n"
        ),
    )


def confirm_code(*, user_id: int, company_id: int, code: str) -> dict[str, Any]:
    """Spend a code and mint an elevated grant. Returns the raw token once,
    the same discipline as ``auth_service.create_password_reset``: only its
    hash is ever stored.

    Returns ``{"granted": False}`` for a wrong, expired, already-used or
    missing code -- deliberately not distinguished further. A precise reason
    ("expired" vs "wrong") turns a handful of tries into a code-guessing
    oracle; the six-digit space is only safe against that when every miss
    looks the same.
    """
    now = utc_now()

    with database_manager.control() as conn:
        claimed = conn.execute(
            """
            UPDATE channel_verification_codes
            SET used_at = ?
            WHERE id = (
                SELECT id FROM channel_verification_codes
                WHERE user_id = ? AND company_id = ? AND code_hash = ?
                  AND used_at IS NULL AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
            )
            """,
            (now.isoformat(), int(user_id), int(company_id), _hash(code), now.isoformat()),
        )

        if claimed.rowcount < 1:
            conn.commit()
            return {"granted": False}

        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=config.CHANNEL_VERIFICATION_TTL_MINUTES)

        conn.execute(
            """
            INSERT INTO channel_elevated_grants (
                user_id, company_id, token_hash, expires_at, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(user_id), int(company_id), _hash(token), expires_at.isoformat(), utc_now_iso()),
        )
        conn.commit()

    return {
        "granted": True,
        "elevated_token": token,
        "expires_at": expires_at.isoformat(),
    }


def is_elevated(*, user_id: int, company_id: int, token: str) -> bool:
    """Whether this bearer token is a live grant for this account and company.

    Scoped to both: a grant minted while managing one company must not carry
    over to another the same person happens to belong to.
    """
    if not token:
        return False

    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM channel_elevated_grants
            WHERE user_id = ? AND company_id = ? AND token_hash = ?
              AND expires_at > ?
            LIMIT 1
            """,
            (int(user_id), int(company_id), _hash(token), utc_now_iso()),
        ).fetchone()

    return row is not None
