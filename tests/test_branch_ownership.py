"""A branch id must name a branch the company owns.

Found by auditing every table for readers and writers. `branches` had four
readers and no writer at all, which is what drew attention to it — and looking
at the readers showed something worse than a dead table.

`channel_accounts.branch_id` and `company_users.branch_id` were written
straight from the request payload. Ids in the control database are global, so
another company's branch id is a perfectly valid row. Three read joins matched
on the id alone with no company condition, so the other company's branch *name*
came back:

* `channel_account_service.list_accounts` — the Channels screen
* `dashboard.py` — the dashboard, twice
* `roles.py` — the team list on the Roles & Permissions screen

One name per row, not a bulk dump. It is on the list anyway because the size of
a leak is not what makes it one: a value from another company's row reached
this company's screen, which is the single property this platform is built to
prevent.

What made it survive is worth recording. At both write sites the neighbouring
pointer *was* checked — `department_id` in `channel_account_service`, `role_id`
in `roles.py`, each with a comment explaining why an id from another company
must be refused. `branch_id` sat in the same argument list and in the same
plain-column tuple, and was not. The reasoning had been done and applied to the
field next to it.

Fixed on both sides: refused at the write, and the joins scoped by company so a
row already stored before this change displays nothing either.
"""

from __future__ import annotations

import sys

import pytest


BETA_BRANCH = "Beta Secret Warehouse"


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    # Without this the test would run against the real database and pass for
    # the wrong reason. It has happened twice in this suite already.
    assert "backend.services.channel_account_service" in rebound

    from backend.services.channel_account_service import channel_account_service

    return channel_account_service


@pytest.fixture()
def beta_branch(platform):
    """A branch belonging to Beta.

    Inserted directly because nothing in the platform can create a branch —
    see `test_nothing_can_create_a_branch` at the bottom of this file.
    """
    from database.manager import utc_now_iso

    manager = platform["manager"]
    now = utc_now_iso()

    with manager.control() as conn:
        cursor = conn.execute(
            """
            INSERT INTO branches (
                company_id, name, code, status, created_at, updated_at
            )
            VALUES (?, ?, 'BSW', 'active', ?, ?)
            """,
            (platform["companies"]["beta"]["id"], BETA_BRANCH, now, now),
        )
        conn.commit()

    return int(cursor.lastrowid)


@pytest.fixture()
def alpha_branch(platform):
    """And one Alpha really owns, so the tests below prove the check refuses
    the foreign id rather than refusing every id."""
    from database.manager import utc_now_iso

    manager = platform["manager"]
    now = utc_now_iso()

    with manager.control() as conn:
        cursor = conn.execute(
            """
            INSERT INTO branches (
                company_id, name, code, status, created_at, updated_at
            )
            VALUES (?, 'Alpha Downtown', 'ADT', 'active', ?, ?)
            """,
            (platform["companies"]["alpha"]["id"], now, now),
        )
        conn.commit()

    return int(cursor.lastrowid)


def _alpha(platform):
    return platform["companies"]["alpha"]["id"]


# ------------------------------------------------------------- channel accounts


def test_connecting_an_account_to_another_companys_branch_is_refused(
    wired, platform, beta_branch
):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="does not belong to this company"):
        wired.create_account(
            company_id=_alpha(platform),
            channel="messenger",
            name="Alpha page",
            values={"branch_id": beta_branch, "page_id": "PAGE-A"},
        )


def test_a_companys_own_branch_is_accepted(wired, platform, alpha_branch):
    """The other half. A check that refused everything would pass the test
    above while breaking the feature."""
    account = wired.create_account(
        company_id=_alpha(platform),
        channel="messenger",
        name="Alpha page",
        values={"branch_id": alpha_branch, "page_id": "PAGE-A"},
    )

    assert account["branch_id"] == alpha_branch
    assert wired.list_accounts(_alpha(platform))[0]["branch_name"] == "Alpha Downtown"


def test_no_branch_is_still_allowed(wired, platform):
    """Optional means optional — most companies have one location."""
    account = wired.create_account(
        company_id=_alpha(platform),
        channel="messenger",
        name="Alpha page",
        values={"branch_id": None, "page_id": "PAGE-A"},
    )

    assert account["branch_id"] is None


def test_moving_an_account_to_another_companys_branch_is_refused(
    wired, platform, beta_branch, alpha_branch
):
    """Create was not the only door. `branch_id` was in the plain-column tuple
    of `update_account`, so an account created clean could be edited dirty."""
    from backend.services.channel_account_service import ChannelAccountError

    account = wired.create_account(
        company_id=_alpha(platform),
        channel="messenger",
        name="Alpha page",
        values={"branch_id": alpha_branch, "page_id": "PAGE-A"},
    )

    with pytest.raises(ChannelAccountError, match="does not belong to this company"):
        wired.update_account(
            company_id=_alpha(platform),
            account_id=account["id"],
            values={"branch_id": beta_branch},
        )

    assert wired.get_account(_alpha(platform), account["id"])["branch_id"] == alpha_branch


def test_an_account_can_still_be_moved_between_its_own_branches(
    wired, platform, alpha_branch
):
    account = wired.create_account(
        company_id=_alpha(platform),
        channel="messenger",
        name="Alpha page",
        values={"page_id": "PAGE-A"},
    )

    updated = wired.update_account(
        company_id=_alpha(platform),
        account_id=account["id"],
        values={"branch_id": alpha_branch},
    )

    assert updated["branch_id"] == alpha_branch


def test_a_branch_can_be_cleared(wired, platform, alpha_branch):
    account = wired.create_account(
        company_id=_alpha(platform),
        channel="messenger",
        name="Alpha page",
        values={"branch_id": alpha_branch, "page_id": "PAGE-A"},
    )

    updated = wired.update_account(
        company_id=_alpha(platform),
        account_id=account["id"],
        values={"branch_id": None},
    )

    assert updated["branch_id"] is None


def test_a_branch_id_that_is_not_a_number_is_refused(wired, platform):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="must be a number"):
        wired.create_account(
            company_id=_alpha(platform),
            channel="messenger",
            name="Alpha page",
            values={"branch_id": "; DROP TABLE branches", "page_id": "PAGE-A"},
        )


# ------------------------------------------------------------ the read is scoped


def test_a_foreign_branch_already_stored_shows_nothing(wired, platform, beta_branch):
    """Defence in depth for rows written before the check existed.

    The refusal above stops new ones. A database that has been running is
    exactly where the bad rows are, and they must not display either — so the
    join is scoped as well, and this test writes the row the old code would
    have written and asserts the name does not come back.
    """
    from database.manager import utc_now_iso

    manager = platform["manager"]
    now = utc_now_iso()

    with manager.control() as conn:
        conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, branch_id, channel, name, page_id, status,
                created_at, updated_at
            )
            VALUES (?, ?, 'messenger', 'Alpha page', 'PAGE-A', 'active', ?, ?)
            """,
            (_alpha(platform), beta_branch, now, now),
        )
        conn.commit()

    listed = wired.list_accounts(_alpha(platform))

    assert len(listed) == 1
    assert listed[0]["branch_name"] is None, (
        f"Beta's branch name reached Alpha's channel list: {listed[0]['branch_name']}"
    )


# ------------------------------------------------------- nothing can create one


def test_nothing_can_create_a_branch():
    """Recorded rather than fixed, and deliberately so.

    `branches` is read in four places and written in none. Two screens already
    render it: the Roles & Permissions screen has a branch dropdown for every
    team member, and the Channels form asks for a branch. Both are permanently
    empty, because no endpoint, service or CLI command inserts a row.

    That is a real gap and it is written down here rather than closed, because
    closing it means a screen for creating branches and the design is frozen by
    instruction. The security half — a branch id that is not yours — is fixed
    above and does not wait for it.

    When branch management is built, delete this test.
    """
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent

    result = subprocess.run(
        [
            "grep", "-rEl", r"INSERT INTO branches",
            "--include=*.py",
            "backend", "core", "channels", "gateway", "tools", "database",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )

    writers = sorted(result.stdout.split())

    assert not writers, (
        "Something can create a branch now: "
        f"{writers}. Delete this test — the gap it records is closed."
    )
