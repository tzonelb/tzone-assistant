"""Tests for the boundary between running the platform and reading its data.

The owner's decision: a platform administrator manages companies but cannot read
their customers' conversations. The server holds the master key, so it *can*
open any database unattended — that is what keeps the assistant answering at
3am. What must not follow is that a person holding platform credentials can read
whatever they like.

These tests hold that line. Without them the encryption protects a tenant from a
stolen disk and from nobody else.
"""

from __future__ import annotations

import pytest

from backend.services.auth_service import COMPANY_SCOPE, PLATFORM_SCOPE
from fastapi import HTTPException


@pytest.fixture()
def auth(platform, monkeypatch):
    """Point the auth service at the test platform's databases."""
    import sys

    import backend.services.auth_service  # noqa: F401
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.auth_service" in rebound

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def people(auth, alpha, beta):
    """An owner in each company, plus a platform administrator."""
    alpha_owner = auth.create_user("owner@alpha.test", "AlphaPass123!", "Alpha Owner")
    beta_owner = auth.create_user("owner@beta.test", "BetaPass123!", "Beta Owner")
    admin = auth.create_user(
        "admin@platform.test", "PlatformPass123!", "Platform Admin", is_super_admin=True
    )

    for company, user_id in ((alpha, alpha_owner), (beta, beta_owner)):
        auth.assign_user_to_company(user_id, company["id"], "owner")

    # The administrator is also a member of alpha, which is the interesting
    # case: being a super admin must not widen that to beta.
    auth.assign_user_to_company(admin, alpha["id"], "owner")

    return {"alpha_owner": alpha_owner, "beta_owner": beta_owner, "admin": admin}


# ----------------------------------------------------------------------
# Platform sign-in
# ----------------------------------------------------------------------


def test_platform_login_needs_no_workspace_code(auth, people):
    """A platform session never opens a company database, so there is nothing
    for a code to unlock. Demanding one would be theatre."""
    user = auth.authenticate_platform(
        email="admin@platform.test", password="PlatformPass123!"
    )

    assert user is not None
    assert user["is_super_admin"] is True


def test_an_ordinary_employee_cannot_get_a_platform_session(auth, people):
    """Otherwise any company owner could administer the whole platform."""
    assert (
        auth.authenticate_platform(
            email="owner@alpha.test", password="AlphaPass123!"
        )
        is None
    )


def test_platform_login_still_checks_the_password(auth, people):
    """Being a super admin is not a substitute for proving who you are."""
    assert (
        auth.authenticate_platform(
            email="admin@platform.test", password="WrongPassword123!"
        )
        is None
    )


# ----------------------------------------------------------------------
# The two token kinds do not interchange
# ----------------------------------------------------------------------


def test_a_platform_token_carries_the_platform_scope(auth, people):
    """The scope has to survive the round trip through the database, or every
    check built on it silently passes."""
    session = auth.create_session(
        user_id=people["admin"], scope=PLATFORM_SCOPE, company_id=None
    )
    user = auth.get_user_from_token(session["access_token"])

    assert user["session_scope"] == PLATFORM_SCOPE
    assert user["active_company_id"] is None


def test_a_company_token_carries_the_company_scope(auth, people, alpha):
    session = auth.create_session(
        user_id=people["alpha_owner"], company_id=alpha["id"]
    )
    user = auth.get_user_from_token(session["access_token"])

    assert user["session_scope"] == COMPANY_SCOPE
    assert user["active_company_id"] == alpha["id"]


def test_a_platform_session_cannot_resolve_a_company(auth, people, alpha):
    """This is the check that stops a platform token reading customer data:
    every customer endpoint resolves a company first, and a platform session
    has none and may not acquire one."""
    session = auth.create_session(
        user_id=people["admin"], scope=PLATFORM_SCOPE, company_id=None
    )
    user = auth.get_user_from_token(session["access_token"])

    with pytest.raises(HTTPException) as raised:
        auth.resolve_company_id(user)

    assert raised.value.status_code == 403

    with pytest.raises(HTTPException):
        auth.resolve_company_id(user, requested_company_id=alpha["id"])


# ----------------------------------------------------------------------
# A super admin gets no blanket reach across companies
# ----------------------------------------------------------------------


def test_a_super_admin_reaches_only_the_company_they_signed_into(
    auth, people, alpha, beta
):
    """The defect this replaces: a super admin signed into one company could
    pass another company's id and read its data, holding only the first
    company's workspace code."""
    session = auth.create_session(user_id=people["admin"], company_id=alpha["id"])
    user = auth.get_user_from_token(session["access_token"])

    assert auth.resolve_company_id(user) == alpha["id"]

    with pytest.raises(HTTPException) as raised:
        auth.resolve_company_id(user, requested_company_id=beta["id"])

    assert raised.value.status_code == 403


def test_a_super_admin_may_still_name_their_own_company(auth, people, alpha):
    """Passing the id you are already signed into is not an escalation, and
    refusing it would break every screen that sends the company explicitly."""
    session = auth.create_session(user_id=people["admin"], company_id=alpha["id"])
    user = auth.get_user_from_token(session["access_token"])

    assert auth.resolve_company_id(user, requested_company_id=alpha["id"]) == alpha["id"]


def test_a_super_admin_reaches_a_second_company_by_signing_into_it(
    auth, people, alpha, beta, platform
):
    """Access is gated on membership plus a correct sign-in, for the operator
    exactly as for an employee."""
    auth.assign_user_to_company(people["admin"], beta["id"], "owner")

    user = auth.authenticate(
        company="beta",
        email="admin@platform.test",
        password="PlatformPass123!",
    )

    assert user is not None
    assert user["active_company_id"] == beta["id"]

    session = auth.create_session(
        user_id=people["admin"], company_id=beta["id"]
    )
    signed_in = auth.get_user_from_token(session["access_token"])

    assert auth.resolve_company_id(signed_in) == beta["id"]


def test_an_ordinary_employee_still_cannot_reach_another_company(
    auth, people, alpha, beta
):
    """The rule that already held must keep holding after the rewrite."""
    session = auth.create_session(
        user_id=people["alpha_owner"], company_id=alpha["id"]
    )
    user = auth.get_user_from_token(session["access_token"])

    with pytest.raises(HTTPException) as raised:
        auth.resolve_company_id(user, requested_company_id=beta["id"])

    assert raised.value.status_code == 403
