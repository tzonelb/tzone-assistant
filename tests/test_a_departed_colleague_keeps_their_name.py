"""Leaving a company does not erase who you were while you were there.

`user_display_names` is how every screen turns a `sender_user_id`,
`assigned_user_id` or `created_by_user_id` into a person: conversations,
tickets, appointments, team chat, comments, the scheduler, analytics. Tenant
rows cannot join to `users` — different files — so the id is carried and the
name resolved separately.

It used to require `users.status = 'active'` and
`company_users.status = 'active'`. So the moment an owner disabled an employee,
every task that employee had been assigned, every note they had written and
every reply they had sent went blank across all twenty-three call sites at
once. The row still held the id; only the name refused. The company lost the
authorship of its own history because somebody stopped working there.

Withholding it protected nothing — they were an employee of this company and
their name is already through its records.

What must NOT change is the scoping, and it is a different clause:
`company_users.company_id = ?`. `ticket_service` and `appointment_service` both
say in as many words that they rely on a foreign id resolving to no name. The
last test here is that guarantee, because a fix that widened it would be worse
than the defect.
"""

from __future__ import annotations

import pytest

from database.manager import utc_now_iso

# Imported here, at module scope, and NOT inside a fixture or a test.
#
# Every helper below needs `auth_service`, and the `wired` fixture rebinds
# `database_manager` on every module that holds one. A module first imported
# *while* that patch is active captures this test's temporary manager as its
# original value, so `monkeypatch` "restores" it to exactly that at teardown —
# and every later test file in the run then talks to a directory that no longer
# exists. This file really was the first importer of `auth_service` in a run,
# and it took `tests/test_roles_admin.py` down with it: eleven passes alone,
# one failure and ten errors when the two ran together.
#
# The same hazard is documented on `main` in
# `tests/test_declared_retention_is_actually_applied.py`. Importing before any
# patch exists is the whole fix.
from backend.services.auth_service import auth_service  # noqa: E402


PASSWORD = "ColleaguePass123!"


def _employ(platform, company_id, email, name, *, role_code="agent"):
    """Create a user and make them an employee of one company."""
    user_id = auth_service.create_user(
        email=email, password=PASSWORD, full_name=name
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = ? LIMIT 1",
            (int(company_id), role_code),
        ).fetchone()

        assert role, f"company {company_id} has no {role_code} role to employ into"

        conn.execute(
            """
            INSERT INTO company_users (
                company_id, user_id, role_id, status, created_at
            )
            VALUES (?, ?, ?, 'active', ?)
            """,
            (int(company_id), int(user_id), int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    return int(user_id)


def _set_membership(platform, company_id, user_id, status):
    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE company_users SET status = ? WHERE company_id = ? AND user_id = ?",
            (status, int(company_id), int(user_id)),
        )
        conn.commit()


def _set_account(platform, user_id, status):
    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE users SET status = ? WHERE id = ?", (status, int(user_id))
        )
        conn.commit()


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    from database.manager import DatabaseManager

    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    return test_manager


def test_an_active_colleague_resolves(wired, platform, alpha):
    """The control. Everything below is about a name still resolving, which
    proves nothing unless it resolves to begin with."""
    user_id = _employ(platform, alpha["id"], "present@alpha.example.com", "Present")

    assert auth_service.user_display_names(alpha["id"], [user_id]) == {
        user_id: "Present"
    }


def test_a_disabled_membership_still_resolves(wired, platform, alpha):
    user_id = _employ(platform, alpha["id"], "left@alpha.example.com", "Departed")
    _set_membership(platform, alpha["id"], user_id, "disabled")

    assert auth_service.user_display_names(alpha["id"], [user_id]) == {
        user_id: "Departed"
    }, (
        "disabling an employee blanked their name, so every task, note and "
        "reply they ever touched now shows as nobody"
    )


def test_a_disabled_account_still_resolves(wired, platform, alpha):
    """The other status column, which fails the same way on its own."""
    user_id = _employ(platform, alpha["id"], "gone@alpha.example.com", "Gone")
    _set_account(platform, user_id, "disabled")

    assert auth_service.user_display_names(alpha["id"], [user_id]) == {
        user_id: "Gone"
    }


def test_a_departed_colleague_is_not_offered_for_new_work(wired, platform, alpha):
    """The half that must keep filtering.

    Attribution asks who it was; an assignment picker asks who is here now.
    Those are different questions and `company_employees` answers the second.
    """
    user_id = _employ(platform, alpha["id"], "past@alpha.example.com", "Past")
    _set_membership(platform, alpha["id"], user_id, "disabled")

    offered = {
        int(person["id"]) for person in auth_service.company_employees(alpha["id"])
    }

    assert user_id not in offered, (
        "a departed colleague is still offered as somebody to assign work to"
    )


def test_another_companys_employee_still_resolves_to_nothing(
    wired, platform, alpha, beta
):
    """The guarantee that must survive the fix.

    `ticket_service` and `appointment_service` both rely on a foreign id
    resolving to no name. The scoping is `company_users.company_id`, not the
    status columns — widening it here would be worse than the defect this file
    is about.
    """
    stranger = _employ(platform, beta["id"], "stranger@beta.example.com", "Stranger")

    assert auth_service.user_display_names(alpha["id"], [stranger]) == {}, (
        "one company resolved the name of another company's employee"
    )


def test_a_departed_colleague_of_another_company_also_resolves_to_nothing(
    wired, platform, alpha, beta
):
    """The combination, because the fix removed the status clauses and a
    disabled stranger is the case where a careless rewrite would leak."""
    stranger = _employ(platform, beta["id"], "exstranger@beta.example.com", "Ex")
    _set_membership(platform, beta["id"], stranger, "disabled")
    _set_account(platform, stranger, "disabled")

    assert auth_service.user_display_names(alpha["id"], [stranger]) == {}
