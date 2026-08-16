"""Ceilings that keep a signed webhook flood from stalling the platform.

A valid signature says who sent a delivery. It says nothing about how big it is,
how many events it expands into, or how much work answering it costs. Four
things multiplied that gap into a whole-platform outage, and each has a test
here:

* a body was read into memory whole, with no cap in the application at all;
* one body could expand into hundreds of thousands of events, each costing
  several database writes;
* the HTTP response waited for every one of them, holding a thread from the pool
  that also serves every ``def`` route — so a flood on the webhook froze the
  dashboard;
* the profile cache and the pending-reply batch both grew without limit, the
  batch's delivery being pushed further out by every new message so a sustained
  flood at one customer deferred its reply for ever.

The refusals matter as much as the limits: each one is cheap, and each one is
logged with enough detail to recognise an attack in the log rather than
inferring it from a stalled server.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from channels.meta import webhook as meta_webhook
from channels.meta.parser import parse_meta_events
from channels.webhook_security import SIGNATURE_HEADER, compute_signature
from channels.whatsapp.webhook import parse_whatsapp_events
from config.settings import config
from channels import webhook_limits


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _meta_payload(count: int, *, page_id: str = "PAGE_1") -> dict:
    """One delivery carrying ``count`` customer messages."""
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "messaging": [
                    {
                        "sender": {"id": f"u{index}"},
                        "recipient": {"id": page_id},
                        "message": {"mid": f"m{index}", "text": f"message {index}"},
                    }
                    for index in range(count)
                ],
            }
        ],
    }


def _whatsapp_payload(count: int) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "PHONE_1"},
                            "messages": [
                                {
                                    "from": f"96{index}",
                                    "id": f"w{index}",
                                    "type": "text",
                                    "text": {"body": f"message {index}"},
                                }
                                for index in range(count)
                            ],
                        }
                    }
                ]
            }
        ],
    }


@pytest.fixture()
def accepted(monkeypatch) -> list[list[dict]]:
    """A Meta endpoint whose processing is recorded instead of performed.

    The endpoint hands its events to ``_process_events`` on a background task;
    swapping that function keeps these tests about the limits rather than about
    the database underneath them.
    """
    batches: list[list[dict]] = []

    def record(events):
        batches.append(list(events))
        return []

    monkeypatch.setattr(meta_webhook, "_process_events", record)
    monkeypatch.setattr(meta_webhook, "_process_comments", record)

    return batches


APP_SECRET = "test-app-secret"


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    """The Meta webhook mounted on its own app, with signatures required.

    The traffic these limits exist for is correctly signed — that is the whole
    point of the defect — so the tests sign it too.
    """
    monkeypatch.setattr(config, "META_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(config, "META_APP_SECRET_PREVIOUS", "")
    monkeypatch.setattr(config, "ALLOW_UNSIGNED_WEBHOOKS", False)

    app = FastAPI()
    app.include_router(meta_webhook.router)

    with TestClient(app) as test_client:
        yield test_client


def _signed_post(client: TestClient, payload: dict):
    """Post a delivery Meta itself could have sent."""
    body = json.dumps(payload).encode("utf-8")

    return client.post(
        "/webhook/meta",
        content=body,
        headers={
            "Content-Type": "application/json",
            SIGNATURE_HEADER: compute_signature(body, APP_SECRET),
        },
    )


def _wait_for_processing(timeout: float = 5.0) -> None:
    """Give the background task its turn. The response does not wait for it —
    that is the point of the change — so the test has to."""
    deadline = time.monotonic() + timeout

    while webhook_limits.in_flight_events() and time.monotonic() < deadline:
        time.sleep(0.02)


# ----------------------------------------------------------------------
# Body size
# ----------------------------------------------------------------------


def test_an_oversized_body_is_refused(client, monkeypatch, caplog):
    """The cap nginx provides is a deployment assumption, not a property of the
    system: a container reached directly had none at all.

    The body here is unsigned, and the answer is 413 rather than 403 — the size
    check comes before the HMAC, so refusing costs nothing.
    """
    monkeypatch.setattr(config, "WEBHOOK_MAX_BODY_BYTES", 2048)

    with caplog.at_level(logging.WARNING):
        response = client.post(
            "/webhook/meta",
            content=b'{"padding":"' + b"x" * 8192 + b'"}',
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert "oversized meta webhook body" in caplog.text.lower()


def test_an_oversized_body_is_refused_on_the_bytes_it_actually_sends(
    client, monkeypatch
):
    """Content-Length is the caller's claim, so it cannot be the real check. A
    chunked body declares no length at all and must still be capped."""
    monkeypatch.setattr(config, "WEBHOOK_MAX_BODY_BYTES", 2048)

    def chunks():
        for _ in range(8):
            yield b"x" * 1024

    response = client.post(
        "/webhook/meta",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_a_body_within_the_cap_is_still_accepted(client, accepted):
    """A limit nobody can pass is a limit that gets turned off."""
    response = _signed_post(client, _meta_payload(2))

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


# ----------------------------------------------------------------------
# Event count
# ----------------------------------------------------------------------


def test_a_meta_delivery_is_capped_and_the_drop_is_logged(monkeypatch, caplog):
    """One signed body could otherwise expand into hundreds of thousands of
    events. Truncating silently would read as "we handled all of it"."""
    monkeypatch.setattr(config, "WEBHOOK_MAX_EVENTS", 5)

    with caplog.at_level(logging.WARNING):
        events = parse_meta_events(_meta_payload(12))

    assert len(events) == 5
    assert [event["text"] for event in events] == [
        f"message {index}" for index in range(5)
    ]
    assert "processed 5, dropped 7" in caplog.text


def test_a_whatsapp_delivery_is_capped_and_the_drop_is_logged(monkeypatch, caplog):
    """The same shape of loop, and the same defect, on the WhatsApp path."""
    monkeypatch.setattr(config, "WEBHOOK_MAX_EVENTS", 3)

    with caplog.at_level(logging.WARNING):
        events = parse_whatsapp_events(_whatsapp_payload(10))

    assert len(events) == 3
    assert "processed 3, dropped 7" in caplog.text


def test_the_endpoint_processes_only_the_capped_events(
    client, accepted, monkeypatch, caplog
):
    """End to end: an over-cap delivery is acknowledged, exactly the cap is
    handed to processing, and the drop is on the record."""
    monkeypatch.setattr(config, "WEBHOOK_MAX_EVENTS", 4)

    with caplog.at_level(logging.WARNING):
        response = _signed_post(client, _meta_payload(40))
        _wait_for_processing()

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "accepted": 4}
    assert [len(batch) for batch in accepted] == [4]
    assert "dropped 36" in caplog.text


# ----------------------------------------------------------------------
# Handing the work off the request
# ----------------------------------------------------------------------


def test_the_response_does_not_wait_for_processing(client, monkeypatch):
    """The response used to wait for every event while holding a thread from
    Starlette's shared pool — the pool that serves the dashboard.

    Held on a gate rather than a sleep: the acknowledgement has to come back
    while the work is provably still unfinished, which is the property, and a
    clock reading is not.
    """
    gate = threading.Event()
    released: list[int] = []

    def blocked(events):
        assert gate.wait(10), "the work never ran"
        released.append(len(events))
        return []

    monkeypatch.setattr(meta_webhook, "_process_events", blocked)

    response = _signed_post(client, _meta_payload(3))

    assert response.status_code == 200
    assert released == []
    assert webhook_limits.in_flight_events() == 3

    # Accepted means accepted: the work still has to happen.
    gate.set()
    _wait_for_processing()
    assert released == [3]


def test_work_that_fails_is_logged_and_does_not_lose_the_next_delivery(
    client, monkeypatch, caplog
):
    """Nothing is watching a background task, so a failure inside one has to
    reach the log by itself."""
    calls: list[int] = []

    def boom(events):
        calls.append(len(events))
        raise RuntimeError("deliberate")

    monkeypatch.setattr(meta_webhook, "_process_events", boom)

    with caplog.at_level(logging.ERROR):
        assert _signed_post(client, _meta_payload(1)).status_code == 200
        _wait_for_processing()

    assert calls == [1]
    assert "deliberate" in caplog.text
    assert webhook_limits.in_flight_events() == 0


def test_a_full_backlog_is_refused_rather_than_accumulated(client, monkeypatch):
    """Accepting without bound just moves the flood from the threadpool into
    memory. A 503 is the one way to shed load without losing events: the
    provider redelivers what was refused."""
    monkeypatch.setattr(config, "WEBHOOK_MAX_EVENTS", 5)
    monkeypatch.setattr(webhook_limits, "IN_FLIGHT_DELIVERY_ALLOWANCE", 1)

    gate = threading.Event()
    holding: list[int] = []

    def hold(events):
        holding.append(len(events))
        assert gate.wait(10)
        return []

    monkeypatch.setattr(meta_webhook, "_process_events", hold)

    first = _signed_post(client, _meta_payload(5))
    second = _signed_post(client, _meta_payload(5))

    assert first.status_code == 200
    assert second.status_code == 503
    assert second.headers["Retry-After"] == "30"

    gate.set()
    _wait_for_processing()
    assert holding == [5]


# ----------------------------------------------------------------------
# The profile cache
# ----------------------------------------------------------------------


@pytest.fixture()
def profile_cache():
    from channels.meta import profile

    profile._PROFILE_CACHE.clear()
    yield profile
    profile._PROFILE_CACHE.clear()


def test_the_profile_cache_stops_growing(profile_cache, monkeypatch):
    """Every distinct sender id added an entry that only expired after twelve
    hours, so a flood of distinct ids was a memory leak with a name."""
    monkeypatch.setattr(config, "PROFILE_CACHE_MAX_ENTRIES", 10)

    for index in range(500):
        profile_cache._store_profile(f"1:{index}", {"customer_name": f"C{index}"})

    assert len(profile_cache._PROFILE_CACHE) == 10


def test_the_profile_cache_evicts_the_least_recently_used_entry(
    profile_cache, monkeypatch
):
    """A bound is only useful if it keeps the entries being asked for."""
    monkeypatch.setattr(config, "PROFILE_CACHE_MAX_ENTRIES", 3)

    for key in ("a", "b", "c"):
        profile_cache._store_profile(key, {"customer_name": key})

    # `a` is used again, so `b` is now the coldest entry.
    assert profile_cache._cached_profile("a") == {"customer_name": "a"}

    profile_cache._store_profile("d", {"customer_name": "d"})

    assert set(profile_cache._PROFILE_CACHE) == {"a", "c", "d"}


# ----------------------------------------------------------------------
# The pending reply batch
# ----------------------------------------------------------------------


@pytest.fixture()
def replies(platform, monkeypatch):
    """The pending reply service pointed at the test platform's databases."""
    import database.manager as manager_module

    import backend.services.pending_reply_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.pending_reply_service" in rebound

    from backend.services.pending_reply_service import pending_reply_service

    return pending_reply_service


def _stored_messages(platform, company_id: int) -> list[str]:
    with platform["manager"].tenant(company_id) as conn:
        row = conn.execute(
            "SELECT messages_json FROM pending_replies LIMIT 1"
        ).fetchone()

    return json.loads(row["messages_json"]) if row else []


def _deliver_after(platform, company_id: int) -> datetime:
    with platform["manager"].tenant(company_id) as conn:
        row = conn.execute(
            "SELECT deliver_after FROM pending_replies LIMIT 1"
        ).fetchone()

    return datetime.fromisoformat(row["deliver_after"])


def _backdate_creation(platform, company_id: int, seconds: int) -> None:
    """Age the batch, as a flood that has been running for a while would."""
    created = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()

    with platform["manager"].tenant(company_id) as conn:
        conn.execute("UPDATE pending_replies SET created_at = ?", (created,))
        conn.commit()


def test_a_batch_stops_accumulating_past_the_message_cap(
    replies, platform, alpha, monkeypatch, caplog
):
    """The stored JSON grew by one message per arrival with nothing stopping
    it, and every arrival rewrote the whole row."""
    monkeypatch.setattr(config, "PENDING_REPLY_MAX_MESSAGES", 4)

    with caplog.at_level(logging.WARNING):
        for index in range(30):
            result = replies.enqueue(
                company_id=alpha["id"],
                channel="messenger",
                external_user_id="flooder",
                message=f"message {index}",
                delay_seconds=5,
            )

    assert result["message_count"] == 4
    assert result["dropped"] is True
    assert _stored_messages(platform, alpha["id"]) == [
        f"message {index}" for index in range(4)
    ]
    assert "is at its limit of 4 messages" in caplog.text


def test_a_batch_deferred_past_the_ceiling_is_delivered_not_deferred_again(
    replies, platform, alpha, monkeypatch, caplog
):
    """The whole stall in one test: every new message pushed the delivery time
    out again, so a customer being flooded never got answered at all."""
    monkeypatch.setattr(config, "PENDING_REPLY_MAX_DEFERRAL_SECONDS", 60)

    replies.enqueue(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="flooder",
        message="first",
        delay_seconds=5,
    )

    _backdate_creation(platform, alpha["id"], seconds=600)

    with caplog.at_level(logging.WARNING):
        result = replies.enqueue(
            company_id=alpha["id"],
            channel="messenger",
            external_user_id="flooder",
            message="and another",
            delay_seconds=60,
        )

    assert result["deferral_capped"] is True
    assert _deliver_after(platform, alpha["id"]) <= datetime.now(timezone.utc)
    assert "has waited its maximum of 60s" in caplog.text

    # And it is genuinely due: the sweep takes it on its next pass.
    claimed = replies.claim_due(alpha["id"])
    assert [batch["external_user_id"] for batch in claimed] == ["flooder"]
    assert claimed[0]["messages"] == ["first", "and another"]


def test_a_new_message_still_defers_a_young_batch(
    replies, platform, alpha, monkeypatch
):
    """The ceiling must not cost a customer who is simply still typing the few
    seconds of collection the batching exists for."""
    monkeypatch.setattr(config, "PENDING_REPLY_MAX_DEFERRAL_SECONDS", 300)

    replies.enqueue(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="typing",
        message="first",
        delay_seconds=5,
    )
    first_due = _deliver_after(platform, alpha["id"])

    result = replies.enqueue(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="typing",
        message="second",
        delay_seconds=30,
    )

    assert result["deferral_capped"] is False
    assert result["message_count"] == 2
    assert _deliver_after(platform, alpha["id"]) > first_due
    assert replies.claim_due(alpha["id"]) == []
