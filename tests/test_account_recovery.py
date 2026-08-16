"""Tests for account lockout and the way back in.

The design this defends, and why each half needs the other:

A locked account is locked for everyone, not just for the address that burned
the attempts. On its own that is a weapon — five requests naming a known
address would disable any employee on the platform, from anywhere, for free.
What makes it safe is that the lock does not have to be waited out: an
administrator holding `users.manage` sends a reset link, and the employee is
back in minutes.

So the tests come in pairs. The lock must actually bite, and the recovery must
actually work. Either one alone is a defect.

There was no test for any of this before. The throttle that shipped counted
`email = ? OR ip_address = ?` in a single query, which is how a remote lockout
of a named employee became possible in the first place.
"""

from __future__ import annotations

import pytest


EMPLOYEE_PASSWORD = "EmployeePass12345"
NEW_PASSWORD = "BrandNewPass9876"


@pytest.fixture()
def service(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.dashboard  # noqa: F401
    import backend.api.routes.roles  # noqa: F401
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

    from backend.api.routes import auth, dashboard, roles

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(roles.router)
    app.include_router(dashboard.router)

    return TestClient(app)


def _employee(service, company, email, role_code="agent") -> int:
    user_id = service.create_user(email, EMPLOYEE_PASSWORD, "Test Person")
    service.assign_user_to_company(user_id, company["id"], role_code)
    return user_id


def _login(client, company, email, password=EMPLOYEE_PASSWORD, ip="10.0.0.1"):
    return client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"],
            "company": company["name"],
            "email": email,
            "password": password,
        },
        headers={"X-Forwarded-For": ip},
    )


def _burn(client, company, email, times, ip="10.0.0.1"):
    for _ in range(times):
        _login(client, company, email, password="WrongPassword999", ip=ip)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ----------------------------------------------------------------------
# The lock bites
# ----------------------------------------------------------------------


def test_five_failures_lock_the_account(client, service, alpha):
    _employee(service, alpha, "lock1@alpha.example.com")

    _burn(client, alpha, "lock1@alpha.example.com", 5)

    refused = _login(client, alpha, "lock1@alpha.example.com")

    assert refused.status_code == 429, refused.text
    assert "locked" in refused.json()["detail"].lower()


def test_the_correct_password_does_not_open_a_locked_account(client, service, alpha):
    """Otherwise the lock would only inconvenience someone who is guessing."""
    _employee(service,alpha, "lock2@alpha.example.com")

    _burn(client, alpha, "lock2@alpha.example.com", 5)

    assert _login(client, alpha, "lock2@alpha.example.com").status_code == 429


def test_a_locked_account_is_told_so_and_told_what_to_do(client, service, alpha):
    """Every other failure answers with one deliberate non-answer. This one is
    the exception: reaching it proves the account exists, so withholding the
    reason would mislead the employee and protect nobody."""
    _employee(service, alpha, "lock3@alpha.example.com")
    _burn(client, alpha, "lock3@alpha.example.com", 5)

    detail = _login(client, alpha, "lock3@alpha.example.com").json()["detail"]

    assert "administrator" in detail.lower()
    assert "reset" in detail.lower()


def test_a_wrong_password_still_says_nothing_before_the_lock(client, service, alpha):
    _employee(service, alpha, "quiet@alpha.example.com")

    response = _login(client, alpha, "quiet@alpha.example.com", password="Nope12345678")

    assert response.status_code == 401
    detail = response.json()["detail"]
    assert "locked" not in detail.lower()
    assert "quiet@alpha.example.com" not in detail


def test_the_lock_carries_a_retry_after_header(client, service, alpha):
    _employee(service, alpha, "retry@alpha.example.com")
    _burn(client, alpha, "retry@alpha.example.com", 5)

    refused = _login(client, alpha, "retry@alpha.example.com")

    assert int(refused.headers["Retry-After"]) > 0


# ----------------------------------------------------------------------
# The lock does not spread — the defect this replaces
# ----------------------------------------------------------------------


def test_locking_one_employee_does_not_lock_another(client, service, alpha):
    """The old throttle counted `email = ? OR ip_address = ?` in one query, so
    failures against one account also blocked every other account reachable
    from that address. Both halves of that are tested here: the second employee
    signs in from the SAME address that was just used to lock the first."""
    _employee(service, alpha, "victim@alpha.example.com")
    _employee(service, alpha, "bystander@alpha.example.com")

    _burn(client, alpha, "victim@alpha.example.com", 5, ip="10.0.0.9")

    assert _login(client, alpha, "victim@alpha.example.com", ip="10.0.0.9").status_code == 429

    bystander = _login(
        client, alpha, "bystander@alpha.example.com", ip="10.0.0.9"
    )
    assert bystander.status_code == 200, bystander.text


def test_a_locked_account_stays_locked_from_a_different_address(
    client, service, alpha
):
    """The lock is on the account, which is what the owner chose. Anything
    weaker would let an attacker simply change address and keep guessing."""
    _employee(service, alpha, "moved@alpha.example.com")
    _burn(client, alpha, "moved@alpha.example.com", 5, ip="10.0.0.2")

    assert _login(client, alpha, "moved@alpha.example.com", ip="203.0.113.7").status_code == 429


# ----------------------------------------------------------------------
# Recovery: the administrator sends a link
# ----------------------------------------------------------------------


def test_an_administrator_can_send_a_reset_link_and_it_unlocks(
    client, service, alpha
):
    """This is what makes the full account lock safe rather than a weapon."""
    user_id = _employee(service, alpha, "locked@alpha.example.com")
    _employee(service, alpha, "boss@alpha.example.com", role_code="owner")

    _burn(client, alpha, "locked@alpha.example.com", 5)
    assert _login(client, alpha, "locked@alpha.example.com").status_code == 429

    boss = _login(client, alpha, "boss@alpha.example.com").json()["access_token"]

    sent = client.post(
        f"/api/admin/access/users/{user_id}/force-password-reset",
        headers=_bearer(boss),
    )
    assert sent.status_code == 200, sent.text

    # The lock is gone. The old password no longer works, because the reset
    # revoked it — but the account is reachable again through the link.
    token = service.create_password_reset(user_id=user_id)
    used = client.post(
        f"/api/auth/password/reset/{token}", json={"new_password": NEW_PASSWORD}
    )
    assert used.status_code == 200, used.text

    assert _login(
        client, alpha, "locked@alpha.example.com", password=NEW_PASSWORD
    ).status_code == 200


def test_an_administrator_can_unlock_without_touching_the_password(
    client, service, alpha
):
    """For the ordinary case — somebody mistyped five times and remembers their
    password perfectly well. Forcing a reset on them would be theatre."""
    user_id = _employee(service, alpha, "typo@alpha.example.com")
    _employee(service, alpha, "boss2@alpha.example.com", role_code="owner")

    _burn(client, alpha, "typo@alpha.example.com", 5)
    boss = _login(client, alpha, "boss2@alpha.example.com").json()["access_token"]

    unlocked = client.post(
        f"/api/admin/access/users/{user_id}/unlock", headers=_bearer(boss)
    )
    assert unlocked.status_code == 200, unlocked.text

    assert _login(client, alpha, "typo@alpha.example.com").status_code == 200


def test_an_ordinary_employee_cannot_reset_a_colleague(client, service, alpha):
    victim = _employee(service, alpha, "target@alpha.example.com")
    _employee(service, alpha, "nosy@alpha.example.com", role_code="agent")

    token = _login(client, alpha, "nosy@alpha.example.com").json()["access_token"]

    refused = client.post(
        f"/api/admin/access/users/{victim}/force-password-reset",
        headers=_bearer(token),
    )

    assert refused.status_code == 403, refused.text


def test_an_administrator_cannot_reset_someone_in_another_company(
    client, service, alpha, beta
):
    """`users` is a shared control-plane table and the id comes from the URL, so
    without a membership check `users.manage` in one company would be a lever on
    every account on the platform."""
    outsider = _employee(service, beta, "outsider@beta.example.com")
    _employee(service, alpha, "boss3@alpha.example.com", role_code="owner")

    boss = _login(client, alpha, "boss3@alpha.example.com").json()["access_token"]

    refused = client.post(
        f"/api/admin/access/users/{outsider}/force-password-reset",
        headers=_bearer(boss),
    )

    assert refused.status_code == 404, refused.text


# ----------------------------------------------------------------------
# Reset tokens
# ----------------------------------------------------------------------


def test_a_reset_token_works_exactly_once(client, service, alpha):
    user_id = _employee(service, alpha, "once@alpha.example.com")
    token = service.create_password_reset(user_id=user_id)

    first = client.post(
        f"/api/auth/password/reset/{token}", json={"new_password": NEW_PASSWORD}
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/auth/password/reset/{token}", json={"new_password": "AnotherPass1234"}
    )
    assert second.status_code == 400, second.text


def test_issuing_a_new_token_spends_the_previous_one(client, service, alpha):
    """Two live links for one account is a second key nobody is tracking."""
    user_id = _employee(service, alpha, "twice@alpha.example.com")

    first_token = service.create_password_reset(user_id=user_id)
    service.create_password_reset(user_id=user_id)

    stale = client.post(
        f"/api/auth/password/reset/{first_token}",
        json={"new_password": NEW_PASSWORD},
    )
    assert stale.status_code == 400, stale.text


def test_an_unknown_token_is_refused_the_same_way_as_a_spent_one(client, alpha):
    response = client.post(
        "/api/auth/password/reset/not-a-real-token",
        json={"new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400
    assert "no longer valid" in response.json()["detail"].lower()


def test_the_raw_token_is_never_stored(service, platform, alpha):
    """Somebody reading the table should learn that a reset was issued and to
    whom, and be unable to use it."""
    user_id = _employee(service, alpha, "hashed@alpha.example.com")
    token = service.create_password_reset(user_id=user_id)

    with platform["manager"].control() as conn:
        rows = conn.execute("SELECT * FROM password_reset_tokens").fetchall()

    assert rows
    for row in rows:
        assert token not in " ".join(str(value) for value in dict(row).values())


# ----------------------------------------------------------------------
# Changing a password ends the sessions that used the old one
# ----------------------------------------------------------------------


def test_a_reset_ends_every_existing_session(client, service, alpha):
    """A password is changed because somebody else may know it. Leaving their
    session alive would make the change cosmetic for the rest of the day."""
    user_id = _employee(service, alpha, "sessions@alpha.example.com")
    token = _login(client, alpha, "sessions@alpha.example.com").json()["access_token"]

    assert client.get("/api/dashboard/summary", headers=_bearer(token)).status_code == 200

    reset = service.create_password_reset(user_id=user_id)
    client.post(f"/api/auth/password/reset/{reset}", json={"new_password": NEW_PASSWORD})

    assert client.get("/api/dashboard/summary", headers=_bearer(token)).status_code == 401


def test_changing_your_own_password_requires_the_current_one(client, service, alpha):
    _employee(service, alpha, "self@alpha.example.com")
    token = _login(client, alpha, "self@alpha.example.com").json()["access_token"]

    refused = client.post(
        "/api/auth/password",
        headers=_bearer(token),
        json={"current_password": "NotMyPassword1", "new_password": NEW_PASSWORD},
    )

    assert refused.status_code == 400, refused.text


def test_changing_your_own_password_works_and_ends_the_session(
    client, service, alpha
):
    _employee(service, alpha, "self2@alpha.example.com")
    token = _login(client, alpha, "self2@alpha.example.com").json()["access_token"]

    changed = client.post(
        "/api/auth/password",
        headers=_bearer(token),
        json={
            "current_password": EMPLOYEE_PASSWORD,
            "new_password": NEW_PASSWORD,
        },
    )
    assert changed.status_code == 200, changed.text

    assert client.get("/api/dashboard/summary", headers=_bearer(token)).status_code == 401
    assert _login(
        client, alpha, "self2@alpha.example.com", password=NEW_PASSWORD
    ).status_code == 200


# ----------------------------------------------------------------------
# A forced change is enforced by the server, not suggested by the screen
# ----------------------------------------------------------------------


def test_a_forced_change_blocks_every_other_route(client, service, alpha):
    user_id = _employee(service, alpha, "forced@alpha.example.com")

    service.set_password(
        user_id=user_id, new_password=NEW_PASSWORD, must_change=True
    )

    token = _login(
        client, alpha, "forced@alpha.example.com", password=NEW_PASSWORD
    ).json()["access_token"]

    blocked = client.get("/api/dashboard/summary", headers=_bearer(token))

    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["detail"]["code"] == "password_change_required"


def test_a_forced_change_leaves_the_identity_route_reachable(client, service, alpha):
    """`/api/auth/me` is the second exemption, and it has to be one: the
    interface cannot route somebody to the change screen without being able to
    ask who they are. While this was blocked, `must_change_password` was the one
    fact the client could not read, so it had to be inferred from the shape of a
    403 — a workaround that would have quietly broken the day the status code
    changed."""
    user_id = _employee(service, alpha, "identity@alpha.example.com")
    service.set_password(
        user_id=user_id, new_password=NEW_PASSWORD, must_change=True
    )

    token = _login(
        client, alpha, "identity@alpha.example.com", password=NEW_PASSWORD
    ).json()["access_token"]

    me = client.get("/api/auth/me", headers=_bearer(token))

    assert me.status_code == 200, me.text
    assert me.json()["user"]["must_change_password"] == 1


def test_a_forced_change_leaves_the_change_route_reachable(client, service, alpha):
    """Otherwise the requirement would be a locked door with no handle."""
    user_id = _employee(service, alpha, "forced2@alpha.example.com")
    service.set_password(
        user_id=user_id, new_password=NEW_PASSWORD, must_change=True
    )

    token = _login(
        client, alpha, "forced2@alpha.example.com", password=NEW_PASSWORD
    ).json()["access_token"]

    changed = client.post(
        "/api/auth/password",
        headers=_bearer(token),
        json={
            "current_password": NEW_PASSWORD,
            "new_password": "ThirdPassword123",
        },
    )
    assert changed.status_code == 200, changed.text

    # And the requirement is gone afterwards.
    fresh = _login(
        client, alpha, "forced2@alpha.example.com", password="ThirdPassword123"
    ).json()["access_token"]

    assert client.get("/api/dashboard/summary", headers=_bearer(fresh)).status_code == 200


# ----------------------------------------------------------------------
# Email that cannot be delivered is reported, not pretended
# ----------------------------------------------------------------------


def test_a_reset_is_refused_when_email_is_not_configured(
    client, service, alpha, monkeypatch
):
    """Reporting success for a mail nobody will receive leaves the
    administrator and the employee both believing the account is recoverable."""
    from config.settings import config

    user_id = _employee(service, alpha, "nomail@alpha.example.com")
    _employee(service, alpha, "boss4@alpha.example.com", role_code="owner")

    boss = _login(client, alpha, "boss4@alpha.example.com").json()["access_token"]

    monkeypatch.setattr(config, "EMAIL_BACKEND", "disabled")

    refused = client.post(
        f"/api/admin/access/users/{user_id}/force-password-reset",
        headers=_bearer(boss),
    )

    assert refused.status_code == 503, refused.text
    assert "manage_platform" in refused.json()["detail"]


def test_the_address_threshold_stays_above_the_account_threshold():
    """A guard on a configuration mistake that reintroduces a fixed bug.

    Both thresholds were briefly the same value while this was being written,
    and the effect was invisible in isolation: locking one account also
    throttled the address it was locked from, so the colleague at the next desk
    was refused too. That is the collateral damage the separate counters exist
    to prevent, arriving through the other door.

    The gap is the feature. This test states the invariant so that lowering
    LOGIN_ADDRESS_MAX_ATTEMPTS to "tighten security" fails loudly instead of
    quietly locking out offices.
    """
    from config.settings import config

    assert config.LOGIN_ADDRESS_MAX_ATTEMPTS > config.LOGIN_MAX_ATTEMPTS


def test_an_office_sharing_an_address_survives_several_locked_colleagues(
    client, service, alpha
):
    """Three colleagues each burning their five attempts from one office is
    fifteen failures on that address — under the twenty that throttles it — so
    the fourth colleague still signs in."""
    for index in range(3):
        email = f"office{index}@alpha.example.com"
        _employee(service, alpha, email)
        _burn(client, alpha, email, 5, ip="198.51.100.4")

    _employee(service, alpha, "office-late@alpha.example.com")

    late = _login(
        client, alpha, "office-late@alpha.example.com", ip="198.51.100.4"
    )

    assert late.status_code == 200, late.text
