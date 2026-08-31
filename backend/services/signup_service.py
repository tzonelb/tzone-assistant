"""Self-service sign-up: a stranger, an email they can read, and a demo workspace.

Everything a company created this way cannot do lives in `demo_gate.py`. This
module is about getting there safely, and the risks it answers are the ones
that come with a form anybody on the internet can post to.

**Every workspace costs a file.** Provisioning creates an encrypted SQLCipher
database on disk, roles, permissions and an owner. A form that does that
without a cost to the caller is a way to fill the volume, so two things stand
in front of it: a code sent to an email address the caller must be able to
read, and a ceiling on how many workspaces one address can create.

**The email code is the throttle, not the identity check.** It proves somebody
reached that mailbox once. That is enough to make bulk creation tedious and it
is not enough to prove anything else, which is why the workspace it produces is
still a demonstration.

**A code that cannot be delivered must refuse loudly.** If the mailer is not
configured, this says so rather than returning "sent" to a screen that then
asks for a code nobody will ever receive. The same decision the password-reset
path already took, for the same reason: a silent no-op here is a sign-up form
that is broken in a way no one can diagnose from the outside.

**Sign-up says the same thing whether or not the address is already in use.**
"We sent a code" either way. Answering differently turns this endpoint into a
way to ask the platform which of a list of email addresses has an account.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from typing import Any

from backend.services import mailer
from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)


class SignupError(Exception):
    """A sign-up that cannot proceed, with the reason a person may read."""


# Long enough to be tedious to guess inside its lifetime, short enough to read
# off a phone and type. The real defence is the attempt ceiling below; six
# digits without one would be guessable in an afternoon.
CODE_DIGITS = 6

CODE_TTL_MINUTES = 20

# Wrong guesses before the code is dead and a new one must be requested.
MAX_ATTEMPTS = 5

# How many workspaces one email address may create. Not a business rule -- a
# person genuinely running three businesses can ask -- but the difference
# between "somebody made a few" and "somebody made four thousand".
MAX_WORKSPACES_PER_EMAIL = 3

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalise_email(email: str) -> str:
    address = str(email or "").strip().lower()

    if not _EMAIL.match(address):
        raise SignupError("That does not look like an email address.")

    return address


class SignupService:
    # ------------------------------------------------------------ the code

    def send_code(self, *, email: str, ip_address: str | None = None) -> dict[str, Any]:
        """Send a verification code, or refuse for a reason worth saying.

        Returns the same shape whether or not an account already exists for the
        address, so this cannot be used to test which addresses are registered.
        """
        address = _normalise_email(email)

        # Refuse rather than pretend. A "sent" that was never sent leaves the
        # screen asking for a code that cannot arrive, and nothing in the
        # product says why.
        if not mailer.is_configured():
            raise SignupError(
                "Sign-up by email is not available on this server yet. Ask "
                "whoever runs it to configure email delivery."
            )

        code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
        expires_at = _in_minutes(CODE_TTL_MINUTES)

        with database_manager.control() as conn:
            # One live code per address: requesting another replaces the last,
            # so a caller cannot accumulate valid codes by asking repeatedly.
            conn.execute(
                "DELETE FROM signup_codes WHERE email = ?", (address,)
            )
            conn.execute(
                """
                INSERT INTO signup_codes (
                    email, code_hash, expires_at, attempts, created_at, ip_address
                )
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (address, _hash(code), expires_at, utc_now_iso(), ip_address),
            )
            conn.commit()

        mailer.send(
            to=address,
            subject="Your verification code",
            body=(
                f"Your verification code is {code}.\n\n"
                f"It expires in {CODE_TTL_MINUTES} minutes. If you did not ask "
                "to create a workspace, you can ignore this message — nothing "
                "has been created."
            ),
        )

        return {"sent": True, "expires_at": expires_at}

    def _consume_code(self, *, email: str, code: str) -> None:
        """Spend the code, or raise. Never says which part was wrong."""
        address = _normalise_email(email)
        now = utc_now_iso()
        wrong = SignupError(
            "That verification code is not valid or has expired. Ask for a new "
            "one and try again."
        )

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT id, code_hash, expires_at, attempts FROM signup_codes "
                "WHERE email = ? LIMIT 1",
                (address,),
            ).fetchone()

            if row is None or row["expires_at"] <= now:
                raise wrong

            if int(row["attempts"]) >= MAX_ATTEMPTS:
                raise wrong

            if not secrets.compare_digest(str(row["code_hash"]), _hash(str(code or "").strip())):
                # Counted before the refusal is raised, so a wrong guess costs
                # the caller an attempt even though the request failed.
                conn.execute(
                    "UPDATE signup_codes SET attempts = attempts + 1 WHERE id = ?",
                    (row["id"],),
                )
                conn.commit()

                raise wrong

            # Correct: the code is gone, so it cannot be replayed.
            conn.execute("DELETE FROM signup_codes WHERE id = ?", (row["id"],))
            conn.commit()

    # ------------------------------------------------------- the workspace

    def _refuse_if_over_the_ceiling(self, email: str) -> None:
        with database_manager.control() as conn:
            existing = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM company_users
                JOIN users ON users.id = company_users.user_id
                WHERE users.email = ?
                """,
                (email,),
            ).fetchone()

        if int(existing["total"]) >= MAX_WORKSPACES_PER_EMAIL:
            raise SignupError(
                "This email address already has the maximum number of "
                "workspaces. Ask support if you need another."
            )

    def create_demo_workspace(
        self,
        *,
        company_name: str,
        owner_full_name: str,
        owner_email: str,
        password: str,
        email_code: str,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Verify the code, provision a demonstration, and describe it.

        The workspace is marked `is_demo` in the same transaction that finishes
        provisioning it, so there is no window in which a self-service company
        exists and is not a demonstration.
        """
        from backend.services.auth_service import auth_service
        from backend.services.platform_service import platform_service

        address = _normalise_email(owner_email)
        name = str(company_name or "").strip()

        if len(name) < 2:
            raise SignupError("Give your workspace a name.")

        # The platform's one minimum, read from where it is defined rather
        # than repeated here -- `schemas/auth.py` already carried a second
        # copy of this number once, and the two drifted (8 against 10).
        minimum = auth_service.MIN_PASSWORD_LENGTH

        if len(str(password or "")) < minimum:
            raise SignupError(
                f"Choose a password of at least {minimum} characters."
            )

        self._consume_code(email=address, code=email_code)
        self._refuse_if_over_the_ceiling(address)

        created = platform_service.create_company(
            name=name,
            slug=platform_service.slugify(name) or f"workspace-{secrets.token_hex(3)}",
            workspace=name,
            owner_email=address,
            owner_name=str(owner_full_name or "").strip() or address,
            owner_password=password,
            ip_address=ip_address,
        )

        company_id = int(created["company_id"])

        with database_manager.control() as conn:
            conn.execute(
                "UPDATE companies SET is_demo = 1, updated_at = ? WHERE id = ?",
                (utc_now_iso(), company_id),
            )
            conn.commit()

        from backend.services.demo_gate import demo_gate
        from backend.services.demo_seed_service import demo_seed_service

        demo_gate.invalidate(company_id)

        # After the flag, never before: a half-seeded workspace that is not yet
        # marked as a demonstration is one that could connect a channel.
        seeded = demo_seed_service.seed(
            company_id=company_id, owner_user_id=int(created["owner_user_id"])
        )

        logger.info(
            "Demo workspace %s created by sign-up, seeded %s", company_id, seeded
        )

        return {
            "company_id": company_id,
            "owner_user_id": int(created["owner_user_id"]),
            "name": created["name"],
            "slug": created["slug"],
            "is_demo": True,
        }


def _in_minutes(minutes: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (
        datetime.now(timezone.utc) + timedelta(minutes=minutes)
    ).isoformat()


signup_service = SignupService()
