"""Tests for the second factor.

The account this exists for is the Super Admin. Its sign-in is deliberately one
factor — an email and a password, with no workspace code, because a platform
administrator belongs to no company and has no code to type. It is also the
account that suspends companies, rotates workspace codes and reads the platform
audit. One guessed or reused password is the whole platform.

So enrolment is mandatory there and optional everywhere else, and the tests
below are mostly about the ways that could be got around: a session minted
before enrolment, a code replayed, a recovery code spent twice, a secret lifted
from one row onto another, or the whole thing switched off by somebody who
walked up to an unlocked screen.
"""

from __future__ import annotations

import pytest

import pyotp


PASSWORD = "PlatformPass123!"


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.platform  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.totp_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.totp_service" in rebound
    assert "backend.services.auth_service" in rebound

    from backend.services.totp_service import totp_service

    return totp_service


@pytest.fixture()
def client(wired):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, platform as platform_routes

    app = FastAPI()
    app.include_router(platform_routes.router)
    app.include_router(auth.router)

    return TestClient(app)


@pytest.fixture()
def admin(wired):
    from backend.services.auth_service import auth_service

    return auth_service.create_user(
        email="root@platform.example.com",
        password=PASSWORD,
        full_name="Platform Root",
        is_super_admin=True,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sign_in(client, *, code: str | None = None) -> dict:
    body = {"email": "root@platform.example.com", "password": PASSWORD}

    if code is not None:
        body["totp_code"] = code

    return client.post("/api/platform/auth/login", json=body)


def _enrol(client, wired, admin) -> tuple[str, str, list[str]]:
    """Take one administrator all the way through enrolment.

    Returns the token, the secret, and the recovery codes.
    """
    token = _sign_in(client).json()["access_token"]

    begun = client.post("/api/platform/auth/totp/begin", headers=_bearer(token))
    assert begun.status_code == 200, begun.text
    secret = begun.json()["secret"]

    confirmed = client.post(
        "/api/platform/auth/totp/confirm",
        headers=_bearer(token),
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert confirmed.status_code == 200, confirmed.text

    return token, secret, confirmed.json()["recovery_codes"]


# ------------------------------------------------------------------ the gate


def test_an_unenrolled_administrator_can_sign_in_but_reach_nothing(
    client, wired, admin
):
    """The session is minted so they can reach enrolment. Refusing the token
    outright would leave them with no way to satisfy the requirement."""
    response = _sign_in(client)

    assert response.status_code == 200, response.text
    assert response.json()["totp"]["enrolment_pending"] is True

    token = response.json()["access_token"]

    blocked = client.get("/api/platform/companies", headers=_bearer(token))

    assert blocked.status_code == 403
    assert "totp_enrolment_required" in blocked.text


def test_enrolment_is_reachable_before_enrolment(client, wired, admin):
    token = _sign_in(client).json()["access_token"]

    assert (
        client.get("/api/platform/auth/totp", headers=_bearer(token)).status_code
        == 200
    )


def test_the_console_opens_once_enrolment_is_finished(client, wired, admin):
    token, _secret, _codes = _enrol(client, wired, admin)

    assert (
        client.get("/api/platform/companies", headers=_bearer(token)).status_code
        == 200
    )


def test_a_second_sign_in_now_demands_a_code(client, wired, admin):
    _enrol(client, wired, admin)

    assert _sign_in(client).status_code == 401
    assert "totp_required" in _sign_in(client).text


def test_the_right_code_signs_in(client, wired, admin):
    _token, secret, _codes = _enrol(client, wired, admin)

    response = _sign_in(client, code=pyotp.TOTP(secret).now())

    assert response.status_code == 200, response.text


def test_a_wrong_code_is_refused(client, wired, admin):
    _enrol(client, wired, admin)

    assert _sign_in(client, code="000000").status_code == 401


# ----------------------------------------------------------------- enrolment


def test_the_secret_is_returned_once_and_never_again(client, wired, admin):
    token, secret, _codes = _enrol(client, wired, admin)

    status = client.get("/api/platform/auth/totp", headers=_bearer(token)).json()

    assert secret not in str(status)
    assert "secret" not in status


def test_the_secret_is_not_stored_in_the_clear(client, wired, platform, admin):
    """Anyone holding it can generate this account's codes for ever, so a
    readable copy would mean a database dump hands over the second factor along
    with the first."""
    _token, secret, _codes = _enrol(client, wired, admin)

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT totp_secret_sealed FROM users WHERE id = ?", (admin,)
        ).fetchone()

    assert row["totp_secret_sealed"]
    assert secret not in str(row["totp_secret_sealed"])


def test_a_sealed_secret_cannot_be_moved_to_another_account(
    client, wired, platform, admin
):
    """The AAD names the account it was issued to, so a row lifted from one
    user fails to decrypt on another rather than silently accepting somebody
    else's second factor."""
    from backend.services.auth_service import auth_service

    _token, secret, _codes = _enrol(client, wired, admin)

    victim = auth_service.create_user(
        email="other@platform.example.com", password=PASSWORD, full_name="Other"
    )

    with platform["manager"].control() as conn:
        sealed = conn.execute(
            "SELECT totp_secret_sealed FROM users WHERE id = ?", (admin,)
        ).fetchone()["totp_secret_sealed"]

        conn.execute(
            "UPDATE users SET totp_secret_sealed = ?, totp_enabled = 1 WHERE id = ?",
            (sealed, victim),
        )
        conn.commit()

    assert wired.verify(victim, pyotp.TOTP(secret).now()) is False


def test_confirming_with_a_wrong_code_does_not_turn_it_on(client, wired, admin):
    token = _sign_in(client).json()["access_token"]
    client.post("/api/platform/auth/totp/begin", headers=_bearer(token))

    response = client.post(
        "/api/platform/auth/totp/confirm",
        headers=_bearer(token),
        json={"code": "000000"},
    )

    assert response.status_code == 400
    assert wired.status(admin)["enabled"] is False


def test_confirming_without_beginning_is_refused(client, wired, admin):
    token = _sign_in(client).json()["access_token"]

    response = client.post(
        "/api/platform/auth/totp/confirm",
        headers=_bearer(token),
        json={"code": "123456"},
    )

    assert response.status_code == 400


def test_starting_again_discards_the_first_secret(client, wired, admin):
    """So an abandoned attempt cannot be resumed by somebody who photographed
    the first QR."""
    token = _sign_in(client).json()["access_token"]

    first = client.post(
        "/api/platform/auth/totp/begin", headers=_bearer(token)
    ).json()["secret"]
    second = client.post(
        "/api/platform/auth/totp/begin", headers=_bearer(token)
    ).json()["secret"]

    assert first != second

    stale = client.post(
        "/api/platform/auth/totp/confirm",
        headers=_bearer(token),
        json={"code": pyotp.TOTP(first).now()},
    )

    assert stale.status_code == 400


def test_the_enrolment_response_carries_a_scannable_qr(client, wired, admin):
    token = _sign_in(client).json()["access_token"]

    body = client.post(
        "/api/platform/auth/totp/begin", headers=_bearer(token)
    ).json()

    assert body["qr_svg"].lstrip().startswith("<svg")
    assert body["uri"].startswith("otpauth://totp/")


# ------------------------------------------------------------ recovery codes


def test_recovery_codes_are_returned_once(client, wired, admin):
    _token, _secret, codes = _enrol(client, wired, admin)

    assert len(codes) == 10
    assert len(set(codes)) == 10


def test_recovery_codes_are_stored_hashed(client, wired, platform, admin):
    """A readable copy would be a second permanent copy of the second factor."""
    _token, _secret, codes = _enrol(client, wired, admin)

    with platform["manager"].control() as conn:
        stored = conn.execute(
            "SELECT totp_recovery_hashes FROM users WHERE id = ?", (admin,)
        ).fetchone()["totp_recovery_hashes"]

    for code in codes:
        assert code not in str(stored)


def test_a_recovery_code_signs_in(client, wired, admin):
    _token, _secret, codes = _enrol(client, wired, admin)

    assert _sign_in(client, code=codes[0]).status_code == 200


def test_a_recovery_code_cannot_be_used_twice(client, wired, admin):
    _token, _secret, codes = _enrol(client, wired, admin)

    assert _sign_in(client, code=codes[0]).status_code == 200
    assert _sign_in(client, code=codes[0]).status_code == 401


def test_spending_a_recovery_code_leaves_the_others(client, wired, admin):
    _token, _secret, codes = _enrol(client, wired, admin)

    _sign_in(client, code=codes[0])

    assert wired.status(admin)["recovery_codes_remaining"] == 9
    assert _sign_in(client, code=codes[1]).status_code == 200


# ------------------------------------------------------------ turning it off


def test_an_administrator_may_not_turn_their_own_off(client, wired, admin):
    """Their sign-in is one factor by design, and this is what makes it two."""
    from backend.services.totp_service import TotpError

    _enrol(client, wired, admin)

    with pytest.raises(TotpError):
        wired.disable(admin)

    assert wired.status(admin)["enabled"] is True


def test_the_server_command_can_clear_it(client, wired, admin):
    """The emergency exit. An administrator who has lost both their device and
    their recovery codes cannot be helped from inside the product, and without
    this the platform would be permanently unadministrable after a lost phone.

    The account is left unenrolled rather than exempt.
    """
    _enrol(client, wired, admin)

    wired.disable(admin, force=True)

    status = wired.status(admin)
    assert status["enabled"] is False
    assert status["enrolment_pending"] is True


def test_a_company_account_may_turn_it_off_with_a_code(
    client, wired, platform, alpha
):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="agent@alpha.example.com", password=PASSWORD, full_name="An Agent"
    )

    with platform["manager"].control() as conn:
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, NULL, 'active', ?)
            """,
            (alpha["id"], user_id, utc_now_iso()),
        )
        conn.commit()

    login = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "agent@alpha.example.com",
            "password": PASSWORD,
        },
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    secret = client.post("/api/auth/totp/begin", headers=_bearer(token)).json()[
        "secret"
    ]
    client.post(
        "/api/auth/totp/confirm",
        headers=_bearer(token),
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert wired.status(user_id)["enabled"] is True

    # A code is required to remove it: without one, anybody who walked up to an
    # unlocked screen could strip the protection with a click, making the second
    # factor only as strong as the session it exists to defend.
    refused = client.request(
        "DELETE",
        "/api/auth/totp",
        headers=_bearer(token),
        json={"code": "000000"},
    )
    assert refused.status_code == 401
    assert wired.status(user_id)["enabled"] is True

    removed = client.request(
        "DELETE",
        "/api/auth/totp",
        headers=_bearer(token),
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert removed.status_code == 200, removed.text
    assert wired.status(user_id)["enabled"] is False


def test_a_company_account_is_not_required_to_enrol(client, wired, platform, alpha):
    """Optional on a company account and mandatory only for a platform
    administrator: the platform decides what protects the platform, and the
    company's owner decides what protects the company."""
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="agent2@alpha.example.com", password=PASSWORD, full_name="Another"
    )

    with platform["manager"].control() as conn:
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, NULL, 'active', ?)
            """,
            (alpha["id"], user_id, utc_now_iso()),
        )
        conn.commit()

    assert wired.status(user_id)["required"] is False
    assert wired.status(user_id)["enrolment_pending"] is False


# ------------------------------------------------------------- the lockout


def test_a_wrong_code_counts_toward_the_lockout(client, wired, admin):
    """Otherwise an attacker who already has the password gets unlimited
    guesses at six digits."""
    _enrol(client, wired, admin)

    for _ in range(6):
        _sign_in(client, code="000000")

    assert _sign_in(client, code="000000").status_code == 429


# --------------------------------------------------------------- robustness


def test_verification_fails_closed_on_a_database_error(wired, admin, monkeypatch):
    """The opposite direction from almost every other guard here, and
    deliberately so. The others fail open because refusing would deny a
    customer work they are entitled to; allowing here would admit somebody who
    has not proved their second factor."""
    import backend.services.totp_service as module
    from database.manager import DatabaseError

    class Exploding:
        def control(self):
            raise DatabaseError("control plane is unavailable")

        def master_key(self):
            raise DatabaseError("control plane is unavailable")

    monkeypatch.setattr(module, "database_manager", Exploding())

    assert module.totp_service.verify(admin, "123456") is False


def test_an_empty_code_is_refused_without_touching_the_database(wired, admin):
    assert wired.verify(admin, "") is False
    assert wired.verify(admin, "   ") is False


def test_verifying_an_account_with_no_second_factor_is_false(wired, admin):
    assert wired.verify(admin, "123456") is False
