"""A Meta/WhatsApp Graph API response can be HTTP 200 and still be a
provider-side rejection -- an expired token, a disallowed recipient, a rate
limit -- with the failure described only in an ``error`` object in the
body. Before `channels/meta/graph.py` existed, every sender here judged
success by ``response.is_success`` alone, so a send Meta itself refused was
recorded as delivered: the pending-reply queue closed it as answered, nothing
retried it, and a customer's message silently never arrived.

This file has two layers: `graph_call_succeeded` itself, and one test per
real call site proving it is actually being used there -- a 200-with-error
response from the provider must never come back as `ok`/`sent`: True.
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_body: dict[str, Any] | None = None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.content = b"{}" if json_body is not None else b""
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._json_body


# ------------------------------------------------------------------ the primitive


def test_graph_call_succeeded_is_true_for_a_clean_200():
    from channels.meta.graph import graph_call_succeeded

    response = _FakeResponse(status_code=200, json_body={"message_id": "abc"})
    assert graph_call_succeeded(response, {"message_id": "abc"}) is True


def test_graph_call_succeeded_is_false_for_a_200_with_an_error_body():
    from channels.meta.graph import graph_call_succeeded

    response = _FakeResponse(status_code=200, json_body={"error": {"message": "Invalid token"}})
    assert graph_call_succeeded(response, {"error": {"message": "Invalid token"}}) is False


def test_graph_call_succeeded_is_false_for_a_real_http_failure():
    from channels.meta.graph import graph_call_succeeded

    response = _FakeResponse(status_code=500, json_body={})
    assert graph_call_succeeded(response, {}) is False


# ------------------------------------------------------------------ meta/sender.py


def test_send_meta_text_reports_failure_for_a_200_wrapped_error(monkeypatch):
    import channels.meta.sender as sender_module

    monkeypatch.setattr(
        sender_module, "resolve", lambda company_id, channel: {"access_token": "tok"}
    )
    monkeypatch.setattr(
        sender_module.httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            status_code=200, json_body={"error": {"message": "Invalid OAuth token"}}
        ),
    )

    result = sender_module.send_meta_text(
        recipient_id="999999999", text="hello", company_id=1
    )

    assert result["ok"] is False
    assert result["response"]["error"]["message"] == "Invalid OAuth token"


def test_send_meta_text_still_reports_success_for_a_clean_200(monkeypatch):
    import channels.meta.sender as sender_module

    monkeypatch.setattr(
        sender_module, "resolve", lambda company_id, channel: {"access_token": "tok"}
    )
    monkeypatch.setattr(
        sender_module.httpx,
        "post",
        lambda *a, **k: _FakeResponse(status_code=200, json_body={"message_id": "mid-1"}),
    )

    result = sender_module.send_meta_text(
        recipient_id="999999999", text="hello", company_id=1
    )

    assert result["ok"] is True


# ------------------------------------------------------------------ whatsapp/sender.py


def test_send_whatsapp_text_reports_failure_for_a_200_wrapped_error(monkeypatch):
    import channels.whatsapp.sender as sender_module

    monkeypatch.setattr(
        sender_module,
        "resolve",
        lambda company_id, channel: {"access_token": "tok", "phone_number_id": "123"},
    )
    monkeypatch.setattr(
        sender_module.httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            status_code=200, json_body={"error": {"message": "Recipient not on WhatsApp"}}
        ),
    )

    result = sender_module.send_whatsapp_text(to="15550001111", text="hi", company_id=1)

    assert result["sent"] is False


def test_send_whatsapp_media_reports_failure_for_a_200_wrapped_error(monkeypatch):
    import channels.whatsapp.sender as sender_module

    monkeypatch.setattr(
        sender_module,
        "resolve",
        lambda company_id, channel: {"access_token": "tok", "phone_number_id": "123"},
    )
    monkeypatch.setattr(
        sender_module.httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            status_code=200, json_body={"error": {"message": "Unsupported media type"}}
        ),
    )

    result = sender_module.send_whatsapp_media(
        to="15550001111",
        media_url="https://example.com/file.pdf",
        media_type="document",
        company_id=1,
    )

    assert result["sent"] is False


# ------------------------------------------------------------------ post_publisher.py


def test_publish_post_reports_failure_for_a_200_wrapped_error(monkeypatch):
    import channels.post_publisher as publisher_module

    monkeypatch.setattr(
        publisher_module,
        "resolve",
        lambda company_id, channel, account_id=None: {
            "access_token": "tok",
            "page_id": "789",
        },
    )
    monkeypatch.setattr(
        publisher_module.httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            status_code=200, json_body={"error": {"message": "Page token expired"}}
        ),
    )

    result = publisher_module.publish_post(
        company_id=1, channel="messenger", body="hello world"
    )

    assert result["ok"] is False


# ------------------------------------------------------------------ comment_sender.py


def test_publish_comment_reply_reports_failure_for_a_200_wrapped_error(monkeypatch):
    from channels import comment_sender

    monkeypatch.setattr(
        comment_sender, "resolve", lambda company_id, channel: {"access_token": "tok"}
    )
    monkeypatch.setattr(
        comment_sender.httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            status_code=200, json_body={"error": {"message": "Comment not found"}}
        ),
    )

    result = comment_sender.publish_comment_reply(
        company_id=1, channel="messenger", provider_comment_id="c1", message="thanks!"
    )

    assert result["ok"] is False


# ------------------------------------------------------------------ meta/profile.py


def test_resolve_meta_profile_returns_empty_for_a_200_wrapped_error(monkeypatch):
    import channels.meta.profile as profile_module

    monkeypatch.setattr(
        profile_module, "resolve", lambda company_id, channel: {"access_token": "tok"}
    )
    monkeypatch.setattr(
        profile_module.httpx,
        "get",
        lambda *a, **k: _FakeResponse(
            status_code=200, json_body={"error": {"message": "Invalid access token"}}
        ),
    )

    profile = profile_module.resolve_meta_profile("unique-user-1", 1, "messenger")

    assert profile == {}


def test_resolve_meta_profile_still_resolves_a_clean_200(monkeypatch):
    import channels.meta.profile as profile_module

    monkeypatch.setattr(
        profile_module, "resolve", lambda company_id, channel: {"access_token": "tok"}
    )
    monkeypatch.setattr(
        profile_module.httpx,
        "get",
        lambda *a, **k: _FakeResponse(
            status_code=200,
            json_body={"first_name": "Jane", "last_name": "Doe", "profile_pic": "https://x"},
        ),
    )

    profile = profile_module.resolve_meta_profile("unique-user-2", 1, "messenger")

    assert profile["customer_name"] == "Jane Doe"
