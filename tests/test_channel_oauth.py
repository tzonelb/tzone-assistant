"""The Facebook connect scaffold: signed state, and no fake connections.

The flow cannot be exercised end to end without a real Meta app, so these pin
the parts that must be right regardless: the state token cannot be forged or
replayed, an unconfigured platform refuses rather than pretending, and a
tampered callback touches nothing.
"""

from __future__ import annotations

import time

import pytest

from backend.services.meta_oauth_service import (
    MetaOAuthError,
    meta_oauth_service,
)


# ------------------------------------------------------------------- the state

def test_state_roundtrips(master_key):
    state = meta_oauth_service.sign_state(company_id=12, user_id=5)
    decoded = meta_oauth_service.decode_state(state)
    assert decoded == {"company_id": 12, "user_id": 5}


def test_a_tampered_state_is_rejected(master_key):
    state = meta_oauth_service.sign_state(company_id=12, user_id=5)
    assert meta_oauth_service.decode_state(state[:-4] + "AAAA") is None
    assert meta_oauth_service.decode_state("not-a-state") is None


def test_an_expired_state_is_rejected(master_key, monkeypatch):
    import backend.services.meta_oauth_service as svc

    state = meta_oauth_service.sign_state(company_id=1, user_id=1)
    # Jump the clock the module reads past the ten-minute window (capture the
    # real clock first so the replacement does not call itself).
    future = time.time() + svc.STATE_TTL_SECONDS + 60
    monkeypatch.setattr(svc.time, "time", lambda: future)
    assert meta_oauth_service.decode_state(state) is None


# ------------------------------------------------------------ configured gate

def test_not_configured_without_an_app(master_key, monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "META_APP_ID", "")
    assert meta_oauth_service.is_configured() is False


def test_authorize_url_refuses_when_unconfigured(master_key, monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "META_APP_ID", "")
    with pytest.raises(MetaOAuthError):
        meta_oauth_service.authorize_url(company_id=1, user_id=1)


def test_authorize_url_is_built_when_configured(master_key, monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "META_APP_ID", "123456")
    monkeypatch.setattr(config, "META_APP_SECRET", "shhh")
    url = meta_oauth_service.authorize_url(company_id=3, user_id=9)
    assert "dialog/oauth" in url
    assert "client_id=123456" in url
    assert "state=" in url


# ----------------------------------------------------------------- the routes

def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import channel_oauth

    app = FastAPI()
    app.include_router(channel_oauth.router)
    app.dependency_overrides[channel_oauth._view] = lambda: 1
    app.dependency_overrides[channel_oauth._manage] = lambda: {
        "id": 1,
        "active_company_id": 1,
    }
    return TestClient(app)


def test_config_reports_off_when_unconfigured(master_key, monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "META_APP_ID", "")
    response = _client().get("/api/channels/oauth/facebook/config")
    assert response.status_code == 200
    assert response.json() == {"configured": False}


def test_start_refuses_when_unconfigured(master_key, monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "META_APP_ID", "")
    response = _client().post("/api/channels/oauth/facebook/start")
    assert response.status_code == 503


def test_callback_with_a_bad_state_touches_nothing(master_key):
    response = _client().get(
        "/api/channels/oauth/facebook/callback?code=abc&state=forged",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "connect=invalid" in response.headers["location"]


def test_callback_cancelled_when_the_person_declines(master_key):
    response = _client().get(
        "/api/channels/oauth/facebook/callback?error=access_denied",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "connect=cancelled" in response.headers["location"]
