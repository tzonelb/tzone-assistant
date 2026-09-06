"""Sign up, look around, be refused a channel, redeem a code, connect one.

The three services underneath this each have their own tests. What this one
holds is the seam between them, over HTTP, in the order a real person meets it
-- because every part passing separately is exactly the state a platform is in
when the journey is still broken.

The step that matters is the fourth. A demonstration is only a demonstration
while it cannot reach somebody else's customers, and "cannot" has to survive
the whole path: a real router, a real session, a real permission check.
"""

from __future__ import annotations

import re
import sys

import pytest

# Before any fixture patches `database.manager.database_manager`.
import backend.api.routes.channels  # noqa: E402,F401
import backend.api.routes.signup  # noqa: E402,F401
import backend.services.activation_service  # noqa: E402,F401
import backend.services.mailer  # noqa: E402,F401
import backend.services.platform_service  # noqa: E402,F401
import backend.services.signup_service  # noqa: E402,F401


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from backend.services.demo_gate import demo_gate

    demo_gate.invalidate()
    yield test_manager
    demo_gate.invalidate()


@pytest.fixture()
def app_client(wired, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, channels, signup
    from backend.services import mailer

    sent: list[dict] = []

    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(
        mailer,
        "send",
        lambda **kwargs: sent.append(kwargs)
        or mailer.DeliveryResult(delivered=True, backend="test"),
    )

    app = FastAPI()
    app.include_router(signup.router)
    app.include_router(signup.activation_router)
    app.include_router(auth.router)
    app.include_router(channels.router)

    client = TestClient(app)
    client.mailbox = sent

    return client


def _code(client) -> str:
    match = re.search(r"\b(\d{6})\b", client.mailbox[-1]["body"])

    assert match, client.mailbox[-1]["body"]

    return match.group(1)


def test_the_whole_journey(app_client):
    from backend.services.activation_service import activation_service

    # 1. The plans are readable with no account, and carry nothing but what the
    #    screen prints.
    plans = app_client.get("/api/signup/plans")

    assert plans.status_code == 200

    for plan in plans.json()["plans"]:
        assert set(plan) <= {
            "id",
            "code",
            "name",
            "price_monthly",
            "currency",
            "max_users",
            "max_channel_accounts",
        }, plan

    # 2. A code arrives.
    assert app_client.post(
        "/api/signup/code", json={"email": "rana@cedarhome.example"}
    ).status_code == 200

    # 3. The workspace is created, and the owner is signed in without having to
    #    retype the password they just chose.
    created = app_client.post(
        "/api/signup",
        json={
            "company_name": "Cedar Home Appliances",
            "owner_full_name": "Rana",
            "owner_email": "rana@cedarhome.example",
            "password": "a-long-enough-password",
            "confirm_password": "a-long-enough-password",
            "email_code": _code(app_client),
        },
    )

    assert created.status_code == 201, created.text

    body = created.json()

    assert body["is_demo"] is True
    assert body["access_token"]

    # The cookie, not only the body -- the app reads the cookie.
    assert any(
        cookie.lower().startswith("tzone") or "session" in cookie.lower()
        for cookie in created.cookies.keys()
    ) or created.cookies, created.cookies

    company_id = body["company_id"]
    headers = {"Authorization": f"Bearer {body['access_token']}"}

    # 4. The demonstration cannot connect a channel. This is the whole point.
    refused = app_client.post(
        "/api/channels",
        headers=headers,
        json={"channel": "telegram", "name": "Sales", "access_token": "1:AA"},
    )

    assert refused.status_code == 403, refused.text
    assert "demonstration" in refused.json()["detail"].lower()

    # 5. A code turns it into a real workspace.
    minted = activation_service.mint()

    redeemed = app_client.post(
        "/api/activation/redeem",
        headers=headers,
        json={"code": minted["code"]},
    )

    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["activated"] is True

    # 6. And now the refusal is gone. Not asserting the connection succeeds --
    #    the credentials are invented -- only that this gate is no longer the
    #    thing standing in the way.
    from backend.services.demo_gate import demo_gate

    assert demo_gate.is_demo(company_id) is False

    after = app_client.post(
        "/api/channels",
        headers=headers,
        json={"channel": "telegram", "name": "Sales", "access_token": "1:AA"},
    )

    assert after.status_code != 403, after.text


def test_an_employee_without_the_permission_cannot_spend_the_code(app_client):
    """Redeeming is the owner's decision, not every colleague's.

    The code is single-use and irreversible; an employee spending it on the
    wrong plan is not something the owner can undo.
    """
    from backend.services.activation_service import activation_service
    from backend.services.auth_service import auth_service

    app_client.post("/api/signup/code", json={"email": "rana@cedarhome.example"})

    created = app_client.post(
        "/api/signup",
        json={
            "company_name": "Cedar Home Appliances",
            "owner_full_name": "Rana",
            "owner_email": "rana@cedarhome.example",
            "password": "a-long-enough-password",
            "confirm_password": "a-long-enough-password",
            "email_code": _code(app_client),
        },
    ).json()

    minted = activation_service.mint()

    # The owner holds every permission in code, so the refusal has to be
    # demonstrated on somebody who does not.
    original = auth_service.has_permission

    def without_it(*, permission_code, **rest):
        if permission_code == "subscriptions.manage":
            return False

        return original(permission_code=permission_code, **rest)

    auth_service.has_permission = without_it

    try:
        refused = app_client.post(
            "/api/activation/redeem",
            headers={"Authorization": f"Bearer {created['access_token']}"},
            json={"code": minted["code"]},
        )
    finally:
        auth_service.has_permission = original

    assert refused.status_code == 403, refused.text

    # And the code was not spent by the attempt.
    from backend.services.demo_gate import demo_gate

    assert demo_gate.is_demo(created["company_id"]) is True
