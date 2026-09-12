"""Connecting or disconnecting a channel requires a live elevated grant from
confirming a 6-digit code emailed to the account itself.

Channel credentials route a company's real customer conversations, so this is
treated the way a password change is: proven by something reaching a mailbox
this platform does not control, not by the session cookie alone. See
`backend/services/channel_verification_service.py` for why this exists again
after being lost in an earlier rewrite -- the company-settings screen's own
help text kept describing it long after the code behind it was gone.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"

CONNECT_BODY = {"channel": "telegram", "name": "Sales", "access_token": "1:AA"}


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager
    import database.manager as manager_module
    from backend.api.routes import auth, channels

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (auth, channels):
        app.include_router(module.router)

    return TestClient(app, raise_server_exceptions=False)


def _make_owner(platform, company, app_client, email):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email=email, password=PASSWORD, full_name="Rana Haddad"
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (company["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (company["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    response = app_client.post(
        "/api/auth/login",
        json={"company": company["name"], "email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text

    return {
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


@pytest.fixture()
def owner(platform, alpha, app_client):
    return _make_owner(platform, alpha, app_client, "owner@alpha.example.com")


def _fix_code(monkeypatch, code: str) -> None:
    from backend.services import channel_verification_service

    monkeypatch.setattr(channel_verification_service, "_generate_code", lambda: code)


def _verify(app_client, owner, monkeypatch, code):
    """Request and confirm a code, returning the elevated-request headers."""
    _fix_code(monkeypatch, code)

    sent = app_client.post(
        "/api/channels/verification/request", headers=owner["headers"]
    )
    assert sent.status_code == 200, sent.text

    confirmed = app_client.post(
        "/api/channels/verification/confirm",
        headers=owner["headers"],
        json={"code": code},
    )
    assert confirmed.status_code == 200, confirmed.text

    return {**owner["headers"], "X-Elevated-Token": confirmed.json()["elevated_token"]}


def test_connecting_without_verifying_is_refused(app_client, owner):
    response = app_client.post(
        "/api/channels", headers=owner["headers"], json=CONNECT_BODY
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "channel_verification_required"


def test_disconnecting_without_verifying_is_refused(app_client, owner):
    response = app_client.delete("/api/channels/1", headers=owner["headers"])

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "channel_verification_required"


def test_a_correct_code_grants_connect_and_disconnect(app_client, owner, monkeypatch):
    elevated = _verify(app_client, owner, monkeypatch, "552017")

    created = app_client.post("/api/channels", headers=elevated, json=CONNECT_BODY)
    assert created.status_code in (200, 201), created.text
    account_id = created.json()["account"]["id"]

    deleted = app_client.delete(f"/api/channels/{account_id}", headers=elevated)
    assert deleted.status_code == 200, deleted.text


def test_editing_a_connected_account_does_not_require_verification(
    app_client, owner, monkeypatch
):
    """Only establishing or removing the credential does. An unrelated edit --
    renaming, toggling AI on -- should not make the owner re-verify."""
    elevated = _verify(app_client, owner, monkeypatch, "204981")
    created = app_client.post("/api/channels", headers=elevated, json=CONNECT_BODY)
    account_id = created.json()["account"]["id"]

    edited = app_client.patch(
        f"/api/channels/{account_id}",
        headers=owner["headers"],  # deliberately no X-Elevated-Token
        json={"name": "Sales team"},
    )

    assert edited.status_code == 200, edited.text
    assert edited.json()["account"]["name"] == "Sales team"


def test_a_wrong_code_is_refused(app_client, owner, monkeypatch):
    _fix_code(monkeypatch, "111111")
    app_client.post("/api/channels/verification/request", headers=owner["headers"])

    confirmed = app_client.post(
        "/api/channels/verification/confirm",
        headers=owner["headers"],
        json={"code": "999999"},
    )

    assert confirmed.status_code == 400


def test_a_code_works_exactly_once(app_client, owner, monkeypatch):
    _fix_code(monkeypatch, "334455")
    app_client.post("/api/channels/verification/request", headers=owner["headers"])

    first = app_client.post(
        "/api/channels/verification/confirm",
        headers=owner["headers"],
        json={"code": "334455"},
    )
    assert first.status_code == 200, first.text

    second = app_client.post(
        "/api/channels/verification/confirm",
        headers=owner["headers"],
        json={"code": "334455"},
    )
    assert second.status_code == 400, (
        "a spent code confirmed a second time -- one code should grant one "
        "elevated session, not an unlimited number"
    )


def test_a_new_code_spends_the_previous_unused_one(app_client, owner, monkeypatch):
    from backend.services import channel_verification_service

    monkeypatch.setattr(channel_verification_service, "_generate_code", lambda: "111000")
    app_client.post("/api/channels/verification/request", headers=owner["headers"])

    monkeypatch.setattr(channel_verification_service, "_generate_code", lambda: "222000")
    app_client.post("/api/channels/verification/request", headers=owner["headers"])

    stale = app_client.post(
        "/api/channels/verification/confirm",
        headers=owner["headers"],
        json={"code": "111000"},
    )
    assert stale.status_code == 400, (
        "the first, superseded code still confirmed -- two live codes for one "
        "sitting means the older one is a second key nobody is tracking"
    )

    fresh = app_client.post(
        "/api/channels/verification/confirm",
        headers=owner["headers"],
        json={"code": "222000"},
    )
    assert fresh.status_code == 200, fresh.text


def test_an_expired_elevated_grant_stops_working(platform, app_client, owner, monkeypatch):
    elevated = _verify(app_client, owner, monkeypatch, "778899")

    # Push the grant into the past instead of sleeping out its TTL.
    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE channel_elevated_grants SET expires_at = '2000-01-01T00:00:00+00:00'"
        )
        conn.commit()

    refused = app_client.post("/api/channels", headers=elevated, json=CONNECT_BODY)

    assert refused.status_code == 403
    assert refused.json()["detail"]["error"] == "channel_verification_required"


def test_verification_is_scoped_to_the_company_it_was_confirmed_for(
    platform, alpha, beta, app_client, monkeypatch
):
    """A grant minted while managing one company must not carry over to
    another company the same person happens to belong to."""
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    alpha_owner = _make_owner(platform, alpha, app_client, "dual@alpha.example.com")

    # The same person also belongs to beta.
    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (beta["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (beta["id"], alpha_owner["user_id"], int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    elevated_for_alpha = _verify(app_client, alpha_owner, monkeypatch, "605040")

    beta_login = app_client.post(
        "/api/auth/login",
        json={
            "company": beta["name"],
            "email": "dual@alpha.example.com",
            "password": PASSWORD,
        },
    )
    assert beta_login.status_code == 200, beta_login.text
    beta_headers = {
        "Authorization": f"Bearer {beta_login.json()['access_token']}",
        "X-Elevated-Token": elevated_for_alpha["X-Elevated-Token"],
    }

    refused = app_client.post("/api/channels", headers=beta_headers, json=CONNECT_BODY)

    assert refused.status_code == 403, (
        "an elevated grant confirmed while managing alpha connected a channel "
        "in beta"
    )


def test_mailer_not_configured_refuses_the_request(app_client, owner, monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "EMAIL_BACKEND", "disabled")

    response = app_client.post(
        "/api/channels/verification/request", headers=owner["headers"]
    )

    assert response.status_code == 503
