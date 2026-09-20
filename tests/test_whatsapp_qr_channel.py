"""Tests for WhatsApp (QR scan) -- the unofficial, session-based channel.

Unlike Instagram (direct login) and Facebook (cookie download), connecting
this one is not one or two ordinary request/response round trips: the
phone has to actually scan a QR code, which is real human time. So
`backend/api/routes/whatsapp_qr.py`'s `start` hands the live browser to a
background thread (see `channels/whatsapp_qr/browser.py`'s own docstring)
and returns immediately; `status` is polled against that thread's own
result. Every router test here builds a real `PendingConnection` and drives
its fields directly -- the same shape the background thread would leave
behind -- rather than launching a real browser, so nothing here waits on
Playwright or a real WhatsApp session.

What this file does not test: the DOM-reading functions inside
`channels/whatsapp_qr/browser.py` themselves (`_looks_like_logged_in`,
`_extract_qr_png`, `_extract_phone_number`, `_read_visible_messages`).
Unlike Facebook (cookie download)'s regex-based reader, which works on
plain strings and is fully testable without a browser, these take a real
Playwright `Page` and were written against WhatsApp Web's own DOM, which
this environment has no way to render or verify against -- see that
module's own docstring for the fuller reasoning. The router, poller and
sender logic *around* those functions -- the state machine, the
credential handling, the delivery/dedup logic -- is what is verified here,
with those functions themselves replaced by controlled fakes.
"""

from __future__ import annotations

import json
import sys

import pytest


PASSWORD = "OwnerPass123!"


# ------------------------------------------------------------------ wiring


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import channels.credentials  # noqa: F401
    import channels.whatsapp_qr.poller  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    for required in (
        "backend.services.channel_account_service",
        "channels.credentials",
        "channels.whatsapp_qr.poller",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager
    import database.manager as manager_module

    from backend.api.routes import auth, channels, whatsapp_qr

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (auth, channels, whatsapp_qr):
        app.include_router(module.router)

    return TestClient(app, raise_server_exceptions=False)


def _make_owner(app_client, manager, company, *, email, code):
    from backend.services.auth_service import auth_service
    from backend.services import channel_verification_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email=email, password=PASSWORD, full_name="Test Owner"
    )

    with manager.control() as conn:
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

    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    from unittest.mock import patch

    with patch.object(channel_verification_service, "_generate_code", lambda: code):
        request_sent = app_client.post(
            "/api/channels/verification/request", headers=headers
        )
        assert request_sent.status_code == 200, request_sent.text

        confirmed = app_client.post(
            "/api/channels/verification/confirm",
            json={"code": code},
            headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text

    headers["X-Elevated-Token"] = confirmed.json()["elevated_token"]
    return headers


@pytest.fixture()
def owner(platform, alpha, app_client):
    return _make_owner(
        app_client, platform["manager"], alpha, email="owner@alpha.example.com", code="482913"
    )


@pytest.fixture()
def beta_owner(platform, beta, app_client):
    return _make_owner(
        app_client, platform["manager"], beta, email="owner@beta.example.com", code="739201"
    )


def _fake_connection(*, status="starting", **fields):
    from channels.whatsapp_qr.browser import PendingConnection

    connection = PendingConnection()
    connection.status = status

    for key, value in fields.items():
        setattr(connection, key, value)

    return connection


def _wire_start_connect(monkeypatch, connection):
    from backend.api.routes import whatsapp_qr as router_module

    monkeypatch.setattr(router_module, "start_connect", lambda: connection)


# ------------------------------------------------------------------ connect/start


def test_start_returns_a_pending_id(app_client, owner, monkeypatch):
    _wire_start_connect(monkeypatch, _fake_connection())

    response = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=owner
    )

    assert response.status_code == 200, response.text
    assert response.json()["pending_id"]


def test_start_without_the_elevated_grant_is_refused(
    app_client, platform, alpha, monkeypatch
):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    _wire_start_connect(monkeypatch, _fake_connection())

    user_id = auth_service.create_user(
        email="plain@alpha.example.com", password=PASSWORD, full_name="No Grant"
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (alpha["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (alpha["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    login = app_client.post(
        "/api/auth/login",
        json={"company": alpha["name"], "email": "plain@alpha.example.com", "password": PASSWORD},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=headers
    )

    assert response.status_code == 403


# ------------------------------------------------------------------ connect/status


def test_status_while_starting_reports_starting(app_client, owner, monkeypatch):
    _wire_start_connect(monkeypatch, _fake_connection(status="starting"))

    started = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=owner
    )
    pending_id = started.json()["pending_id"]

    response = app_client.get(
        f"/api/whatsapp-qr/connect/status/{pending_id}", headers=owner
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "starting"}


def test_status_once_qr_ready_returns_the_image(app_client, owner, monkeypatch):
    _wire_start_connect(
        monkeypatch, _fake_connection(status="qr_ready", qr_png_base64="Zm9v")
    )

    started = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=owner
    )
    pending_id = started.json()["pending_id"]

    response = app_client.get(
        f"/api/whatsapp-qr/connect/status/{pending_id}", headers=owner
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "qr_ready", "qr_png_base64": "Zm9v"}


def test_status_once_connected_creates_the_account(app_client, owner, monkeypatch):
    _wire_start_connect(
        monkeypatch,
        _fake_connection(
            status="connected",
            storage_state={"cookies": [], "origins": []},
            phone_number="966501234567",
        ),
    )

    started = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=owner
    )
    pending_id = started.json()["pending_id"]

    response = app_client.get(
        f"/api/whatsapp-qr/connect/status/{pending_id}", headers=owner
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "connected"
    account = body["account"]
    assert account["channel"] == "whatsapp_qr"
    assert account["external_account_id"] == "966501234567"
    assert account["has_access_token"] is True
    assert "access_token" not in account
    assert "storage_state" not in json.dumps(account)


def test_status_once_connected_is_gone_on_a_second_poll(app_client, owner, monkeypatch):
    _wire_start_connect(
        monkeypatch,
        _fake_connection(
            status="connected",
            storage_state={"cookies": []},
            phone_number="966501234567",
        ),
    )

    started = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=owner
    )
    pending_id = started.json()["pending_id"]

    first = app_client.get(f"/api/whatsapp-qr/connect/status/{pending_id}", headers=owner)
    assert first.status_code == 200

    second = app_client.get(f"/api/whatsapp-qr/connect/status/{pending_id}", headers=owner)
    assert second.status_code == 410


def test_status_failed_is_refused_and_cleared(app_client, owner, monkeypatch):
    _wire_start_connect(
        monkeypatch, _fake_connection(status="failed", error="Could not reach WhatsApp Web.")
    )

    started = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=owner
    )
    pending_id = started.json()["pending_id"]

    first = app_client.get(f"/api/whatsapp-qr/connect/status/{pending_id}", headers=owner)
    assert first.status_code == 400
    assert "WhatsApp" in first.json()["detail"]

    second = app_client.get(f"/api/whatsapp-qr/connect/status/{pending_id}", headers=owner)
    assert second.status_code == 410


def test_status_expired_is_refused(app_client, owner, monkeypatch):
    _wire_start_connect(monkeypatch, _fake_connection(status="expired"))

    started = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=owner
    )
    pending_id = started.json()["pending_id"]

    response = app_client.get(
        f"/api/whatsapp-qr/connect/status/{pending_id}", headers=owner
    )
    assert response.status_code == 400


def test_status_unknown_pending_id_is_refused(app_client, owner):
    response = app_client.get(
        "/api/whatsapp-qr/connect/status/does-not-exist", headers=owner
    )
    assert response.status_code == 410


def test_status_is_isolated_per_company(app_client, owner, beta_owner, monkeypatch):
    _wire_start_connect(monkeypatch, _fake_connection(status="qr_ready", qr_png_base64="x"))

    started = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=owner
    )
    pending_id = started.json()["pending_id"]

    stolen = app_client.get(
        f"/api/whatsapp-qr/connect/status/{pending_id}", headers=beta_owner
    )
    assert stolen.status_code == 410


def test_connecting_the_same_number_twice_is_refused(app_client, owner, monkeypatch):
    _wire_start_connect(
        monkeypatch,
        _fake_connection(
            status="connected", storage_state={"cookies": []}, phone_number="966501234567"
        ),
    )

    first_start = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=owner
    )
    first_status = app_client.get(
        f"/api/whatsapp-qr/connect/status/{first_start.json()['pending_id']}", headers=owner
    )
    assert first_status.status_code == 200, first_status.text

    _wire_start_connect(
        monkeypatch,
        _fake_connection(
            status="connected", storage_state={"cookies": []}, phone_number="966501234567"
        ),
    )

    second_start = app_client.post(
        "/api/whatsapp-qr/connect/start",
        json={"name": "Support Line Again"},
        headers=owner,
    )
    second_status = app_client.get(
        f"/api/whatsapp-qr/connect/status/{second_start.json()['pending_id']}", headers=owner
    )
    assert second_status.status_code == 409


# ------------------------------------------------------------------ connect/cancel


def test_cancel_stops_the_background_connection_and_clears_it(
    app_client, owner, monkeypatch
):
    connection = _fake_connection(status="qr_ready", qr_png_base64="x")
    _wire_start_connect(monkeypatch, connection)

    started = app_client.post(
        "/api/whatsapp-qr/connect/start", json={"name": "Support Line"}, headers=owner
    )
    pending_id = started.json()["pending_id"]

    cancelled = app_client.post(
        f"/api/whatsapp-qr/connect/cancel/{pending_id}", headers=owner
    )
    assert cancelled.status_code == 200

    assert connection._stop.is_set()

    gone = app_client.get(f"/api/whatsapp-qr/connect/status/{pending_id}", headers=owner)
    assert gone.status_code == 410


# ------------------------------------------------------------------ poller


def _connect_via_service(company, *, phone_number="966501234567"):
    import backend.services.channel_account_service as service_module

    return service_module.channel_account_service.create_account(
        company_id=company["id"],
        channel="whatsapp_qr",
        name="Support Line",
        values={
            "external_account_id": phone_number,
            "access_token": json.dumps({"cookies": []}),
            "_whatsapp_phone_number": phone_number,
        },
    )


class _FakeWhatsAppSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _wire_poller(monkeypatch, *, chats=None, open_error=None):
    import channels.whatsapp_qr.poller as poller_module

    session = _FakeWhatsAppSession()

    def fake_open(storage_state):
        if open_error:
            raise open_error
        return session

    monkeypatch.setattr(poller_module, "open_authenticated_page", fake_open)
    monkeypatch.setattr(
        poller_module, "read_unread_chats", lambda session, max_chats: chats or []
    )
    return session


def test_poll_account_delivers_incoming_messages_only(wired, alpha, monkeypatch):
    import channels.whatsapp_qr.poller as poller_module

    account = _connect_via_service(alpha)
    session = _wire_poller(
        monkeypatch,
        chats=[
            {
                "chat_id": "966555000111",
                "chat_name": "A Customer",
                "messages": [
                    {"text": "hello, are you open?", "is_outgoing": False},
                    {"text": "yes we are", "is_outgoing": True},
                ],
            }
        ],
    )

    delivered = []
    monkeypatch.setattr(
        poller_module,
        "process_inbound_event",
        lambda **kwargs: delivered.append(kwargs) or {},
    )

    poller_module.poll_account(account["id"])

    assert len(delivered) == 1
    event = delivered[0]["event"]
    assert event["text"] == "hello, are you open?"
    assert event["channel"] == "whatsapp_qr"
    assert event["user_id"] == "966555000111"
    assert event["customer_name"] == "A Customer"
    assert delivered[0]["company_id"] == alpha["id"]
    assert session.closed is True


def test_poll_account_does_not_redeliver_already_seen_messages(wired, alpha, monkeypatch):
    import channels.whatsapp_qr.poller as poller_module

    account = _connect_via_service(alpha)

    with wired.control() as conn:
        conn.execute(
            "UPDATE channel_accounts SET config_json = ? WHERE id = ?",
            (json.dumps({"seen_message_counts": {"966555000111": 2}}), account["id"]),
        )
        conn.commit()

    _wire_poller(
        monkeypatch,
        chats=[
            {
                "chat_id": "966555000111",
                "chat_name": "A Customer",
                "messages": [
                    {"text": "already seen one", "is_outgoing": False},
                    {"text": "already seen two", "is_outgoing": False},
                ],
            }
        ],
    )

    delivered = []
    monkeypatch.setattr(
        poller_module,
        "process_inbound_event",
        lambda **kwargs: delivered.append(kwargs) or {},
    )

    poller_module.poll_account(account["id"])

    assert delivered == []


def test_poll_account_advances_the_seen_count_cursor(wired, alpha, monkeypatch):
    import channels.whatsapp_qr.poller as poller_module

    account = _connect_via_service(alpha)
    _wire_poller(
        monkeypatch,
        chats=[
            {
                "chat_id": "966555000111",
                "chat_name": "A Customer",
                "messages": [{"text": "hi", "is_outgoing": False}],
            }
        ],
    )
    monkeypatch.setattr(poller_module, "process_inbound_event", lambda **kwargs: {})

    poller_module.poll_account(account["id"])

    with wired.control() as conn:
        row = conn.execute(
            "SELECT config_json FROM channel_accounts WHERE id = ?", (account["id"],)
        ).fetchone()

    stored = json.loads(row["config_json"])
    assert stored["seen_message_counts"]["966555000111"] == 1


def test_poll_account_survives_an_expired_session(wired, alpha, monkeypatch):
    import channels.whatsapp_qr.poller as poller_module
    from channels.whatsapp_qr.browser import WhatsAppSessionError

    account = _connect_via_service(alpha)
    _wire_poller(monkeypatch, open_error=WhatsAppSessionError("expired"))

    poller_module.poll_account(account["id"])  # must not raise


def test_poll_account_does_nothing_without_a_sealed_session(wired, alpha):
    import channels.whatsapp_qr.poller as poller_module
    import backend.services.channel_account_service as service_module

    account = service_module.channel_account_service.create_account(
        company_id=alpha["id"],
        channel="whatsapp_qr",
        name="No Session",
        values={"external_account_id": "1", "access_token": json.dumps({"cookies": []})},
    )

    with poller_module.database_manager.control() as conn:
        conn.execute(
            "UPDATE channel_accounts SET access_token_sealed = NULL WHERE id = ?",
            (account["id"],),
        )
        conn.commit()

    poller_module.poll_account(account["id"])  # must not raise


def test_poll_all_accounts_isolates_one_companys_failure_from_another(
    wired, alpha, beta, monkeypatch
):
    import channels.whatsapp_qr.poller as poller_module

    account_a = _connect_via_service(alpha, phone_number="1")
    account_b = _connect_via_service(beta, phone_number="2")

    polled = []

    def _fake_poll_account(account_id):
        polled.append(account_id)
        if account_id == account_a["id"]:
            raise RuntimeError("account a exploded")

    monkeypatch.setattr(poller_module, "poll_account", _fake_poll_account)

    poller_module.poll_all_accounts()  # must not raise

    assert set(polled) == {account_a["id"], account_b["id"]}


# ------------------------------------------------------------------ sender


def _wire_sender(monkeypatch, *, sent=True, open_error=None):
    import channels.whatsapp_qr.sender as sender_module

    session = _FakeWhatsAppSession()
    calls = []

    def fake_open(storage_state):
        if open_error:
            raise open_error
        return session

    def fake_send(session_arg, chat_id, text):
        calls.append({"chat_id": chat_id, "text": text})
        return sent

    monkeypatch.setattr(sender_module, "open_authenticated_page", fake_open)
    monkeypatch.setattr(sender_module, "send_text_message", fake_send)
    return session, calls


def test_send_delivers_through_the_connected_session(wired, alpha, monkeypatch):
    from channels.whatsapp_qr.sender import send_whatsapp_qr_text

    _connect_via_service(alpha)
    session, calls = _wire_sender(monkeypatch, sent=True)

    result = send_whatsapp_qr_text(
        recipient_id="966555000111", text="Thanks for reaching out!", company_id=alpha["id"]
    )

    assert result["ok"] is True
    assert calls == [{"chat_id": "966555000111", "text": "Thanks for reaching out!"}]
    assert session.closed is True


def test_send_appends_buttons_as_plain_text(wired, alpha, monkeypatch):
    from channels.whatsapp_qr.sender import send_whatsapp_qr_text

    _connect_via_service(alpha)
    _session, calls = _wire_sender(monkeypatch, sent=True)

    send_whatsapp_qr_text(
        recipient_id="966555000111",
        text="Pick one:",
        company_id=alpha["id"],
        buttons=["Sales", "Support"],
    )

    assert "- Sales" in calls[0]["text"]
    assert "- Support" in calls[0]["text"]


def test_send_without_a_connected_account_fails(wired, alpha, monkeypatch):
    from channels.whatsapp_qr.sender import send_whatsapp_qr_text

    _session, calls = _wire_sender(monkeypatch, sent=True)

    result = send_whatsapp_qr_text(
        recipient_id="966555000111", text="hi", company_id=alpha["id"]
    )

    assert result["ok"] is False
    assert calls == []


def test_send_with_an_expired_session_fails_without_a_retry(wired, alpha, monkeypatch):
    from channels.whatsapp_qr.sender import send_whatsapp_qr_text
    from channels.whatsapp_qr.browser import WhatsAppSessionError

    _connect_via_service(alpha)
    _session, calls = _wire_sender(
        monkeypatch, open_error=WhatsAppSessionError("This session has expired.")
    )

    result = send_whatsapp_qr_text(
        recipient_id="966555000111", text="hi", company_id=alpha["id"]
    )

    assert result["ok"] is False
    assert "expired" in result["error"]
    assert calls == []


def test_send_failure_is_reported_not_raised(wired, alpha, monkeypatch):
    from channels.whatsapp_qr.sender import send_whatsapp_qr_text

    _connect_via_service(alpha)
    _wire_sender(monkeypatch, sent=False)

    result = send_whatsapp_qr_text(
        recipient_id="966555000111", text="hi", company_id=alpha["id"]
    )

    assert result["ok"] is False
    assert result["error"]
