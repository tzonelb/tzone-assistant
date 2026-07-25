"""
Real tests for the "Connect Page" flow: company-side Telegram bot
connection, token encryption/decryption, plan channel-limit enforcement,
and duplicate-connection rejection. Mocks the outbound call to
Telegram's getMe API — no real network calls.

Run with: python3 -m pytest tests/test_channel_connections.py -v
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

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'owner@test.local', 'Owner', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, workspace_id) VALUES (1, 'Test Co', 1)")
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


def _fake_getme(ok=True, bot_id=999888, username="tzone_support_bot"):
    resp = MagicMock()
    resp.json.return_value = (
        {"ok": True, "result": {"id": bot_id, "username": username, "is_bot": True}}
        if ok else {"ok": False, "description": "Unauthorized"}
    )
    return resp


def test_connect_telegram_succeeds_with_valid_token(client_and_db):
    client = client_and_db
    with patch("backend.services.channel_account_service.requests.get", return_value=_fake_getme()):
        with patch("channels.telegram.manager.start_bot"):
            resp = client.post("/api/channels/telegram/connect", json={"bot_token": "123:ABC"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["channel"] == "telegram"
    assert body["name"] == "@tzone_support_bot"
    assert body["status"] == "active"


def test_connect_telegram_rejects_invalid_token(client_and_db):
    client = client_and_db
    with patch("backend.services.channel_account_service.requests.get", return_value=_fake_getme(ok=False)):
        resp = client.post("/api/channels/telegram/connect", json={"bot_token": "bad-token"})
    assert resp.status_code == 400
    assert "Unauthorized" in resp.json()["detail"]


def test_token_is_encrypted_at_rest_and_decrypts_correctly(client_and_db):
    from backend.services.channel_account_service import channel_account_service
    from database.database import db

    with patch("backend.services.channel_account_service.requests.get", return_value=_fake_getme()):
        account = channel_account_service.connect_telegram(company_id=COMPANY_ID, bot_token="123:SECRET-TOKEN")

    with db.connect() as conn:
        row = conn.execute(
            "SELECT access_token_encrypted FROM channel_accounts WHERE id = ?", (account["id"],),
        ).fetchone()
    assert "SECRET-TOKEN" not in row["access_token_encrypted"]

    decrypted = channel_account_service.get_decrypted_token(account_id=account["id"])
    assert decrypted == "123:SECRET-TOKEN"


def test_duplicate_bot_connection_is_rejected(client_and_db):
    client = client_and_db
    with patch("backend.services.channel_account_service.requests.get", return_value=_fake_getme()):
        with patch("channels.telegram.manager.start_bot"):
            client.post("/api/channels/telegram/connect", json={"bot_token": "123:ABC"})
            resp = client.post("/api/channels/telegram/connect", json={"bot_token": "123:ABC"})
    assert resp.status_code == 400
    assert "already connected" in resp.json()["detail"]


def test_connecting_beyond_plan_channel_limit_is_rejected(client_and_db):
    from database.database import db

    with db.connect() as conn:
        conn.execute(
            "UPDATE subscriptions SET status = 'cancelled' WHERE company_id = ? AND status IN ('active', 'trialing')",
            (COMPANY_ID,),
        )
        cursor = conn.execute(
            "INSERT INTO plans (name, code, max_channel_accounts) VALUES ('Tiny', 'tiny-channels', 1)"
        )
        plan_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO subscriptions (company_id, plan_id, status, starts_at, expires_at) "
            "VALUES (?, ?, 'active', datetime('now'), datetime('now', '+30 days'))",
            (COMPANY_ID, plan_id),
        )
        conn.commit()

    client = client_and_db
    with patch("backend.services.channel_account_service.requests.get") as mock_get:
        mock_get.side_effect = [_fake_getme(bot_id=1, username="bot_one"), _fake_getme(bot_id=2, username="bot_two")]
        with patch("channels.telegram.manager.start_bot"):
            first = client.post("/api/channels/telegram/connect", json={"bot_token": "111:AAA"})
            second = client.post("/api/channels/telegram/connect", json={"bot_token": "222:BBB"})

    assert first.status_code == 200
    assert second.status_code == 400
    assert "Tiny" in second.json()["detail"]


def test_disconnect_marks_channel_disabled(client_and_db):
    client = client_and_db
    with patch("backend.services.channel_account_service.requests.get", return_value=_fake_getme()):
        with patch("channels.telegram.manager.start_bot"):
            connect_resp = client.post("/api/channels/telegram/connect", json={"bot_token": "123:ABC"})
    account_id = connect_resp.json()["id"]

    with patch("channels.telegram.manager.stop_bot"):
        resp = client.delete(f"/api/channels/{account_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


def test_list_channels_returns_connected_accounts(client_and_db):
    client = client_and_db
    with patch("backend.services.channel_account_service.requests.get", return_value=_fake_getme()):
        with patch("channels.telegram.manager.start_bot"):
            client.post("/api/channels/telegram/connect", json={"bot_token": "123:ABC"})

    resp = client.get("/api/channels")
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    assert len(channels) == 1
    assert channels[0]["channel"] == "telegram"


def _fake_meta_get(data):
    resp = MagicMock()
    resp.json.return_value = data
    return resp


def test_connect_whatsapp_succeeds_with_valid_credentials(client_and_db):
    client = client_and_db
    fake_response = _fake_meta_get({"display_phone_number": "+96170000000", "verified_name": "T-ZONE Support"})
    with patch("backend.services.channel_account_service.requests.get", return_value=fake_response):
        resp = client.post(
            "/api/channels/whatsapp/connect",
            json={"phone_number_id": "1234567890", "access_token": "EAABsbCS...token"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["channel"] == "whatsapp"
    assert body["name"] == "T-ZONE Support"


def test_connect_whatsapp_rejects_meta_error(client_and_db):
    client = client_and_db
    fake_response = _fake_meta_get({"error": {"message": "Invalid OAuth access token"}})
    with patch("backend.services.channel_account_service.requests.get", return_value=fake_response):
        resp = client.post(
            "/api/channels/whatsapp/connect",
            json={"phone_number_id": "123", "access_token": "bad-token"},
        )
    assert resp.status_code == 400
    assert "Invalid OAuth" in resp.json()["detail"]


def test_connect_instagram_succeeds_when_page_has_ig_account(client_and_db):
    client = client_and_db
    fake_response = _fake_meta_get({
        "id": "111222333", "name": "T-ZONE Page",
        "instagram_business_account": {"id": "999888777"},
    })
    with patch("backend.services.channel_account_service.requests.get", return_value=fake_response):
        resp = client.post(
            "/api/channels/instagram/connect",
            json={"page_id": "111222333", "access_token": "EAABsbCS...token"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["channel"] == "instagram"
    assert body["instagram_business_id"] == "999888777"


def test_connect_instagram_rejects_page_without_ig_account(client_and_db):
    client = client_and_db
    fake_response = _fake_meta_get({"id": "111222333", "name": "T-ZONE Page"})
    with patch("backend.services.channel_account_service.requests.get", return_value=fake_response):
        resp = client.post(
            "/api/channels/instagram/connect",
            json={"page_id": "111222333", "access_token": "EAABsbCS...token"},
        )
    assert resp.status_code == 400
    assert "Instagram professional account" in resp.json()["detail"]


def test_resolve_meta_account_finds_the_right_company_by_page_id(client_and_db):
    from backend.services.channel_account_service import channel_account_service

    fake_response = _fake_meta_get({
        "id": "555", "name": "Page", "instagram_business_account": {"id": "777"},
    })
    with patch("backend.services.channel_account_service.requests.get", return_value=fake_response):
        channel_account_service.connect_instagram(company_id=COMPANY_ID, page_id="555", access_token="tok")

    match = channel_account_service.resolve_meta_account(recipient_id="777", channel="instagram")
    assert match is not None
    assert match["company_id"] == COMPANY_ID
    assert match["access_token"] == "tok"

    no_match = channel_account_service.resolve_meta_account(recipient_id="does-not-exist", channel="instagram")
    assert no_match is None
