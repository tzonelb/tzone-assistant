"""One place that answers "does this company own that id?".

Ids in the control database are global. Nothing about `42` says which company
owns it, so every field that carries one from a request has to be checked —
and this platform has now had the *same* defect four times, each in a place
where the check existed a few lines away and was not extended:

* `channel_accounts.branch_id` was written straight from the payload while
  `department_id`, in the same argument list, was checked. Another company's
  branch name reached three screens.
* `company_users.branch_id`, the same field on the neighbouring table.
* `team_chat.add_member` validates the invitee against the company directory,
  with a comment saying "without this check a caller could name any user id in
  the platform" — and `create_channel`, which takes the same list as
  `member_user_ids`, did not.
* `appointments` accepted any `staff_user_id` and any `branch_id`.

Four times is not four mistakes; it is one missing shared answer. A check that
lives inside the function that happens to need it is invisible to the next
function that needs it, and reads in review exactly like a check that is
called.

So the checks live here, they raise the same error whatever asks, and every
caller gets the same refusal message. Reading only — nothing here writes, and
nothing here decides policy. Whether a company *may* book an appointment is
the caller's business; whether the person being booked works there is this
module's.

Deliberately not a validator for tenant ids. `category_id`, `customer_id` and
`conversation_id` point into the company's own encrypted file, which another
company's connection cannot open, so there is nothing to check and a check
would imply a risk that does not exist.
"""

from __future__ import annotations

from typing import Iterable

from database.manager import database_manager


class NotOwnedByCompany(ValueError):
    """An id in the request names a row belonging to somebody else.

    A `ValueError` so that callers which already turn one into a 400 keep
    working, and a distinct type so the ones that want a 404 can tell it apart
    from an ordinary bad value.
    """


def assert_employees(company_id: int, user_ids: Iterable[int | None]) -> list[int]:
    """Every id must be an active employee of this company.

    Both halves of "active" are required — a person whose platform account is
    disabled, and a person whose membership of *this* company is disabled, are
    equally not somebody to assign work to. Returns the ids so a caller can use
    the cleaned list rather than the raw one.
    """
    wanted = sorted({int(value) for value in user_ids if value is not None})

    if not wanted:
        return []

    placeholders = ",".join("?" for _ in wanted)

    with database_manager.control() as conn:
        rows = conn.execute(
            f"""
            SELECT users.id
            FROM users
            JOIN company_users ON company_users.user_id = users.id
            WHERE users.id IN ({placeholders})
              AND company_users.company_id = ?
              AND users.status = 'active'
              AND company_users.status = 'active'
            """,
            (*wanted, int(company_id)),
        ).fetchall()

    found = {int(row["id"]) for row in rows}
    missing = [value for value in wanted if value not in found]

    if missing:
        raise NotOwnedByCompany(
            "These people are not active employees of this company: "
            + ", ".join(str(value) for value in missing)
        )

    return wanted


def assert_branch(company_id: int, branch_id: int | None) -> int | None:
    """`None` stays `None` — most companies have one location and never set it."""
    if branch_id is None or str(branch_id).strip() == "":
        return None

    try:
        wanted = int(branch_id)
    except (TypeError, ValueError) as exc:
        raise NotOwnedByCompany("The branch id must be a number.") from exc

    with database_manager.control() as conn:
        row = conn.execute(
            "SELECT id FROM branches WHERE id = ? AND company_id = ? LIMIT 1",
            (wanted, int(company_id)),
        ).fetchone()

    if not row:
        raise NotOwnedByCompany("That branch does not belong to this company.")

    return wanted


def assert_channel_account(company_id: int, account_id: int | None) -> int | None:
    if account_id is None or str(account_id).strip() == "":
        return None

    try:
        wanted = int(account_id)
    except (TypeError, ValueError) as exc:
        raise NotOwnedByCompany("The channel account id must be a number.") from exc

    with database_manager.control() as conn:
        row = conn.execute(
            "SELECT id FROM channel_accounts WHERE id = ? AND company_id = ? LIMIT 1",
            (wanted, int(company_id)),
        ).fetchone()

    if not row:
        raise NotOwnedByCompany(
            "That connected account does not belong to this company."
        )

    return wanted
