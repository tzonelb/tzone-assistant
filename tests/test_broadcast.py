"""Broadcast campaigns: one message, sent once, to many contacts.

Three properties are worth a test here, and only the first is about the
feature working at all.

The first is that a campaign is company-owned. It lives in that company's own
encrypted database, so one company's campaign, its recipient list and its
report can never be read, sent or deleted by another. The shared-table failure
this normally guards against — a forgotten ``WHERE company_id = ?`` — is not
reachable here because the row is in a file the other company is never handed
a connection to; the tests below prove the isolation survives all the way out
through the API anyway, which is where it would be lost.

The second is that sending is exactly once. A campaign is irreversible in a way
almost nothing else on this platform is: a double-clicked "Send now" is a
second message in every customer's phone, and there is no way to take it back.
So the send is guarded by an atomic claim on the row rather than by reading the
status and then writing it, and resuming an interrupted send skips whoever
already received it.

The third is that a targeting dimension this platform cannot resolve is
refused rather than widened. The design branch this feature is ported from
targets segments, lifecycle stages and contact tags; this platform's contacts
have none of those. Answering such a broadcast with "everybody on the channel"
would be the one wrong answer that looks like it worked, so it is a 400.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "BroadcastPass123"


@pytest.fixture()
def service(platform, monkeypatch):
    """Point every service and router at this test's encrypted databases."""
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.broadcasts  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.broadcast_service  # noqa: F401
    import backend.services.customer_service  # noqa: F401
    import backend.services.message_service  # noqa: F401
    import backend.services.platform_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    # The assertion is the point of the fixture: without it a rebinding that
    # silently missed the module would run every test below against the
    # developer's real database and prove nothing.
    assert "backend.services.broadcast_service" in rebound
    assert "backend.services.auth_service" in rebound

    # Both gates cache their answer for thirty seconds, keyed by company id,
    # in a module-level singleton that outlives this test's databases. A test
    # below switches the Broadcast module off for company 1; without this the
    # next test's company 1 -- a brand new file -- would still read "off".
    from backend.services.module_gate import module_gate
    from backend.services.subscription_gate import subscription_gate

    module_gate.invalidate()
    subscription_gate.invalidate()

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def sent(monkeypatch):
    """Stand in for the channel, and record what it was asked to send.

    Every send in this file goes through `channels/sender.py`'s `send_text`,
    which is where the company's own credentials are resolved and a real HTTP
    request is made. Replacing it here is the boundary: everything above it —
    the lock, the resume, the recipient rows, the conversation record — is
    this platform's code and is what the tests are about.
    """
    import backend.services.broadcast_service as module

    calls: list[dict] = []

    def fake_send_text(*, channel, recipient_id, company_id, text, **_kwargs):
        calls.append(
            {
                "channel": channel,
                "recipient_id": recipient_id,
                "company_id": company_id,
                "text": text,
            }
        )
        return {"ok": True, "response": {"message_id": f"pmid-{len(calls)}"}}

    monkeypatch.setattr(module, "send_text", fake_send_text)

    return calls


@pytest.fixture()
def refused(monkeypatch):
    """A channel that rejects everything, with a reason worth showing."""
    import backend.services.broadcast_service as module

    def fake_send_text(**_kwargs):
        return {"ok": False, "error": "The number is not on WhatsApp."}

    monkeypatch.setattr(module, "send_text", fake_send_text)


@pytest.fixture()
def client(service):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, broadcasts
    from backend.services.module_access import require_module

    app = FastAPI()
    app.include_router(auth.router)
    # Mounted exactly as `main.py` mounts it. The module switch lives in the
    # registration rather than in the handlers, so a test that mounted the
    # router bare would be testing something the application does not do.
    app.include_router(
        broadcasts.router, dependencies=[Depends(require_module("broadcast"))]
    )

    return TestClient(app, raise_server_exceptions=False)


def _token(client, service, company, email, role="owner"):
    user_id = service.create_user(email, PASSWORD, "Person")
    service.assign_user_to_company(user_id, company["id"], role)

    response = client.post(
        "/api/auth/login",
        json={"company": company["name"], "email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text

    return response.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def owner(client, service, alpha):
    return _token(client, service, alpha, "owner@alpha.example.com")


@pytest.fixture()
def beta_owner(client, service, beta):
    return _token(client, service, beta, "owner@beta.example.com")


@pytest.fixture()
def manager(client, service, alpha):
    """Holds `channels.view` and not `channels.manage` — see DEFAULT_ROLES."""
    return _token(client, service, alpha, "manager@alpha.example.com", "manager")


def _create(client, token, **overrides):
    body = {
        "name": "July sale",
        "message_text": "Half price this week.",
        "channel": "whatsapp",
        "numbers": ["+1 555 0100", "+1-555-0101"],
    }
    body.update(overrides)

    return client.post("/api/broadcasts", headers=_headers(token), json=body)


def _contact_on(platform, company, channel, external_user_id):
    """A contact this company already knows on a channel."""
    from backend.services.customer_service import customer_service

    return customer_service.upsert_from_channel(
        company_id=company["id"],
        channel=channel,
        external_user_id=external_user_id,
    )


# --------------------------------------------------------------- the basics


def test_a_draft_is_created_and_read_back(client, owner):
    created = _create(client, owner)
    assert created.status_code == 200, created.text

    draft = created.json()
    assert draft["status"] == "draft"
    assert draft["channel"] == "whatsapp"
    # Two numbers, normalized: the formatting characters are stripped and the
    # digits are what is stored.
    assert draft["recipient_count"] == 2
    assert draft["sent_count"] == 0

    listed = client.get("/api/broadcasts", headers=_headers(owner))
    assert listed.status_code == 200, listed.text
    assert [row["name"] for row in listed.json()["items"]] == ["July sale"]

    single = client.get(
        f"/api/broadcasts/{draft['id']}", headers=_headers(owner)
    )
    assert single.status_code == 200, single.text
    assert single.json()["id"] == draft["id"]


def test_a_number_list_is_normalized_and_deduplicated(client, owner):
    """Two spellings of the same number are one recipient, not two messages."""
    created = _create(
        client, owner, numbers=["+1 555 0100", "+1-555-0100", "(555) 0100"]
    )
    assert created.status_code == 200, created.text

    # "+15550100" twice, then "5550100" — the third has no country code and is
    # deliberately NOT treated as the same person; the normalization strips
    # formatting, it does not parse phone numbers.
    assert created.json()["recipient_count"] == 2


def test_an_unknown_broadcast_is_a_404_not_a_crash(client, owner):
    for path in ("", "/report", "/recipient-count"):
        response = client.get(
            f"/api/broadcasts/9999{path}", headers=_headers(owner)
        )
        assert response.status_code == 404, response.text


def test_the_recipient_count_is_recomputed_rather_than_remembered(
    client, owner, platform, alpha
):
    """`recipient_count` is a snapshot from creation time. A draft targeting a
    whole channel goes stale the moment a contact arrives, and the confirm
    dialog must not promise a number the send will not deliver to."""
    _contact_on(platform, alpha, "telegram", "tg-1")

    created = _create(client, owner, channel="telegram", numbers=None)
    assert created.status_code == 200, created.text
    assert created.json()["recipient_count"] == 1

    _contact_on(platform, alpha, "telegram", "tg-2")

    recount = client.get(
        f"/api/broadcasts/{created.json()['id']}/recipient-count",
        headers=_headers(owner),
    )
    assert recount.status_code == 200, recount.text
    assert recount.json()["recipient_count"] == 2


# --------------------------------------------------------------- sending


def test_sending_reaches_every_recipient_and_reports_it(client, owner, sent):
    draft = _create(client, owner).json()

    response = client.post(
        f"/api/broadcasts/{draft['id']}/send", headers=_headers(owner)
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["status"] == "sent"
    assert body["sent_count"] == 2
    assert body["failed_count"] == 0
    assert body["sent_at"]

    assert sorted(call["recipient_id"] for call in sent) == [
        "+15550100",
        "+15550101",
    ]
    assert {call["text"] for call in sent} == {"Half price this week."}

    report = client.get(
        f"/api/broadcasts/{draft['id']}/report", headers=_headers(owner)
    )
    assert report.status_code == 200, report.text

    detail = report.json()
    assert detail["totals"]["recipients"] == 2
    assert detail["totals"]["sent"] == 2
    assert detail["totals"]["failed"] == 0
    assert [row["send_status"] for row in detail["recipients"]] == ["sent", "sent"]
    # Nothing on this platform records a provider's delivery or read event for
    # an outbound message, so the report says so rather than reporting zeroes
    # that would read as "nobody opened it".
    assert detail["channel_tracking_supported"] is False
    assert all(row["delivery_status"] is None for row in detail["recipients"])


def test_a_refused_message_is_recorded_with_its_reason(client, owner, refused):
    """A campaign that reached nobody must say why, per recipient. Counting it
    as sent would hide a broken channel behind a green number."""
    draft = _create(client, owner).json()

    response = client.post(
        f"/api/broadcasts/{draft['id']}/send", headers=_headers(owner)
    )
    assert response.status_code == 200, response.text
    assert response.json()["sent_count"] == 0
    assert response.json()["failed_count"] == 2

    report = client.get(
        f"/api/broadcasts/{draft['id']}/report", headers=_headers(owner)
    ).json()

    assert [row["send_status"] for row in report["recipients"]] == [
        "failed",
        "failed",
    ]
    assert all(
        "not on WhatsApp" in (row["error"] or "") for row in report["recipients"]
    )


def test_a_sent_campaign_appears_in_the_customers_conversation(
    client, owner, sent, platform, alpha
):
    """The rule this platform already holds, applied to a campaign: every
    outbound message a customer receives is on their conversation. Without it
    an employee opening the thread sees the customer answering something the
    inbox never shows being said."""
    draft = _create(client, owner, numbers=["+15550100"]).json()

    client.post(f"/api/broadcasts/{draft['id']}/send", headers=_headers(owner))

    with platform["manager"].tenant(alpha["id"]) as conn:
        rows = conn.execute(
            """
            SELECT direction, body, source, provider_message_id
            FROM messages WHERE external_user_id = ?
            """,
            ("+15550100",),
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["direction"] == "out"
    assert rows[0]["body"] == "Half price this week."
    assert rows[0]["source"] == "broadcast"
    assert rows[0]["provider_message_id"] == "pmid-1"


def test_a_campaign_cannot_be_sent_twice(client, owner, sent):
    """The defect this prevents is irreversible: a second send is a second
    message in every customer's phone, and there is no unsend."""
    draft = _create(client, owner).json()

    first = client.post(
        f"/api/broadcasts/{draft['id']}/send", headers=_headers(owner)
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/broadcasts/{draft['id']}/send", headers=_headers(owner)
    )
    assert second.status_code == 400, second.text
    assert "already been sent" in second.json()["detail"]

    # And nobody was messaged a second time.
    assert len(sent) == 2


def test_resuming_an_interrupted_send_skips_whoever_already_received_it(
    client, owner, sent, platform, alpha
):
    """A send killed partway through — a proxy timeout on a long list, a
    restart — leaves the row in `sending`, and nothing else would ever move it
    on. Resuming has to be possible, and it must not re-send."""
    draft = _create(
        client, owner, numbers=["+15550100", "+15550101", "+15550102"]
    ).json()

    # The state an interrupted send leaves behind: status `sending`, the lock
    # released, and a recipient row for the one person who was reached.
    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.execute(
            "UPDATE broadcasts SET status = 'sending' WHERE id = ?",
            (draft["id"],),
        )
        conn.execute(
            """
            INSERT INTO broadcast_recipients (
                company_id, broadcast_id, customer_id, channel,
                external_user_id, send_status, created_at
            )
            VALUES (?, ?, NULL, 'whatsapp', '+15550100', 'sent', '2026-01-01T00:00:00+00:00')
            """,
            (alpha["id"], draft["id"]),
        )
        conn.commit()

    response = client.post(
        f"/api/broadcasts/{draft['id']}/send", headers=_headers(owner)
    )
    assert response.status_code == 200, response.text

    assert sorted(call["recipient_id"] for call in sent) == [
        "+15550101",
        "+15550102",
    ]
    # The totals cover the whole campaign, not just the resumed batch.
    assert response.json()["sent_count"] == 3
    assert response.json()["recipient_count"] == 3


def test_a_send_already_in_flight_is_refused_rather_than_duplicated(
    client, owner, platform, alpha
):
    """Two overlapping resumes both match `status = 'sending'`, so the status
    alone is not a lock. `send_lock_acquired_at` is."""
    from database.manager import utc_now_iso

    draft = _create(client, owner).json()

    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.execute(
            """
            UPDATE broadcasts SET status = 'sending', send_lock_acquired_at = ?
            WHERE id = ?
            """,
            (utc_now_iso(), draft["id"]),
        )
        conn.commit()

    response = client.post(
        f"/api/broadcasts/{draft['id']}/send", headers=_headers(owner)
    )
    assert response.status_code == 400, response.text
    assert "already being sent" in response.json()["detail"]


# --------------------------------------------------------------- refusals


def test_deleting_is_only_for_drafts(client, owner, sent):
    draft = _create(client, owner).json()

    client.post(f"/api/broadcasts/{draft['id']}/send", headers=_headers(owner))

    refused_delete = client.delete(
        f"/api/broadcasts/{draft['id']}", headers=_headers(owner)
    )
    assert refused_delete.status_code == 400, refused_delete.text

    still_a_draft = _create(client, owner, name="Second").json()
    deleted = client.delete(
        f"/api/broadcasts/{still_a_draft['id']}", headers=_headers(owner)
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"deleted": True}

    assert [row["name"] for row in client.get(
        "/api/broadcasts", headers=_headers(owner)
    ).json()["items"]] == ["July sale"]


@pytest.mark.parametrize(
    "targeting",
    [
        {"segment_id": 1},
        {"lifecycle_stage": "lead"},
        {"tag": "vip"},
    ],
)
def test_a_targeting_dimension_this_platform_lacks_is_refused(
    client, owner, targeting
):
    """The design branch targets segments, lifecycle stages and contact tags.
    This platform's contacts carry none of them, so there is nothing to
    resolve such a campaign against — and resolving it to "everybody on the
    channel" would send a message meant for one group to all of them."""
    response = _create(client, owner, numbers=None, **targeting)

    assert response.status_code == 400, response.text
    assert "segment" in response.json()["detail"].lower()


def test_a_number_list_is_whatsapp_only(client, owner):
    response = _create(client, owner, channel="telegram")

    assert response.status_code == 400, response.text
    assert "WhatsApp" in response.json()["detail"]


def test_an_unsupported_channel_is_refused(client, owner):
    response = _create(client, owner, channel="carrier-pigeon", numbers=None)

    assert response.status_code == 400, response.text


def test_an_attachment_must_be_a_file_this_workspace_stored(client, owner):
    """Otherwise the platform is an open relay: it would fetch any address an
    employee names and deliver it from the company's own channel."""
    response = _create(
        client,
        owner,
        media_url="https://evil.example.com/payload.png",
        media_type="image",
    )

    assert response.status_code == 400, response.text
    assert "uploaded to this workspace" in response.json()["detail"]


def test_a_campaign_needs_a_name_and_a_message(client, owner):
    assert _create(client, owner, name="   ").status_code == 400
    assert _create(client, owner, message_text="  ").status_code == 400


# --------------------------------------------------------------- permissions


def test_reading_takes_channels_view_and_writing_takes_channels_manage(
    client, manager, owner
):
    """A manager may see the campaigns; only somebody who may operate the
    company's channels may create or send one. Without the split, a screen
    offers a view-only employee fully clickable Send and Delete controls that
    always 403 — the fake-button pattern this platform's audits remove."""
    draft = _create(client, owner).json()

    assert client.get(
        "/api/broadcasts", headers=_headers(manager)
    ).status_code == 200
    assert client.get(
        f"/api/broadcasts/{draft['id']}/report", headers=_headers(manager)
    ).status_code == 200

    assert _create(client, manager, name="Not mine").status_code == 403
    assert client.post(
        f"/api/broadcasts/{draft['id']}/send", headers=_headers(manager)
    ).status_code == 403
    assert client.delete(
        f"/api/broadcasts/{draft['id']}", headers=_headers(manager)
    ).status_code == 403


def test_an_unauthenticated_caller_reaches_nothing(client):
    assert client.get("/api/broadcasts").status_code == 401


# --------------------------------------------------------------- the module gate


def test_switching_the_module_off_closes_its_api(client, owner, alpha):
    """The operator's switch has to mean the same thing to the server as it
    does to the sidebar, or they believe they have turned something off that
    anybody can still call."""
    from backend.services.platform_service import platform_service

    assert client.get(
        "/api/broadcasts", headers=_headers(owner)
    ).status_code == 200

    platform_service.update_platform_config(
        alpha["id"], modules={"broadcast": False}
    )

    refused_read = client.get("/api/broadcasts", headers=_headers(owner))
    assert refused_read.status_code == 403, refused_read.text
    assert _create(client, owner).status_code == 403


def test_the_module_is_in_the_catalogue_and_gates_its_router():
    """`broadcast` has to be a key the operator console actually offers, and
    `main.py` has to gate the router on that same key. The two live in
    different files and nothing but this compares them."""
    import re
    from pathlib import Path

    from backend.services.platform_service import PLATFORM_MODULES

    assert "broadcast" in PLATFORM_MODULES

    source = (Path(__file__).resolve().parent.parent / "main.py").read_text()
    gated = set(re.findall(r'_module(?:_unpaid_too)?\("([a-z_]+)"\)', source))

    assert "broadcast" in gated


# --------------------------------------------------------------- isolation


def test_one_company_cannot_see_or_send_anothers_campaign(
    client, owner, beta_owner, sent
):
    """The property the per-company encrypted file exists to guarantee, proved
    through the API rather than in the service. Alpha's campaign is not merely
    forbidden to Beta — it is not in the file Beta is handed."""
    alpha_draft = _create(client, owner, name="Alpha only").json()

    listed = client.get("/api/broadcasts", headers=_headers(beta_owner))
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []

    for method, path in (
        ("get", f"/api/broadcasts/{alpha_draft['id']}"),
        ("get", f"/api/broadcasts/{alpha_draft['id']}/report"),
        ("get", f"/api/broadcasts/{alpha_draft['id']}/recipient-count"),
        ("post", f"/api/broadcasts/{alpha_draft['id']}/send"),
        ("delete", f"/api/broadcasts/{alpha_draft['id']}"),
    ):
        response = getattr(client, method)(path, headers=_headers(beta_owner))
        assert response.status_code == 404, f"{method} {path}: {response.text}"

    # Nothing was sent on Beta's behalf, and Alpha's campaign is untouched.
    assert sent == []
    assert client.get(
        f"/api/broadcasts/{alpha_draft['id']}", headers=_headers(owner)
    ).json()["status"] == "draft"


def test_a_campaign_only_reaches_its_own_companys_contacts(
    client, owner, beta_owner, sent, platform, alpha, beta
):
    """The subtler half. Even with the same channel and the same external id,
    a campaign resolves recipients out of its own company's database."""
    _contact_on(platform, alpha, "telegram", "shared-id")
    _contact_on(platform, alpha, "telegram", "alpha-only")
    _contact_on(platform, beta, "telegram", "shared-id")
    _contact_on(platform, beta, "telegram", "beta-only")

    draft = _create(client, owner, channel="telegram", numbers=None).json()
    assert draft["recipient_count"] == 2

    response = client.post(
        f"/api/broadcasts/{draft['id']}/send", headers=_headers(owner)
    )
    assert response.status_code == 200, response.text

    assert sorted(call["recipient_id"] for call in sent) == [
        "alpha-only",
        "shared-id",
    ]
    assert {call["company_id"] for call in sent} == {alpha["id"]}

    # And Beta's own contact list is untouched by a campaign it never ran.
    with platform["manager"].tenant(beta["id"]) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS total FROM broadcast_recipients"
        ).fetchone()["total"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS total FROM messages"
        ).fetchone()["total"] == 0
