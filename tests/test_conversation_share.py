"""Share links and the transcript they point to: real tokens, real expiry,
real revocation, and never one company's conversation behind another's link.

The token lives in the control-plane database (like a password-reset token),
not the tenant one, because the public endpoint that resolves it is reached
before any company is known -- this is the property under test in the
cross-company cases.
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager


def _wire(platform, monkeypatch):
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)
        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)
    return test_manager


@pytest.fixture()
def bound(platform, monkeypatch):
    return _wire(platform, monkeypatch)


def _seed_conversation(alpha, channel="messenger", user_id="share-cust", text="hello there"):
    from channels.inbound import process_inbound_event

    process_inbound_event(
        company_id=alpha["id"],
        event={
            "channel": channel,
            "user_id": user_id,
            "text": text,
            "message_id": f"mid-{user_id}",
        },
    )


# ------------------------------------------------------------------ the token


def test_a_created_link_resolves_to_its_conversation(bound, alpha, monkeypatch):
    import channels.inbound as inbound
    from backend.services.conversation_share_service import create_link, resolve

    monkeypatch.setattr(inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True})
    _seed_conversation(alpha)

    link = create_link(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="share-cust",
        scope="chat",
        created_by_user_id=1,
    )

    resolved = resolve(link["token"])
    assert resolved == {
        "company_id": alpha["id"],
        "channel": "messenger",
        "external_user_id": "share-cust",
        "scope": "chat",
    }


def test_an_unknown_token_does_not_resolve(bound, alpha):
    from backend.services.conversation_share_service import resolve

    assert resolve("not-a-real-token") is None


def test_a_revoked_link_no_longer_resolves(bound, alpha, monkeypatch):
    import channels.inbound as inbound
    from backend.services.conversation_share_service import (
        create_link,
        list_links,
        resolve,
        revoke,
    )

    monkeypatch.setattr(inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True})
    _seed_conversation(alpha)

    link = create_link(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="share-cust",
        created_by_user_id=1,
    )
    assert resolve(link["token"]) is not None

    [row] = list_links(
        company_id=alpha["id"], channel="messenger", external_user_id="share-cust"
    )
    assert revoke(company_id=alpha["id"], link_id=row["id"]) is True

    assert resolve(link["token"]) is None


def test_a_link_cannot_be_revoked_by_another_company(bound, alpha, beta, monkeypatch):
    import channels.inbound as inbound
    from backend.services.conversation_share_service import create_link, list_links, revoke

    monkeypatch.setattr(inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True})
    _seed_conversation(alpha)

    create_link(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="share-cust",
        created_by_user_id=1,
    )
    [row] = list_links(
        company_id=alpha["id"], channel="messenger", external_user_id="share-cust"
    )

    assert revoke(company_id=beta["id"], link_id=row["id"]) is False


def test_listed_links_never_carry_the_token(bound, alpha, monkeypatch):
    import channels.inbound as inbound
    from backend.services.conversation_share_service import create_link, list_links

    monkeypatch.setattr(inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True})
    _seed_conversation(alpha)

    create_link(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="share-cust",
        created_by_user_id=1,
    )

    [row] = list_links(
        company_id=alpha["id"], channel="messenger", external_user_id="share-cust"
    )
    assert "token" not in row and "token_hash" not in row


# ------------------------------------------------------------- the transcript


def test_the_transcript_contains_the_real_messages(bound, alpha, monkeypatch):
    import channels.inbound as inbound
    from backend.services.transcript_service import build_transcript_text

    monkeypatch.setattr(inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True})
    _seed_conversation(alpha, text="a very specific customer question")

    text = build_transcript_text(
        company_id=alpha["id"], channel="messenger", external_user_id="share-cust"
    )
    assert "a very specific customer question" in text


def test_a_missing_conversation_returns_none(bound, alpha):
    from backend.services.transcript_service import build_transcript_text

    assert build_transcript_text(
        company_id=alpha["id"], channel="messenger", external_user_id="nobody-ever-messaged"
    ) is None


# -------------------------------------------------- the public HTML endpoint


def _share_app():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import conversation_share

    app = FastAPI()
    app.include_router(conversation_share.router)
    return TestClient(app)


def test_the_public_page_shows_the_transcript(bound, alpha, monkeypatch):
    import channels.inbound as inbound
    from backend.services.conversation_share_service import create_link

    monkeypatch.setattr(inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True})
    _seed_conversation(alpha, text="the exact words a customer sent")

    link = create_link(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="share-cust",
        created_by_user_id=1,
    )

    response = _share_app().get(f"/api/share/conversation/{link['token']}")
    assert response.status_code == 200
    assert "the exact words a customer sent" in response.text


def test_the_public_page_refuses_a_bad_token(bound, alpha):
    response = _share_app().get("/api/share/conversation/forged-or-expired")
    assert response.status_code == 404


def test_head_agrees_with_get_on_a_real_link(bound, alpha, monkeypatch):
    """A plain `@router.get(...)` here would not answer HEAD -- see
    media_uploads.py's read_media, where the identical gap was reproduced
    live: the request falls through every route to the SPA catch-all and
    comes back a bare 404. A link pasted into a chat app is exactly the kind
    of URL an unfurl bot HEAD-checks before fetching it."""
    import channels.inbound as inbound
    from backend.services.conversation_share_service import create_link

    monkeypatch.setattr(inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True})
    _seed_conversation(alpha, text="the exact words a customer sent")

    link = create_link(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="share-cust",
        created_by_user_id=1,
    )

    client = _share_app()
    url = f"/api/share/conversation/{link['token']}"

    get_response = client.get(url)
    head_response = client.head(url)

    assert get_response.status_code == 200
    assert head_response.status_code == 200, (
        "HEAD returned "
        f"{head_response.status_code} for a link GET can see at the same URL"
    )


def test_head_on_a_bad_token_is_a_clean_404(bound, alpha):
    response = _share_app().head("/api/share/conversation/forged-or-expired")
    assert response.status_code == 404


def test_one_companys_link_never_shows_anothers_transcript(bound, alpha, beta, monkeypatch):
    """The isolation that matters most: a link minted for alpha's conversation
    must never resolve into beta's tenant database even if ids collide."""
    import channels.inbound as inbound
    from backend.services.conversation_share_service import create_link, resolve

    monkeypatch.setattr(inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True})
    _seed_conversation(alpha, user_id="shared-id", text="alpha private message")
    _seed_conversation(beta, user_id="shared-id", text="beta private message")

    link = create_link(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="shared-id",
        created_by_user_id=1,
    )

    resolved = resolve(link["token"])
    assert resolved["company_id"] == alpha["id"]

    response = _share_app().get(f"/api/share/conversation/{link['token']}")
    assert "alpha private message" in response.text
    assert "beta private message" not in response.text
