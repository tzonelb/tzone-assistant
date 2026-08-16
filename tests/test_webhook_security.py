"""Tests for inbound webhook authenticity and batching.

Before this, both webhook endpoints accepted any request. A forged POST created
a customer, a conversation, a notification, and queued a paid model call whose
reply went out through the connected page token.
"""

from __future__ import annotations

import pytest

from channels.meta.parser import parse_meta_events
from channels.webhook_security import (
    WebhookVerificationError,
    compute_signature,
    verify_signature,
    verify_token_challenge,
)
from channels.whatsapp.webhook import parse_whatsapp_events


APP_SECRET = "test-app-secret"
BODY = b'{"object":"page","entry":[]}'


# ----------------------------------------------------------------------
# Signatures
# ----------------------------------------------------------------------


def test_correctly_signed_body_is_accepted():
    """The happy path must keep working; a check nobody can pass gets disabled."""
    verify_signature(
        raw_body=BODY,
        signature_header=compute_signature(BODY, APP_SECRET),
        app_secret=APP_SECRET,
    )


def test_unsigned_request_is_rejected():
    """This is the exact request that previously created forged conversations."""
    with pytest.raises(WebhookVerificationError):
        verify_signature(raw_body=BODY, signature_header=None, app_secret=APP_SECRET)


def test_signature_from_a_different_secret_is_rejected():
    """Knowing the URL must not be enough; the caller has to hold the secret."""
    with pytest.raises(WebhookVerificationError):
        verify_signature(
            raw_body=BODY,
            signature_header=compute_signature(BODY, "attacker-secret"),
            app_secret=APP_SECRET,
        )


def test_tampered_body_is_rejected():
    """A valid signature must not authorise a body someone edited in transit."""
    signature = compute_signature(BODY, APP_SECRET)

    with pytest.raises(WebhookVerificationError):
        verify_signature(
            raw_body=b'{"object":"page","entry":[{"injected":true}]}',
            signature_header=signature,
            app_secret=APP_SECRET,
        )


def test_missing_app_secret_fails_closed():
    """An unconfigured secret must reject traffic, not wave it through. Failing
    open here would silently reproduce the original hole on any deployment that
    forgot the variable."""
    with pytest.raises(WebhookVerificationError):
        verify_signature(
            raw_body=BODY,
            signature_header=compute_signature(BODY, APP_SECRET),
            app_secret="",
        )


def test_unsigned_is_allowed_only_when_explicitly_enabled():
    """Local development against a tunnel needs an escape hatch, but it has to
    be deliberate rather than the default."""
    verify_signature(
        raw_body=BODY,
        signature_header=None,
        app_secret="",
        allow_unsigned=True,
    )


def test_malformed_signature_header_is_rejected():
    with pytest.raises(WebhookVerificationError):
        verify_signature(
            raw_body=BODY,
            signature_header="not-a-signature",
            app_secret=APP_SECRET,
        )


# ----------------------------------------------------------------------
# Subscription handshake
# ----------------------------------------------------------------------


def test_verification_challenge_requires_the_right_token():
    assert verify_token_challenge(
        mode="subscribe", token="expected", expected_token="expected"
    )
    assert not verify_token_challenge(
        mode="subscribe", token="wrong", expected_token="expected"
    )


def test_verification_challenge_rejects_empty_expected_token():
    """An unset verify token must not make every challenge succeed."""
    assert not verify_token_challenge(
        mode="subscribe", token="", expected_token=""
    )


# ----------------------------------------------------------------------
# Batching
# ----------------------------------------------------------------------


def test_every_message_in_a_meta_batch_is_parsed():
    """Meta batches deliveries. Reading only entry[0].messaging[0] silently
    dropped customer messages — worst under bursts, which is exactly when
    customers send several in a row."""
    payload = {
        "object": "page",
        "entry": [
            {
                "id": "PAGE_1",
                "messaging": [
                    {
                        "sender": {"id": "u1"},
                        "recipient": {"id": "PAGE_1"},
                        "message": {"mid": "m1", "text": "first"},
                    },
                    {
                        "sender": {"id": "u1"},
                        "recipient": {"id": "PAGE_1"},
                        "message": {"mid": "m2", "text": "second"},
                    },
                ],
            },
            {
                "id": "PAGE_1",
                "messaging": [
                    {
                        "sender": {"id": "u2"},
                        "recipient": {"id": "PAGE_1"},
                        "message": {"mid": "m3", "text": "third"},
                    }
                ],
            },
        ],
    }

    events = parse_meta_events(payload)

    assert [event["text"] for event in events] == ["first", "second", "third"]
    assert [event["message_id"] for event in events] == ["m1", "m2", "m3"]
    assert {event["page_id"] for event in events} == {"PAGE_1"}


def test_every_message_in_a_whatsapp_batch_is_parsed():
    """Same defect on the WhatsApp path."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "PHONE_1"},
                            "messages": [
                                {
                                    "from": "9611",
                                    "id": "w1",
                                    "type": "text",
                                    "text": {"body": "one"},
                                },
                                {
                                    "from": "9612",
                                    "id": "w2",
                                    "type": "text",
                                    "text": {"body": "two"},
                                },
                            ],
                        }
                    }
                ]
            }
        ],
    }

    events = parse_whatsapp_events(payload)

    assert [event["text"] for event in events] == ["one", "two"]
    assert {event["phone_number_id"] for event in events} == {"PHONE_1"}


def test_echo_messages_are_ignored():
    """Echoes are our own outbound messages. Treating them as customer input
    makes the assistant answer itself in a loop."""
    payload = {
        "object": "page",
        "entry": [
            {
                "id": "PAGE_1",
                "messaging": [
                    {
                        "sender": {"id": "PAGE_1"},
                        "recipient": {"id": "u1"},
                        "message": {"is_echo": True, "text": "our own reply"},
                    }
                ],
            }
        ],
    }

    events = parse_meta_events(payload)

    assert len(events) == 1
    assert events[0]["ignored"] is True
    assert events[0]["reason"] == "echo_message"


def test_instagram_events_are_labelled_instagram():
    """Mislabelling stored Instagram conversations as Messenger meant replies
    went out on the wrong channel."""
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": "IG_1",
                "messaging": [
                    {
                        "sender": {"id": "u1"},
                        "recipient": {"id": "IG_1"},
                        "message": {"mid": "m1", "text": "hello"},
                    }
                ],
            }
        ],
    }

    assert parse_meta_events(payload)[0]["channel"] == "instagram"
