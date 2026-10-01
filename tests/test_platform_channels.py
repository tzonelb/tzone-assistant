"""Tests for the Super Admin's own "Channels" page: platform-wide Meta
developer app credentials, and which companies may reach them.

Two things this exists to keep separate, the same way `backend/services/
platform_channel_service.py`'s own docstring frames it: whether a channel
is *configured* at all (an app id and secret exist), and whether *this
company* has been *granted* it. Configuring a credential must never, by
itself, open it to every company on the platform -- that is the one
property most of these tests are really checking, from different angles.
"""

from __future__ import annotations

import sys

import pytest


PLATFORM_PASSWORD = "PlatformAdminPass1"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.channel_oauth  # noqa: F401
    import backend.api.routes.platform  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.meta_oauth_service  # noqa: F401
    import backend.services.platform_channel_service  # noqa: F401
    import backend.services.platform_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    for required in (
        "backend.services.platform_service",
        "backend.services.auth_service",
        "backend.services.platform_channel_service",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    from backend.services.platform_channel_service import platform_channel_service

    return platform_channel_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, channel_oauth, channels, platform as platform_routes

    app = FastAPI()
    app.include_router(platform_routes.router)
    app.include_router(auth.router)
    app.include_router(channels.router)
    app.include_router(channel_oauth.router)

    return TestClient(app)


def _make_admin(email: str = "root@platform.example.com") -> int:
    from backend.services.auth_service import auth_service

    return auth_service.create_user(
        email=email, password=PLATFORM_PASSWORD, full_name="Platform Root", is_super_admin=True
    )


def _platform_token(client, email: str = "root@platform.example.com") -> str:
    import pyotp

    response = client.post(
        "/api/platform/auth/login", json={"email": email, "password": PLATFORM_PASSWORD}
    )
    assert response.status_code == 200, response.text

    body = response.json()
    token = body["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    if not (body.get("totp") or {}).get("enrolment_pending"):
        return token

    secret = client.post("/api/platform/auth/totp/begin", headers=headers).json()["secret"]
    confirmed = client.post(
        "/api/platform/auth/totp/confirm",
        headers=headers,
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert confirmed.status_code == 200, confirmed.text

    return token


@pytest.fixture()
def admin_headers(service, client):
    _make_admin()
    token = _platform_token(client)
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ service-level


def test_a_channel_starts_unconfigured(service):
    status = service.status_for("messenger")
    assert status == {"channel": "messenger", "configured": False, "config": {}, "updated_at": None}


def test_setting_credentials_requires_the_app_secret(service):
    from backend.services.platform_channel_service import PlatformChannelError

    with pytest.raises(PlatformChannelError):
        service.set_credentials(
            channel="messenger", values={"app_id": "123"}, actor_user_id=1
        )


def test_setting_credentials_marks_the_channel_configured(service):
    result = service.set_credentials(
        channel="messenger",
        values={"app_id": "app-123", "app_secret": "shh-secret"},
        actor_user_id=1,
    )

    assert result["configured"] is True
    assert result["config"] == {"app_id": "app-123"}

    status = service.status_for("messenger")
    assert status["configured"] is True
    assert status["config"] == {"app_id": "app-123"}


def test_the_secret_is_never_in_the_public_status(service):
    service.set_credentials(
        channel="messenger",
        values={"app_id": "app-123", "app_secret": "very-secret-value"},
        actor_user_id=1,
    )

    status = service.status_for("messenger")
    assert "very-secret-value" not in str(status)


def test_get_credentials_unseals_the_secret_for_internal_use_only(service):
    service.set_credentials(
        channel="whatsapp",
        values={"app_id": "wa-app", "app_secret": "wa-secret"},
        actor_user_id=1,
    )

    credentials = service.get_credentials("whatsapp")
    assert credentials == {"app_id": "wa-app", "app_secret": "wa-secret"}


def test_clearing_credentials_removes_the_channel(service):
    service.set_credentials(
        channel="messenger", values={"app_id": "a", "app_secret": "b"}, actor_user_id=1
    )
    service.clear_credentials(channel="messenger", actor_user_id=1)

    assert service.status_for("messenger")["configured"] is False
    assert service.get_credentials("messenger") is None


def test_an_unofficial_channel_has_no_platform_credential(service):
    from backend.services.platform_channel_service import PlatformChannelError

    with pytest.raises(PlatformChannelError):
        service.set_credentials(
            channel="instagram_direct",
            values={"app_id": "x", "app_secret": "y"},
            actor_user_id=1,
        )

    assert service.has_credentials("instagram_direct") is False


def test_access_defaults_closed(service, alpha):
    assert service.company_has_access(alpha["id"], "messenger") is False


def test_access_grid_lists_every_company_including_ungranted(service, alpha, beta):
    grid = service.access_grid_for_channel("messenger")
    by_id = {row["company_id"]: row for row in grid}

    assert alpha["id"] in by_id
    assert beta["id"] in by_id
    assert by_id[alpha["id"]]["enabled"] is False


def test_granting_and_revoking_access(service, alpha):
    service.set_company_access(company_id=alpha["id"], channel="messenger", enabled=True, actor_user_id=1)
    assert service.company_has_access(alpha["id"], "messenger") is True

    service.set_company_access(company_id=alpha["id"], channel="messenger", enabled=False, actor_user_id=1)
    assert service.company_has_access(alpha["id"], "messenger") is False


def test_granting_access_for_an_unknown_company_is_refused(service):
    from backend.services.platform_channel_service import PlatformChannelError

    with pytest.raises(PlatformChannelError):
        service.set_company_access(
            company_id=999999, channel="messenger", enabled=True, actor_user_id=1
        )


# ------------------------------------------------------------------ routes


def test_list_channels_requires_a_platform_token(client):
    response = client.get("/api/platform/channels")
    assert response.status_code in (401, 403)


def test_set_and_list_credentials_over_http(admin_headers, client):
    response = client.put(
        "/api/platform/channels/messenger/credentials",
        json={"app_id": "app-1", "app_secret": "secret-1"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is True

    listed = client.get("/api/platform/channels", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    by_channel = {item["channel"]: item for item in listed.json()["items"]}
    assert by_channel["messenger"]["configured"] is True
    assert by_channel["whatsapp"]["configured"] is False


def test_credentials_response_never_leaks_the_secret_over_http(admin_headers, client):
    response = client.put(
        "/api/platform/channels/messenger/credentials",
        json={"app_id": "app-1", "app_secret": "leak-check-secret"},
        headers=admin_headers,
    )
    assert "leak-check-secret" not in response.text


def test_clear_credentials_over_http(admin_headers, client):
    client.put(
        "/api/platform/channels/messenger/credentials",
        json={"app_id": "app-1", "app_secret": "secret-1"},
        headers=admin_headers,
    )

    response = client.delete("/api/platform/channels/messenger/credentials", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is False


def test_access_grid_over_http(admin_headers, client, alpha):
    response = client.get("/api/platform/channels/messenger/access", headers=admin_headers)
    assert response.status_code == 200, response.text
    company_ids = {row["company_id"] for row in response.json()["items"]}
    assert alpha["id"] in company_ids


def test_set_access_over_http(admin_headers, client, alpha):
    response = client.put(
        f"/api/platform/channels/messenger/access/{alpha['id']}",
        json={"enabled": True},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True

    grid = client.get("/api/platform/channels/messenger/access", headers=admin_headers).json()
    row = next(r for r in grid["items"] if r["company_id"] == alpha["id"])
    assert row["enabled"] is True


def test_an_unofficial_channel_is_refused_over_http(admin_headers, client):
    response = client.put(
        "/api/platform/channels/instagram_direct/credentials",
        json={"app_id": "x", "app_secret": "y"},
        headers=admin_headers,
    )
    assert response.status_code == 400


# ------------------------------------------------------------------ the
# Facebook OAuth button, gated by a platform credential + per-company grant


def _company_manage_headers(client, company, *, email="owner@example.com"):
    """A company employee with `channels.manage`, logged in on the customer
    auth router -- `channel_oauth.py`'s own gate, not the platform one."""
    import backend.services.auth_service as auth_service_module
    from database.manager import utc_now_iso

    password = "OwnerPass123!"
    user_id = auth_service_module.auth_service.create_user(
        email=email, password=password, full_name="Company Owner"
    )

    with auth_service_module.database_manager.control() as conn:
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

    response = client.post(
        "/api/auth/login",
        json={"company": company["name"], "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_oauth_button_is_off_with_no_platform_credential_and_no_env(
    service, client, alpha, monkeypatch
):
    import config.settings as settings_module

    monkeypatch.setattr(settings_module.config, "META_APP_ID", "")
    monkeypatch.setattr(settings_module.config, "META_APP_SECRET", "")

    headers = _company_manage_headers(client, alpha)
    response = client.get("/api/channels/oauth/facebook/config", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is False


def test_oauth_button_stays_off_for_a_company_not_granted_access(
    service, client, alpha, monkeypatch
):
    import config.settings as settings_module

    monkeypatch.setattr(settings_module.config, "META_APP_ID", "")
    monkeypatch.setattr(settings_module.config, "META_APP_SECRET", "")

    service.set_credentials(
        channel="messenger", values={"app_id": "a", "app_secret": "b"}, actor_user_id=1
    )

    headers = _company_manage_headers(client, alpha)
    response = client.get("/api/channels/oauth/facebook/config", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is False


def test_oauth_button_turns_on_once_granted(service, client, alpha, monkeypatch):
    import config.settings as settings_module

    monkeypatch.setattr(settings_module.config, "META_APP_ID", "")
    monkeypatch.setattr(settings_module.config, "META_APP_SECRET", "")

    service.set_credentials(
        channel="messenger", values={"app_id": "a", "app_secret": "b"}, actor_user_id=1
    )
    service.set_company_access(
        company_id=alpha["id"], channel="messenger", enabled=True, actor_user_id=1
    )

    headers = _company_manage_headers(client, alpha)
    response = client.get("/api/channels/oauth/facebook/config", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is True


def test_oauth_button_is_universal_when_configured_only_through_the_environment(
    service, client, alpha, monkeypatch
):
    """The legacy path: no platform-level credential exists at all, so the
    old env-var behaviour applies unchanged -- every company sees the
    button, matching this deployment's behaviour before this feature."""
    import config.settings as settings_module

    monkeypatch.setattr(settings_module.config, "META_APP_ID", "env-app-id")
    monkeypatch.setattr(settings_module.config, "META_APP_SECRET", "env-app-secret")

    headers = _company_manage_headers(client, alpha)
    response = client.get("/api/channels/oauth/facebook/config", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is True


def test_starting_oauth_is_refused_for_a_company_not_granted_access(
    service, client, alpha, monkeypatch
):
    import config.settings as settings_module

    monkeypatch.setattr(settings_module.config, "META_APP_ID", "")
    monkeypatch.setattr(settings_module.config, "META_APP_SECRET", "")

    service.set_credentials(
        channel="messenger", values={"app_id": "a", "app_secret": "b"}, actor_user_id=1
    )

    headers = _company_manage_headers(client, alpha)
    response = client.post("/api/channels/oauth/facebook/start", headers=headers)
    assert response.status_code == 503
