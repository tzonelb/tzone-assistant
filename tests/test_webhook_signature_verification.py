"""Tests for the Meta/WhatsApp webhook signature verification fix.

Both POST /webhook/meta and POST /webhook/whatsapp/ used to accept any
request unconditionally -- no X-Hub-Signature-256/HMAC check existed
anywhere, so anyone on the internet could inject fake customer messages
(triggering unlimited paid AI completions) or, for WhatsApp, real
outbound sends -- with no credential needed beyond the URL path.

This file proves:
  * a request with a missing/invalid signature is rejected with 403 and
    never reaches process_meta_payload / the WhatsApp message handling
  * a request with a correctly-computed signature (HMAC-SHA256 of the
    raw body with a test app secret) is accepted and processed normally

Run with: python3 -m pytest tests/test_webhook_signature_verification.py -v
"""
import hashlib
import hmac
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from channels.meta.verifier import verify_meta_signature
from config.settings import config

TEST_APP_SECRET = "test-app-secret-for-signature-verification"


def _sign(body: bytes, secret: str = TEST_APP_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------------------
# Unit tests for channels/meta/verifier.py
# ---------------------------------------------------------------------------

def test_verify_meta_signature_accepts_correct_signature():
    body = b'{"hello": "world"}'
    header = _sign(body)
    assert verify_meta_signature(body, header, TEST_APP_SECRET) is True


def test_verify_meta_signature_rejects_wrong_secret():
    body = b'{"hello": "world"}'
    header = _sign(body, secret="wrong-secret")
    assert verify_meta_signature(body, header, TEST_APP_SECRET) is False


def test_verify_meta_signature_rejects_tampered_body():
    body = b'{"hello": "world"}'
    header = _sign(body)
    assert verify_meta_signature(b'{"hello": "mallory"}', header, TEST_APP_SECRET) is False


def test_verify_meta_signature_rejects_missing_header():
    assert verify_meta_signature(b"{}", None, TEST_APP_SECRET) is False


def test_verify_meta_signature_rejects_malformed_header():
    assert verify_meta_signature(b"{}", "not-a-real-signature", TEST_APP_SECRET) is False


def test_verify_meta_signature_rejects_when_secret_empty():
    body = b"{}"
    header = _sign(body, secret="")
    assert verify_meta_signature(body, header, "") is False


# ---------------------------------------------------------------------------
# Integration tests for the POST route handlers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_rate_limiters():
    """Reset the shared in-process rate limiters before each test so
    unrelated tests never bleed into each other's request counts (the
    TestClient always presents as the same source IP).
    """
    import channels.common.rate_limiter as rl

    rl.meta_webhook_rate_limiter._hits.clear()
    rl.whatsapp_webhook_rate_limiter._hits.clear()
    yield
    rl.meta_webhook_rate_limiter._hits.clear()
    rl.whatsapp_webhook_rate_limiter._hits.clear()


@pytest.fixture()
def meta_client(monkeypatch):
    import channels.meta.webhook as meta_webhook_module

    monkeypatch.setattr(config, "FACEBOOK_APP_SECRET", TEST_APP_SECRET)

    calls = []

    def fake_process_meta_payload(payload):
        calls.append(payload)
        return {"status": "processed"}

    monkeypatch.setattr(meta_webhook_module, "process_meta_payload", fake_process_meta_payload)
    # Avoid writing to logs/meta_messages.log during tests.
    monkeypatch.setattr(meta_webhook_module, "log_meta_event", lambda *a, **k: None)

    app = FastAPI()
    app.include_router(meta_webhook_module.router)

    with TestClient(app) as client:
        yield client, calls


@pytest.fixture()
def whatsapp_client(monkeypatch):
    import channels.whatsapp.webhook as whatsapp_webhook_module

    monkeypatch.setattr(config, "FACEBOOK_APP_SECRET", TEST_APP_SECRET)
    monkeypatch.setattr(config, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")

    calls = []

    class FakeResponse:
        text = "ok"
        buttons = []

    def fake_handle_text(channel, user_id, message):
        calls.append({"channel": channel, "user_id": user_id, "message": message})
        return FakeResponse()

    def fake_send_whatsapp_text(to, text, buttons=None):
        return {"sent": True, "status_code": 200, "response": {}}

    monkeypatch.setattr(whatsapp_webhook_module.message_gateway, "handle_text", fake_handle_text)
    monkeypatch.setattr(whatsapp_webhook_module, "send_whatsapp_text", fake_send_whatsapp_text)

    app = FastAPI()
    app.include_router(whatsapp_webhook_module.router)

    with TestClient(app) as client:
        yield client, calls


def _meta_payload():
    return {
        "object": "page",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "123"},
                        "recipient": {"id": "456"},
                        "message": {"text": "hello"},
                    }
                ]
            }
        ],
    }


def _whatsapp_payload():
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "1234567890"},
                            "messages": [
                                {
                                    "from": "15551234567",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


def test_meta_webhook_rejects_missing_signature(meta_client):
    client, calls = meta_client
    body = json.dumps(_meta_payload()).encode()

    resp = client.post("/webhook/meta", content=body, headers={"Content-Type": "application/json"})

    assert resp.status_code == 403
    assert calls == []


def test_meta_webhook_rejects_invalid_signature(meta_client):
    client, calls = meta_client
    body = json.dumps(_meta_payload()).encode()

    resp = client.post(
        "/webhook/meta",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )

    assert resp.status_code == 403
    assert calls == []


def test_meta_webhook_accepts_valid_signature(meta_client):
    client, calls = meta_client
    body = json.dumps(_meta_payload()).encode()

    resp = client.post(
        "/webhook/meta",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body),
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert len(calls) == 1


def test_whatsapp_webhook_rejects_missing_signature(whatsapp_client):
    client, calls = whatsapp_client
    body = json.dumps(_whatsapp_payload()).encode()

    resp = client.post("/webhook/whatsapp/", content=body, headers={"Content-Type": "application/json"})

    assert resp.status_code == 403
    assert calls == []


def test_whatsapp_webhook_rejects_invalid_signature(whatsapp_client):
    client, calls = whatsapp_client
    body = json.dumps(_whatsapp_payload()).encode()

    resp = client.post(
        "/webhook/whatsapp/",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=" + "f" * 64,
        },
    )

    assert resp.status_code == 403
    assert calls == []


def test_whatsapp_webhook_accepts_valid_signature(whatsapp_client):
    client, calls = whatsapp_client
    body = json.dumps(_whatsapp_payload()).encode()

    resp = client.post(
        "/webhook/whatsapp/",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body),
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "received"
    assert len(calls) == 1


def test_meta_webhook_rate_limited_after_threshold(meta_client, monkeypatch):
    import channels.common.rate_limiter as rl

    client, calls = meta_client
    monkeypatch.setattr(rl.meta_webhook_rate_limiter, "max_requests", 2)

    body = json.dumps(_meta_payload()).encode()
    headers = {"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)}

    assert client.post("/webhook/meta", content=body, headers=headers).status_code == 200
    assert client.post("/webhook/meta", content=body, headers=headers).status_code == 200
    resp = client.post("/webhook/meta", content=body, headers=headers)

    assert resp.status_code == 429
    assert len(calls) == 2
