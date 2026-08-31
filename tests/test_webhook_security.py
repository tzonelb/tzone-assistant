"""Tests for inbound webhook authenticity and batching.

Before this, both webhook endpoints accepted any request. A forged POST created
a customer, a conversation, a notification, and queued a paid model call whose
reply went out through the connected page token.
"""

from __future__ import annotations

import json
import logging

import pytest

from channels.meta.parser import parse_meta_events
from channels.webhook_security import (
    SOURCE_ACCOUNT,
    SOURCE_CURRENT,
    SOURCE_PREVIOUS,
    WebhookVerificationError,
    compute_signature,
    verify_signature,
    verify_token_challenge,
    verify_webhook_signature,
)
from channels.whatsapp.webhook import parse_whatsapp_events


APP_SECRET = "test-app-secret"
PREVIOUS_APP_SECRET = "rotated-out-app-secret"
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
# Rotation overlap
# ----------------------------------------------------------------------
#
# One Meta app serves the whole platform, so there is one platform secret. It
# still has to be rotatable: Meta signs with whichever secret was current when
# it queued the delivery, so without an overlap a rotation silently discards
# every event already in flight.


LOGGER_NAME = "channels.webhook_security"


def _page_body(page_id: str) -> bytes:
    """A minimal delivery naming the page it arrived on."""
    return json.dumps(
        {"object": "page", "entry": [{"id": page_id, "messaging": []}]}
    ).encode("utf-8")


def _whatsapp_body(phone_number_id: str) -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {"value": {"metadata": {"phone_number_id": phone_number_id}}}
                    ]
                }
            ],
        }
    ).encode("utf-8")


def _no_account_secret(routing):
    """Stand-in for the ordinary case: nobody brings their own Meta app."""
    return None


def test_body_signed_with_the_current_secret_is_accepted():
    """Virtually all traffic, and it must stay the first thing tried."""
    match = verify_webhook_signature(
        raw_body=BODY,
        signature_header=compute_signature(BODY, APP_SECRET),
        app_secret=APP_SECRET,
        previous_app_secret=PREVIOUS_APP_SECRET,
        account_secret_lookup=_no_account_secret,
    )

    assert match.source == SOURCE_CURRENT


def test_current_secret_match_never_reaches_the_database():
    """The per-account lookup is a database read per request. On the path that
    carries 99% of traffic it must not run at all."""
    calls = []

    def lookup(routing):
        calls.append(routing)
        return None

    verify_webhook_signature(
        raw_body=_page_body("PAGE_1"),
        signature_header=compute_signature(_page_body("PAGE_1"), APP_SECRET),
        app_secret=APP_SECRET,
        previous_app_secret=PREVIOUS_APP_SECRET,
        account_secret_lookup=lookup,
    )

    assert calls == []


def test_body_signed_with_the_previous_secret_is_accepted(caplog):
    """The queue Meta built up before the rotation still has to drain."""
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        match = verify_webhook_signature(
            raw_body=BODY,
            signature_header=compute_signature(BODY, PREVIOUS_APP_SECRET),
            app_secret=APP_SECRET,
            previous_app_secret=PREVIOUS_APP_SECRET,
            account_secret_lookup=_no_account_secret,
        )

    assert match.source == SOURCE_PREVIOUS

    # The operator watches for this line to stop before clearing the old value.
    assert SOURCE_PREVIOUS in caplog.text
    # Which secret matched, never the secret.
    assert PREVIOUS_APP_SECRET not in caplog.text
    assert APP_SECRET not in caplog.text


def test_clearing_the_previous_secret_stops_accepting_it():
    """The point of finishing a rotation: the leaked or retired secret is dead.
    If the old value kept working after being cleared, rotation would be
    theatre."""
    signature = compute_signature(BODY, PREVIOUS_APP_SECRET)

    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            raw_body=BODY,
            signature_header=signature,
            app_secret=APP_SECRET,
            previous_app_secret="",
            account_secret_lookup=_no_account_secret,
        )


def test_unsigned_body_is_still_refused_during_a_rotation():
    """An overlap widens which secrets are accepted, not whether one is needed."""
    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            raw_body=BODY,
            signature_header=None,
            app_secret=APP_SECRET,
            previous_app_secret=PREVIOUS_APP_SECRET,
            account_secret_lookup=_no_account_secret,
        )


def test_third_party_signature_is_refused_during_a_rotation():
    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            raw_body=BODY,
            signature_header=compute_signature(BODY, "attacker-secret"),
            app_secret=APP_SECRET,
            previous_app_secret=PREVIOUS_APP_SECRET,
            account_secret_lookup=_no_account_secret,
        )


def test_no_configured_secret_at_all_still_fails_closed():
    """Neither a current nor a previous secret means nothing can be verified,
    and an unverifiable request is refused rather than waved through."""
    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            raw_body=BODY,
            signature_header=compute_signature(BODY, APP_SECRET),
            app_secret="",
            previous_app_secret="",
            account_secret_lookup=_no_account_secret,
        )


def test_allow_unsigned_still_only_covers_an_unconfigured_secret():
    """ALLOW_UNSIGNED_WEBHOOKS is the local-tunnel escape hatch. It must not
    become a way to accept a bad signature on a deployment that does have a
    secret configured."""
    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            raw_body=BODY,
            signature_header=compute_signature(BODY, "attacker-secret"),
            app_secret=APP_SECRET,
            previous_app_secret=PREVIOUS_APP_SECRET,
            allow_unsigned=True,
            account_secret_lookup=_no_account_secret,
        )

    verify_webhook_signature(
        raw_body=BODY,
        signature_header=None,
        app_secret="",
        previous_app_secret="",
        allow_unsigned=True,
        account_secret_lookup=_no_account_secret,
    )


# ----------------------------------------------------------------------
# Per-account app secrets
# ----------------------------------------------------------------------
#
# A customer big enough to bring their own Meta app signs with their own
# secret. That secret is already stored, sealed under their company key, on
# their channel account — until now nothing read it.


@pytest.fixture()
def accounts(platform, monkeypatch):
    """Point the channel service, and the lookup behind verification, at the
    test platform's databases."""
    import sys

    import database.manager as manager_module

    # Imported before the sweep below: a module that has not been imported yet
    # holds no reference to rebind, and would later import the real singleton.
    import backend.services.channel_account_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.channel_account_service" in rebound

    from backend.services.channel_account_service import channel_account_service

    return channel_account_service


def _connect_page(accounts, company, page_id, app_secret=None):
    values = {"page_id": page_id, "access_token": "page-token"}

    if app_secret:
        values["app_secret"] = app_secret

    return accounts.create_account(
        company_id=company["id"],
        channel="messenger",
        name=f"Page {page_id}",
        values=values,
    )


def test_account_secret_is_accepted_when_the_platform_secrets_do_not_match(
    accounts, alpha, caplog
):
    """The customer's own Meta app signs with their own secret, so neither
    platform secret can verify it. Refusing would drop their traffic."""
    _connect_page(accounts, alpha, "PAGE_ALPHA", app_secret="alpha-app-secret")

    body = _page_body("PAGE_ALPHA")

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        match = verify_webhook_signature(
            raw_body=body,
            signature_header=compute_signature(body, "alpha-app-secret"),
            app_secret=APP_SECRET,
            previous_app_secret=PREVIOUS_APP_SECRET,
        )

    assert match.source == SOURCE_ACCOUNT
    assert match.company_id == alpha["id"]
    assert "alpha-app-secret" not in caplog.text


def test_one_companys_secret_cannot_sign_for_another_companys_page(
    accounts, alpha, beta
):
    """The isolation property. A per-account secret authenticates that account
    and nothing else — otherwise any customer with their own Meta app could
    forge events for every other company on the platform."""
    _connect_page(accounts, alpha, "PAGE_ALPHA", app_secret="alpha-app-secret")
    _connect_page(accounts, beta, "PAGE_BETA", app_secret="beta-app-secret")

    body = _page_body("PAGE_BETA")

    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            raw_body=body,
            signature_header=compute_signature(body, "alpha-app-secret"),
            app_secret=APP_SECRET,
            previous_app_secret=PREVIOUS_APP_SECRET,
        )

    # And the refusal above is isolation, not a lookup that quietly found
    # nothing: beta's own secret verifies the very same body.
    match = verify_webhook_signature(
        raw_body=body,
        signature_header=compute_signature(body, "beta-app-secret"),
        app_secret=APP_SECRET,
        previous_app_secret=PREVIOUS_APP_SECRET,
    )

    assert match.company_id == beta["id"]


def test_account_without_its_own_secret_is_not_a_way_in(accounts, alpha):
    """Almost every account stores no app secret. Those must fall through to a
    rejection, not to some empty-secret comparison that anyone can satisfy."""
    _connect_page(accounts, alpha, "PAGE_ALPHA")

    body = _page_body("PAGE_ALPHA")

    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            raw_body=body,
            signature_header=compute_signature(body, ""),
            app_secret=APP_SECRET,
            previous_app_secret=PREVIOUS_APP_SECRET,
        )


def test_unknown_page_cannot_be_verified_by_any_account_secret(accounts, alpha):
    """A body naming a page this platform does not serve has no secret to be
    checked against, so it is refused rather than left unchecked."""
    _connect_page(accounts, alpha, "PAGE_ALPHA", app_secret="alpha-app-secret")

    body = _page_body("PAGE_NOBODY")

    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            raw_body=body,
            signature_header=compute_signature(body, "alpha-app-secret"),
            app_secret=APP_SECRET,
            previous_app_secret=PREVIOUS_APP_SECRET,
        )


def test_whatsapp_account_secret_is_found_by_phone_number_id(accounts, alpha):
    """WhatsApp routes on the phone number id, so the override has to read that
    payload shape too."""
    accounts.create_account(
        company_id=alpha["id"],
        channel="whatsapp",
        name="Alpha WhatsApp",
        values={"phone_number_id": "PHONE_ALPHA", "app_secret": "alpha-wa-secret"},
    )

    body = _whatsapp_body("PHONE_ALPHA")

    match = verify_webhook_signature(
        raw_body=body,
        signature_header=compute_signature(body, "alpha-wa-secret"),
        app_secret=APP_SECRET,
        previous_app_secret=PREVIOUS_APP_SECRET,
    )

    assert match.source == SOURCE_ACCOUNT
    assert match.company_id == alpha["id"]


def test_a_failing_account_lookup_rejects_rather_than_accepts(accounts, alpha):
    """A database that cannot answer is not an authorisation."""
    _connect_page(accounts, alpha, "PAGE_ALPHA", app_secret="alpha-app-secret")

    def broken_lookup(routing):
        raise RuntimeError("control database unavailable")

    body = _page_body("PAGE_ALPHA")

    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            raw_body=body,
            signature_header=compute_signature(body, "alpha-app-secret"),
            app_secret=APP_SECRET,
            previous_app_secret=PREVIOUS_APP_SECRET,
            account_secret_lookup=broken_lookup,
        )


def test_a_body_that_is_not_json_is_refused_without_a_lookup():
    """Step three parses the body only far enough to find a routing id. A body
    that is not JSON simply has none, and is refused."""
    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            raw_body=b"not json at all",
            signature_header=compute_signature(b"not json at all", "attacker"),
            app_secret=APP_SECRET,
            previous_app_secret=PREVIOUS_APP_SECRET,
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
