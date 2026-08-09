"""
Tests for the WhatsApp QR channel (WhatsApp Web bridge — no Meta app):

  * the bridge webhook rejects a wrong shared secret and ignores unknown
    session keys (no cross-tenant injection path)
  * a forwarded bridge message enters the SAME unified pipeline as Cloud
    API WhatsApp (conversation state under channel "whatsapp")
  * pairing status creates the channel account idempotently and refuses
    another company's session key
  * outbound send_whatsapp_text routes through the bridge when the
    company has a QR session and no Cloud account — including when the
    platform-wide .env fallback credentials exist

Run with: python3 -m pytest tests/test_whatsapp_qr.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1
SESSION_KEY = f"waqr-{COMPANY_ID}-abc123"
CUSTOMER_PHONE = "96170123456"


@pytest.fixture()
def fresh_env(tmp_path, monkeypatch):
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service
    from backend.services.notification_service import notification_service
    from backend.services.customer_service import customer_service
    import core.conversation_store as conversation_store

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    original_base_dir = conversation_store.BASE_DIR
    conversation_store.BASE_DIR = tmp_path / "conversations"
    conversation_store.BASE_DIR.mkdir(parents=True, exist_ok=True)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()
    notification_service.ensure_schema()
    customer_service.ensure_schema()

    monkeypatch.setattr("config.settings.config.WA_BRIDGE_SECRET", "test-bridge-secret")

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.commit()

    from main import app
    yield TestClient(app)

    conversation_store.BASE_DIR = original_base_dir
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


def _pair_account():
    from backend.services.channel_account_service import channel_account_service
    return channel_account_service.connect_whatsapp_qr(
        company_id=COMPANY_ID, session_key=SESSION_KEY, phone="96171000000",
    )


def test_verify_webhook_secret_hardening(monkeypatch):
    """Outside local dev, an empty or default bridge secret is refused so
    it can't fail open when APP_ENV is forgotten."""
    from channels.whatsapp_qr import service as bridge_service

    # Simulate a real deploy: JWT_SECRET changed from its default → not dev,
    # even though APP_ENV was left at "development".
    monkeypatch.setattr("config.settings.config.JWT_SECRET", "a-real-production-jwt-secret")
    monkeypatch.setattr("config.settings.config.APP_ENV", "development")
    monkeypatch.setattr("config.settings.config.DEBUG", False)

    monkeypatch.setattr("config.settings.config.WA_BRIDGE_SECRET", bridge_service.DEFAULT_BRIDGE_SECRET)
    assert bridge_service.verify_webhook_secret(bridge_service.DEFAULT_BRIDGE_SECRET) is False

    monkeypatch.setattr("config.settings.config.WA_BRIDGE_SECRET", "")
    assert bridge_service.verify_webhook_secret("") is False

    monkeypatch.setattr("config.settings.config.WA_BRIDGE_SECRET", "a-strong-real-secret")
    assert bridge_service.verify_webhook_secret("a-strong-real-secret") is True
    assert bridge_service.verify_webhook_secret("wrong") is False


def test_verify_webhook_secret_allows_default_in_local_dev(monkeypatch):
    """Untouched local dev (default JWT secret) still accepts the default
    bridge secret so local setup 'just works'."""
    from channels.whatsapp_qr import service as bridge_service

    monkeypatch.setattr("config.settings.config.JWT_SECRET", bridge_service.DEFAULT_JWT_SECRET)
    monkeypatch.setattr("config.settings.config.APP_ENV", "development")
    monkeypatch.setattr("config.settings.config.WA_BRIDGE_SECRET", bridge_service.DEFAULT_BRIDGE_SECRET)
    assert bridge_service.verify_webhook_secret(bridge_service.DEFAULT_BRIDGE_SECRET) is True


def test_webhook_rejects_wrong_secret(fresh_env):
    client = fresh_env
    response = client.post(
        "/webhook/whatsapp-qr/",
        json={"session": SESSION_KEY, "from": CUSTOMER_PHONE, "text": "hi"},
        headers={"X-Bridge-Secret": "wrong"},
    )
    assert response.status_code == 403


def test_webhook_ignores_unknown_session(fresh_env):
    client = fresh_env
    response = client.post(
        "/webhook/whatsapp-qr/",
        json={"session": "waqr-9-doesnotexist", "from": CUSTOMER_PHONE, "text": "hi"},
        headers={"X-Bridge-Secret": "test-bridge-secret"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "unknown_session"


def test_webhook_routes_message_into_unified_inbox(fresh_env):
    client = fresh_env
    _pair_account()

    response = client.post(
        "/webhook/whatsapp-qr/",
        json={"session": SESSION_KEY, "from": CUSTOMER_PHONE, "name": "Jean", "text": "Bonjour"},
        headers={"X-Bridge-Secret": "test-bridge-secret"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"

    from backend.services.conversation_control_service import conversation_control_service
    state = conversation_control_service.get_state(
        company_id=COMPANY_ID, channel="whatsapp", external_user_id=CUSTOMER_PHONE,
    )
    assert state is not None
    assert state.get("id")


def test_connect_is_idempotent_per_session(fresh_env):
    first = _pair_account()
    second = _pair_account()
    assert first["id"] == second["id"]
    assert first["channel"] == "whatsapp_qr"


def test_repair_supersedes_old_qr_session(fresh_env):
    """A new QR pairing retires the previous one so only one QR session is
    ever active for a company (no channel-limit pileup)."""
    from backend.services.channel_account_service import channel_account_service
    from database.database import db

    first = _pair_account()
    second = channel_account_service.connect_whatsapp_qr(
        company_id=COMPANY_ID, session_key=f"waqr-{COMPANY_ID}-def456", phone="96172000000",
    )
    assert first["id"] != second["id"]

    with db.connect() as conn:
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM channel_accounts "
            "WHERE company_id = ? AND channel = 'whatsapp_qr' AND status = 'active'",
            (COMPANY_ID,),
        ).fetchone()["n"]
    assert active == 1

    resolved = channel_account_service.get_qr_account(company_id=COMPANY_ID)
    assert resolved["external_account_id"] == f"waqr-{COMPANY_ID}-def456"


def test_resolve_qr_session_finds_company(fresh_env):
    from backend.services.channel_account_service import channel_account_service
    _pair_account()
    match = channel_account_service.resolve_qr_session(session_key=SESSION_KEY)
    assert match is not None
    assert match["company_id"] == COMPANY_ID


def test_status_endpoint_refuses_foreign_session(fresh_env):
    client = fresh_env
    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "o@test.local", "is_super_admin": True, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override
    try:
        response = client.get("/api/channels/whatsapp-qr/status/waqr-999-somebodyelse")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_outbound_routes_via_bridge_even_with_env_fallback(fresh_env, monkeypatch):
    """A QR-only company must send through its own paired session, not the
    platform's .env Cloud credentials."""
    _pair_account()
    monkeypatch.setattr("config.settings.config.WHATSAPP_ACCESS_TOKEN", "platform-env-token")
    monkeypatch.setattr("config.settings.config.WHATSAPP_PHONE_NUMBER_ID", "555000")

    sent = {}

    def _fake_bridge_send(session_key, to, text):
        sent.update({"session": session_key, "to": to, "text": text})
        return {"sent": True, "status_code": 200, "response": {"messages": [{"id": "wamid.test"}]}}

    with patch("channels.whatsapp_qr.service.send_text", side_effect=_fake_bridge_send):
        from channels.whatsapp.sender import send_whatsapp_text
        result = send_whatsapp_text(CUSTOMER_PHONE, "Marhaba", company_id=COMPANY_ID)

    assert result["sent"] is True
    assert sent["session"] == SESSION_KEY
    assert sent["to"] == CUSTOMER_PHONE


def test_outbound_prefers_company_cloud_account_over_bridge(fresh_env, monkeypatch):
    """When a company has BOTH a Cloud API account and a QR session, the
    official Cloud transport wins."""
    from backend.services.channel_account_service import channel_account_service
    _pair_account()

    class _FakeVerifyResponse:
        @staticmethod
        def json():
            return {"display_phone_number": "+961 71 000 000", "verified_name": "Test Co"}

    with patch(
        "backend.services.channel_account_service.requests.get",
        return_value=_FakeVerifyResponse(),
    ):
        channel_account_service.connect_whatsapp(
            company_id=COMPANY_ID, phone_number_id="123123", access_token="cloud-token", name="Cloud",
        )

    calls = {}

    class _FakeResponse:
        status_code = 200
        text = '{"messages": [{"id": "wamid.cloud"}]}'

        @staticmethod
        def json():
            return {"messages": [{"id": "wamid.cloud"}]}

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        return _FakeResponse()

    with patch("channels.whatsapp.sender.httpx.post", side_effect=_fake_post):
        from channels.whatsapp.sender import send_whatsapp_text
        result = send_whatsapp_text(CUSTOMER_PHONE, "Marhaba", company_id=COMPANY_ID)

    assert result["sent"] is True
    assert "123123" in calls["url"]
