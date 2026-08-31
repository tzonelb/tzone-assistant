"""Minting and redeeming the code that turns a demonstration into a business.

The operator issues a code; an owner types it into their demo workspace; the
workspace becomes real and can connect its own channels. Two operations, and
almost all of the care is in the second one.

**The code is never stored.** Only `sha256` of it, the same way
`auth_sessions.token_hash` and `password_reset_tokens` already work here. The
operator sees the code once, at the moment it is minted, and a leaked control
database yields a table of hashes that nobody can redeem. That also means a
lost code cannot be recovered, only reissued -- which is the correct trade and
is said plainly in the console copy.

**Redeeming is single-use, and single-use under concurrency.** Two requests
carrying the same code, arriving together, must not both succeed: the check and
the claim happen in one `UPDATE ... WHERE used_at IS NULL`, and the loser is the
request that updated no rows. Reading first and writing second would leave a
window exactly as wide as the database call between them, and "the same trial
code activated four workspaces" is the kind of defect that is found by an
accountant rather than by a test.

**A wrong code is a wrong code.** Expired, spent, or never minted -- one
refusal, one wording. Distinguishing them tells somebody guessing which of
their guesses was once real.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

from backend.services.demo_gate import demo_gate
from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)


class ActivationError(Exception):
    """A code that cannot be redeemed, with the reason a person may read."""


# The alphabet `keyring.generate_workspace_code` uses, and for its reason: no
# O/0, no I/1/l. A code is read off a screen, an invoice or a phone call and
# typed by hand, and every pair that looks alike is a support call.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

_GROUPS = 4

_GROUP_SIZE = 4

# 31 symbols, 16 of them: about 79 bits. Guessing is not the attack this
# resists -- rate limiting is -- but a code short enough to guess would make
# the rate limit the only thing standing between a stranger and a free
# workspace, and there is no reason to be that close to the edge.
_PREFIX = "TZA"


def _normalise(code: str) -> str:
    """What the owner typed, as the code that was minted.

    People paste codes with spaces around them, type them in lower case, and
    leave out the dashes when reading from a phone call. None of that is a
    different code, and refusing it would produce a support ticket rather than
    a security outcome.
    """
    stripped = "".join(str(code or "").split()).upper()

    return stripped.replace("_", "-")


def _hash(code: str) -> str:
    return hashlib.sha256(_normalise(code).encode("utf-8")).hexdigest()


class ActivationService:
    # ------------------------------------------------------------------ mint

    def mint(
        self,
        *,
        plan_id: int | None = None,
        note: str | None = None,
        created_by_user_id: int | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Create one code and return it readable, this once.

        `plan_id` is optional: a code may simply lift the demonstration and
        leave the plan to be chosen, which is what a code handed out at a trade
        show does.
        """
        code = f"{_PREFIX}-" + "-".join(
            "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_SIZE))
            for _ in range(_GROUPS)
        )

        with database_manager.control() as conn:
            cursor = conn.execute(
                """
                INSERT INTO activation_codes (
                    code_hash, plan_id, note, created_by_user_id,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _hash(code),
                    plan_id,
                    (note or "").strip() or None,
                    created_by_user_id,
                    utc_now_iso(),
                    expires_at,
                ),
            )
            conn.commit()

            return {
                "id": int(cursor.lastrowid),
                # The only time this leaves the process. Everything that reads
                # a code afterwards reads a hash.
                "code": code,
                "plan_id": plan_id,
                "note": note,
                "expires_at": expires_at,
            }

    # ---------------------------------------------------------------- redeem

    def redeem(self, *, company_id: int, code: str) -> dict[str, Any]:
        """Spend a code on this workspace, or refuse.

        Returns what changed, so the caller can tell the owner which plan they
        landed on rather than only that it worked.
        """
        now = utc_now_iso()
        code_hash = _hash(code)

        with database_manager.control() as conn:
            company = conn.execute(
                "SELECT id, is_demo FROM companies WHERE id = ? LIMIT 1",
                (company_id,),
            ).fetchone()

            if company is None:
                raise ActivationError("This workspace could not be found.")

            # Refused rather than treated as a no-op, and the code is not
            # spent: an owner who pastes a code into an already-live workspace
            # has made a mistake, and burning their code would turn it into a
            # loss.
            if not company["is_demo"]:
                raise ActivationError(
                    "This workspace is already active — your activation code "
                    "has not been used, so you can still use it on the "
                    "workspace you meant."
                )

            # One statement does the checking and the claiming. Reading first
            # and writing second leaves a window between them in which a second
            # request carrying the same code also reads it as unused.
            claimed = conn.execute(
                """
                UPDATE activation_codes
                SET used_at = ?, used_by_company_id = ?
                WHERE code_hash = ?
                  AND used_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (now, company_id, code_hash, now),
            )

            if claimed.rowcount != 1:
                # Never minted, already spent, or expired -- one wording. The
                # difference would tell somebody guessing which of their
                # guesses had once been real.
                raise ActivationError(
                    "That activation code is not valid. Check it for typos, or "
                    "ask whoever gave it to you for a new one."
                )

            row = conn.execute(
                "SELECT id, plan_id FROM activation_codes WHERE code_hash = ? LIMIT 1",
                (code_hash,),
            ).fetchone()

            conn.execute(
                "UPDATE companies SET is_demo = 0, activated_at = ?, updated_at = ? "
                "WHERE id = ?",
                (now, now, company_id),
            )

            conn.commit()

        # The gate caches its answer for thirty seconds, and the owner who has
        # just typed the code is looking at the screen. Dropping it here is
        # what makes the next request able to connect a channel.
        demo_gate.invalidate(company_id)

        plan_id = row["plan_id"] if row is not None else None

        logger.info(
            "Activation code %s redeemed by company %s",
            row["id"] if row is not None else "?",
            company_id,
        )

        return {
            "company_id": company_id,
            "activated_at": now,
            "plan_id": plan_id,
        }


activation_service = ActivationService()
