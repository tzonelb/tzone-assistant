"""
Real tests for the "Buffer-style" Facebook Login OAuth connect flow.
Mocks all outbound Meta API calls — no real network calls.

Run with: python3 -m pytest tests/test_facebook_oauth.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.facebook_oauth_service import facebook_oauth_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    facebook_oauth_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'owner@test.local', 'Owner', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')"
        )
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "owner@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

    yield TestClient(app)

    app.dependency_overrides.clear()
    db.db_path = original_db_path
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_db_path):
                os.remove(tmp_db_path)
            break
        except PermissionError:
            time.sleep(0.1)


def test_start_oauth_requires_app_id_configured(client_and_db):
    client = client_and_db
    with patch("backend.services.facebook_oauth_service.config.META_APP_ID", ""):
        resp = client.get("/api/channels/facebook/oauth/start")
    assert resp.status_code == 400


def test_start_oauth_returns_authorize_url(client_and_db):
    client = client_and_db
    with patch("backend.services.facebook_oauth_service.config.META_APP_ID", "test-app-id"):
        resp = client.get("/api/channels/facebook/oauth/start")
    assert resp.status_code == 200, resp.text
    url = resp.json()["authorize_url"]
    assert "test-app-id" in url
    assert "state=" in url
    assert "pages_messaging" in url


def test_callback_with_missing_code_redirects_with_error(client_and_db):
    client = client_and_db
    resp = client.get("/api/channels/facebook/oauth/callback", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "fb_error" in resp.headers["location"]


def test_callback_rejects_invalid_state(client_and_db):
    client = client_and_db
    resp = client.get(
        "/api/channels/facebook/oauth/callback",
        params={"code": "abc", "state": "not-a-real-state"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "fb_error" in resp.headers["location"]


def test_full_oauth_flow_connects_page_and_linked_instagram(client_and_db):
    client = client_and_db

    with patch("backend.services.facebook_oauth_service.config.META_APP_ID", "test-app-id"), \
         patch("backend.services.facebook_oauth_service.config.META_APP_SECRET", "test-secret"):
        start_resp = client.get("/api/channels/facebook/oauth/start")
        authorize_url = start_resp.json()["authorize_url"]
        state = authorize_url.split("state=")[1].split("&")[0]

        token_response = MagicMock()
        token_response.json.return_value = {"access_token": "user-token-abc"}

        pages_response = MagicMock()
        pages_response.json.return_value = {
            "data": [
                {
                    "id": "page-111",
                    "name": "T-ZONE Support",
                    "access_token": "page-token-xyz",
                    "instagram_business_account": {"id": "ig-999"},
                }
            ]
        }

        messenger_verify_response = MagicMock()
        messenger_verify_response.json.return_value = {"id": "page-111", "name": "T-ZONE Support"}

        instagram_verify_response = MagicMock()
        instagram_verify_response.json.return_value = {
            "id": "page-111", "name": "T-ZONE Support",
            "instagram_business_account": {"id": "ig-999"},
        }

        with patch("backend.services.facebook_oauth_service.requests.get") as mock_get:
            mock_get.side_effect = [token_response, pages_response, messenger_verify_response, instagram_verify_response]
            resp = client.get(
                "/api/channels/facebook/oauth/callback",
                params={"code": "auth-code-123", "state": state},
                follow_redirects=False,
            )

    assert resp.status_code in (302, 307)
    assert "fb_connected=2" in resp.headers["location"]  # messenger + instagram

    channels_resp = client.get("/api/channels")
    channels = channels_resp.json()["channels"]
    channel_types = sorted(c["channel"] for c in channels)
    assert channel_types == ["instagram", "messenger"]


def test_state_cannot_be_reused(client_and_db):
    client = client_and_db
    with patch("backend.services.facebook_oauth_service.config.META_APP_ID", "test-app-id"):
        start_resp = client.get("/api/channels/facebook/oauth/start")
        authorize_url = start_resp.json()["authorize_url"]
        state = authorize_url.split("state=")[1].split("&")[0]

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "user-token-abc"}
    pages_response = MagicMock()
    pages_response.json.return_value = {"data": [{"id": "p1", "name": "Page", "access_token": "tok"}]}
    verify_response = MagicMock()
    verify_response.json.return_value = {"id": "p1", "name": "Page"}

    with patch("backend.services.facebook_oauth_service.requests.get") as mock_get:
        mock_get.side_effect = [token_response, pages_response, verify_response]
        first = client.get(
            "/api/channels/facebook/oauth/callback",
            params={"code": "code1", "state": state}, follow_redirects=False,
        )
    assert "fb_connected" in first.headers["location"]

    second = client.get(
        "/api/channels/facebook/oauth/callback",
        params={"code": "code2", "state": state}, follow_redirects=False,
    )
    assert "fb_error" in second.headers["location"]
