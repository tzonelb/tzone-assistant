"""Two-factor authentication, with the second factor an authenticator app.

The account this exists for is the Super Admin. Its sign-in is deliberately one
factor — an email and a password, with no workspace code, because a platform
administrator belongs to no company and so has no code to type. It is also the
account that suspends companies, rotates workspace codes and reads the platform
audit. One guessed or reused password is the whole platform.

So enrolment is **mandatory** for a Super Admin and optional for everybody else.
An owner can see who on their team has turned it on, and require it of them by
policy rather than by code — the platform decides what protects the platform,
and the company decides what protects the company.

### Why TOTP and not SMS

An SMS code is delivered over a channel a SIM swap takes over, which is a
routine attack against exactly this kind of account. It would also make signing
in to the platform depend on a paid gateway staying up and paid for: the failure
mode is the operator locked out of their own console during the incident that
made them need it.

### What is stored

* The secret is **sealed**, never in the clear. Anyone holding it can generate
  this account's codes for ever, so a readable copy in the database would mean a
  dump hands over the second factor with the first — which is the same as having
  neither. It is sealed under the platform master key and bound to the account,
  so a row lifted from one user cannot be pasted onto another.
* Recovery codes are stored as **hashes**, shown once, and consumed on use. A
  readable copy would be a second permanent copy of the second factor.
* `totp_enabled` goes on only after the user proves a code the secret produced.
  Turning it on when the secret is issued would lock out anybody whose
  authenticator app failed to save the QR they just scanned.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import logging
import secrets
from typing import Any

import pyotp

from backend.security import keyring
from database.manager import DatabaseError, database_manager, utc_now_iso


logger = logging.getLogger(__name__)


SECRET_CONTEXT = "totp_secret"
ISSUER = "T-ZONE"

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_BYTES = 5  # 10 hex characters, shown in two groups of five.

# One step either side of now. A code is valid for 30 seconds, and a phone whose
# clock is a few seconds out is the ordinary case, not an attack — a window of
# zero produces support tickets, and a wide one hands an attacker more time on a
# code they may have shoulder-surfed.
VALID_WINDOW = 1


class TotpError(RuntimeError):
    """Enrolment or verification refused."""


def _hash_recovery(code: str) -> str:
    """Hash a recovery code.

    A plain SHA-256 rather than the password KDF: these are 40 bits of CSPRNG
    output with no structure to guess, so stretching buys nothing that the
    entropy does not already provide, and a login path that runs ten PBKDF2
    verifications to find a match would be its own denial of service.
    """
    return hashlib.sha256(code.strip().lower().encode("utf-8")).hexdigest()


class TotpService:
    # ------------------------------------------------------------------ state

    def status(self, user_id: int) -> dict[str, Any]:
        """Whether this account has a second factor, and whether it must."""
        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT id, email, is_super_admin, totp_enabled, totp_confirmed_at,
                       totp_secret_sealed, totp_recovery_hashes
                FROM users WHERE id = ? LIMIT 1
                """,
                (int(user_id),),
            ).fetchone()

        if not row:
            raise TotpError("No such account.")

        enabled = bool(row["totp_enabled"])
        required = bool(row["is_super_admin"])

        return {
            "enabled": enabled,
            "required": required,
            # The one state the console has to act on: an administrator who has
            # not enrolled yet. The interface sends them to enrolment instead of
            # to the dashboard.
            "enrolment_pending": required and not enabled,
            "confirmed_at": row["totp_confirmed_at"],
            "recovery_codes_remaining": len(
                self._recovery_hashes(row["totp_recovery_hashes"])
            ),
            "started": bool(row["totp_secret_sealed"]) and not enabled,
        }

    def is_required(self, user: dict[str, Any]) -> bool:
        return bool(user.get("is_super_admin"))

    def is_enabled(self, user: dict[str, Any]) -> bool:
        return bool(user.get("totp_enabled"))

    # ------------------------------------------------------------- enrolment

    def begin_enrolment(self, user_id: int) -> dict[str, Any]:
        """Issue a secret and the QR that carries it.

        The secret is stored sealed straight away but `totp_enabled` stays off:
        it is not a second factor until the user has proved their app can
        produce a code from it. Restarting enrolment issues a *new* secret and
        discards the old one, so an abandoned attempt cannot be resumed by
        somebody who photographed the first QR.
        """
        user_id = int(user_id)

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT id, email, full_name, totp_enabled FROM users WHERE id = ? LIMIT 1",
                (user_id,),
            ).fetchone()

            if not row:
                raise TotpError("No such account.")

            if bool(row["totp_enabled"]):
                raise TotpError(
                    "Two-factor authentication is already on for this account. "
                    "Turn it off first to enrol a new device."
                )

            secret = pyotp.random_base32()

            conn.execute(
                """
                UPDATE users
                SET totp_secret_sealed = ?, totp_confirmed_at = NULL
                WHERE id = ?
                """,
                (self._seal(secret, user_id), user_id),
            )
            conn.commit()

            label = str(row["email"])

        uri = pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name=ISSUER)

        return {
            # Returned once, to be scanned or typed. It is never returned again:
            # `status` reports that enrolment is in progress and nothing more.
            "secret": secret,
            "uri": uri,
            "qr_svg": self._qr(uri),
        }

    def confirm_enrolment(self, user_id: int, code: str) -> dict[str, Any]:
        """Turn the second factor on, and hand back the recovery codes.

        The codes are returned exactly once. They are stored hashed, so this is
        the only moment they exist in readable form anywhere — which is the
        point, and is why the caller has to show them before navigating away.
        """
        user_id = int(user_id)

        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT id, totp_secret_sealed, totp_enabled
                FROM users WHERE id = ? LIMIT 1
                """,
                (user_id,),
            ).fetchone()

            if not row:
                raise TotpError("No such account.")

            if bool(row["totp_enabled"]):
                raise TotpError("Two-factor authentication is already on.")

            if not row["totp_secret_sealed"]:
                raise TotpError("Start enrolment before confirming it.")

            secret = self._unseal(row["totp_secret_sealed"], user_id)

            if not self._matches(secret, code):
                raise TotpError(
                    "That code is not right. Check your authenticator app and "
                    "try the next one."
                )

            codes = [
                secrets.token_hex(RECOVERY_CODE_BYTES)
                for _ in range(RECOVERY_CODE_COUNT)
            ]

            conn.execute(
                """
                UPDATE users
                SET totp_enabled = 1,
                    totp_confirmed_at = ?,
                    totp_recovery_hashes = ?
                WHERE id = ?
                """,
                (
                    utc_now_iso(),
                    json.dumps([_hash_recovery(code) for code in codes]),
                    user_id,
                ),
            )
            conn.commit()

        return {"enabled": True, "recovery_codes": codes}

    def disable(self, user_id: int, *, force: bool = False) -> None:
        """Turn the second factor off and discard everything behind it.

        A Super Admin may not turn their own off: their sign-in is one factor by
        design, and this is what makes it two. `force` exists for the CLI, which
        is the emergency exit when an administrator has genuinely lost both
        their device and their recovery codes — and it is a console command on
        the server, not an endpoint anybody can reach.
        """
        user_id = int(user_id)

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT id, is_super_admin FROM users WHERE id = ? LIMIT 1",
                (user_id,),
            ).fetchone()

            if not row:
                raise TotpError("No such account.")

            if bool(row["is_super_admin"]) and not force:
                raise TotpError(
                    "Two-factor authentication is required for a platform "
                    "administrator and cannot be switched off from here."
                )

            conn.execute(
                """
                UPDATE users
                SET totp_enabled = 0,
                    totp_secret_sealed = NULL,
                    totp_confirmed_at = NULL,
                    totp_recovery_hashes = NULL
                WHERE id = ?
                """,
                (user_id,),
            )
            conn.commit()

    # ---------------------------------------------------------- verification

    def verify(self, user_id: int, code: str) -> bool:
        """Check a code at sign-in. A recovery code is accepted and consumed.

        Returns False rather than raising: the caller answers a failed second
        factor with the same message as a failed password, and an exception type
        that distinguished them would be a way to ask whether an account has 2FA
        on.
        """
        user_id = int(user_id)
        code = str(code or "").strip().replace(" ", "")

        if not code:
            return False

        try:
            with database_manager.control() as conn:
                row = conn.execute(
                    """
                    SELECT totp_secret_sealed, totp_enabled, totp_recovery_hashes,
                           totp_last_step
                    FROM users WHERE id = ? LIMIT 1
                    """,
                    (user_id,),
                ).fetchone()

                if not row or not bool(row["totp_enabled"]):
                    return False

                secret = self._unseal(row["totp_secret_sealed"], user_id)

                if secret:
                    step = self._matched_step(secret, code)

                    if step is not None:
                        # RFC 6238 single-use: the code is valid for its window,
                        # but a validated step must never be accepted twice, or
                        # an observed code could be replayed for the rest of that
                        # window. Claim the step atomically -- only a step newer
                        # than the last accepted one wins -- so two simultaneous
                        # logins with the same code cannot both succeed.
                        conn.execute("BEGIN IMMEDIATE")
                        claimed = conn.execute(
                            """
                            UPDATE users
                            SET totp_last_step = ?
                            WHERE id = ?
                              AND (totp_last_step IS NULL OR totp_last_step < ?)
                            """,
                            (step, user_id, step),
                        )
                        conn.commit()

                        return claimed.rowcount == 1

                # Not a TOTP code. It may be a recovery code, which is single
                # use: consumed inside the same transaction that accepts it, so
                # two simultaneous attempts cannot both spend the same one.
                remaining = self._recovery_hashes(row["totp_recovery_hashes"])
                offered = _hash_recovery(code)

                for stored in remaining:
                    if hmac.compare_digest(stored, offered):
                        remaining.remove(stored)
                        conn.execute(
                            "UPDATE users SET totp_recovery_hashes = ? WHERE id = ?",
                            (json.dumps(remaining), user_id),
                        )
                        conn.commit()

                        logger.warning(
                            "A recovery code was used for user %s; %s remain",
                            user_id,
                            len(remaining),
                        )

                        return True

                return False
        except (DatabaseError, keyring.CorruptedKeyMaterial):
            # Fails **closed**, unlike almost every other guard in this
            # codebase. The others fail open because refusing would deny a
            # customer work they are entitled to; here, allowing would admit
            # somebody who has not proved their second factor. The direction
            # follows the consequence, not a house style.
            logger.exception("Could not verify a second factor for user %s", user_id)

            return False

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _matches(secret: str, code: str) -> bool:
        try:
            return bool(
                pyotp.TOTP(secret).verify(
                    str(code).strip().replace(" ", ""), valid_window=VALID_WINDOW
                )
            )
        except Exception:
            return False

    @staticmethod
    def _matched_step(secret: str, code: str) -> int | None:
        """Which time-step a code matches, or None.

        `pyotp`'s own `verify` only returns a bool, but single-use enforcement
        needs the step itself so it can be recorded and compared. The candidate
        steps are the same window `verify` would check (now +/- VALID_WINDOW),
        and the comparison is constant-time.
        """
        cleaned = str(code).strip().replace(" ", "")

        if not cleaned:
            return None

        try:
            totp = pyotp.TOTP(secret)
            current_step = int(time.time()) // totp.interval

            for offset in range(VALID_WINDOW, -VALID_WINDOW - 1, -1):
                step = current_step + offset

                if hmac.compare_digest(totp.at(step * totp.interval), cleaned):
                    return step
        except Exception:
            return None

        return None

    @staticmethod
    def _recovery_hashes(stored: Any) -> list[str]:
        if not stored:
            return []

        try:
            parsed = json.loads(stored)
        except (TypeError, ValueError):
            return []

        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    @staticmethod
    def _seal(secret: str, user_id: int) -> str:
        return keyring.seal_user_secret(
            secret, database_manager.master_key(), user_id, SECRET_CONTEXT
        )

    @staticmethod
    def _unseal(sealed: Any, user_id: int) -> str:
        if not sealed:
            return ""

        return keyring.unseal_user_secret(
            str(sealed), database_manager.master_key(), user_id, SECRET_CONTEXT
        )

    @staticmethod
    def _qr(uri: str) -> str:
        """The enrolment QR as inline SVG.

        SVG rather than a PNG data URI: it is smaller, it scales, and it needs
        no image encoder. The secret is inside the URI either way, which is why
        this response is the only one that ever carries it.
        """
        import io

        import segno

        # A bytes buffer, not a text one: segno writes encoded bytes for every
        # kind including SVG, so a `StringIO` raises rather than producing a
        # string.
        buffer = io.BytesIO()
        segno.make(uri, error="m").save(buffer, kind="svg", scale=5, xmldecl=False)

        return buffer.getvalue().decode("utf-8")


totp_service = TotpService()
