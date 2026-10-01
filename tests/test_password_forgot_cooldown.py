"""`/password/forgot` is unauthenticated and sends a real email on every
call. Nothing before this limited how often it could be asked for the same
account: a caller who knew (or was guessing) a real address could loop the
endpoint and flood that mailbox, draining the platform's send quota and
reputation along the way -- the same email-bombing shape
`signup_service.RESEND_COOLDOWN_SECONDS` already guards against for sign-up.

The cooldown has to fail the same way the rest of this endpoint does: the
response is the identical generic "if that email is registered" message
whether the address has no account, has an account with no cooldown active,
or has an account currently in cooldown. A distinguishable response for the
last case would let an unauthenticated caller learn "a reset was already
requested for this address a moment ago" -- which is itself an account-
existence signal, the exact thing this endpoint otherwise refuses to leak.
"""

from __future__ import annotations

import sys

import pytest


EMPLOYEE_PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.services.auth_service  # noqa: F401

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
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth

    app = FastAPI()
    app.include_router(auth.router)

    return TestClient(app)


def _employee(service, company, email) -> int:
    user_id = service.create_user(email, EMPLOYEE_PASSWORD, "Test Person")
    service.assign_user_to_company(user_id, company["id"], "agent")
    return user_id


def _forgot(client, email):
    return client.post("/api/auth/password/forgot", json={"email": email})


def test_a_second_request_within_the_cooldown_sends_no_new_link(
    client, service, alpha, platform
):
    _employee(service, alpha, "cooldown@alpha.example.com")

    first = _forgot(client, "cooldown@alpha.example.com")
    assert first.status_code == 200, first.text

    with platform["manager"].control() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM password_reset_tokens"
        ).fetchone()["n"]

    second = _forgot(client, "cooldown@alpha.example.com")
    assert second.status_code == 200, second.text
    assert second.json() == first.json(), (
        "a request inside the cooldown answered differently from one "
        "outside it -- that difference is itself an account-existence signal"
    )

    with platform["manager"].control() as conn:
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM password_reset_tokens"
        ).fetchone()["n"]

    assert after == before, (
        "a second request inside the cooldown minted another token -- "
        "each one sends a real email, so this is the email-bombing path"
    )


def test_a_request_after_the_cooldown_sends_a_new_link(
    client, service, alpha, platform, monkeypatch
):
    from config.settings import config

    monkeypatch.setattr(config, "PASSWORD_RESET_COOLDOWN_SECONDS", 0)

    _employee(service, alpha, "later@alpha.example.com")

    first = _forgot(client, "later@alpha.example.com")
    assert first.status_code == 200, first.text

    second = _forgot(client, "later@alpha.example.com")
    assert second.status_code == 200, second.text

    with platform["manager"].control() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM password_reset_tokens"
        ).fetchone()["n"]

    assert total == 2, (
        "a request outside the cooldown window was still silently dropped"
    )


def test_the_cooldown_is_per_account_not_platform_wide(
    client, service, alpha, platform
):
    """One address in cooldown must not silently block a different address
    asking a moment later."""
    _employee(service, alpha, "first@alpha.example.com")
    _employee(service, alpha, "second@alpha.example.com")

    _forgot(client, "first@alpha.example.com")
    second = _forgot(client, "second@alpha.example.com")
    assert second.status_code == 200, second.text

    with platform["manager"].control() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM password_reset_tokens"
        ).fetchone()["n"]

    assert total == 2, "the second account's own request was suppressed too"


def test_an_unregistered_address_answers_identically_whether_or_not_it_was_just_asked(
    client,
):
    """The cooldown check must never run for an address with no account --
    there is no user_id to key it on, and reaching for one would be a new
    enumeration path of its own."""
    first = _forgot(client, "nobody@nowhere.example.com")
    second = _forgot(client, "nobody@nowhere.example.com")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
