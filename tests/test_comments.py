"""Tests for the post-comment queue.

Replaces a screen that rendered two invented comments from a hardcoded array.
These comments are public: an unanswered one sits under the company's own
advertising, so losing, duplicating or misrouting one is visible to customers.
"""

from __future__ import annotations

import pytest

from channels.meta.parser import parse_meta_comment_events


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the comment service at the test platform's databases."""
    import sys

    import backend.services.comment_service  # noqa: F401
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.comment_service" in rebound

    from backend.services.comment_service import comment_service

    return comment_service


def _feed_payload(
    *,
    comment_id="COMMENT_1",
    page_id="PAGE_1",
    message="Is this still available?",
    author_id="CUSTOMER_1",
    item="comment",
    verb="add",
):
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "changes": [
                    {
                        "field": "feed",
                        "value": {
                            "item": item,
                            "verb": verb,
                            "comment_id": comment_id,
                            "post_id": "POST_1",
                            "from": {"id": author_id, "name": "A Customer"},
                            "message": message,
                            "permalink_url": "https://facebook.com/x",
                        },
                    }
                ],
            }
        ],
    }


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------


def test_a_new_page_comment_is_parsed():
    """The base case. Comments arrive on the `feed` field, which shares nothing
    with the message payload shape."""
    events = parse_meta_comment_events(_feed_payload())

    assert len(events) == 1
    assert events[0]["comment_id"] == "COMMENT_1"
    assert events[0]["message"] == "Is this still available?"
    assert events[0]["page_id"] == "PAGE_1"


def test_non_comment_feed_activity_is_ignored():
    """The page `feed` field also carries likes, shares and new posts. Treating
    those as comments would fill the queue with items nobody can reply to."""
    assert parse_meta_comment_events(_feed_payload(item="like")) == []
    assert parse_meta_comment_events(_feed_payload(item="status")) == []


def test_edits_and_deletes_do_not_create_new_comments():
    """An edit arrives on the same field with a different verb. Treating it as
    new would re-open a comment the team already answered."""
    assert parse_meta_comment_events(_feed_payload(verb="edited")) == []
    assert parse_meta_comment_events(_feed_payload(verb="remove")) == []


def test_our_own_reply_is_not_treated_as_a_customer_comment():
    """A reply we publish comes back on the same webhook. Recording it would
    put the team in an endless loop of answering itself."""
    events = parse_meta_comment_events(
        _feed_payload(author_id="PAGE_1", page_id="PAGE_1")
    )

    assert events == []


def test_instagram_comments_are_labelled_instagram():
    """Mislabelling the channel sends the reply to the wrong Graph endpoint."""
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": "IG_1",
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": "IG_COMMENT_1",
                            "text": "How much?",
                            "from": {"id": "CUST", "username": "someone"},
                            "media": {"id": "MEDIA_1"},
                        },
                    }
                ],
            }
        ],
    }

    events = parse_meta_comment_events(payload)

    assert len(events) == 1
    assert events[0]["channel"] == "instagram"
    assert events[0]["comment_id"] == "IG_COMMENT_1"


def test_empty_comment_is_skipped():
    """A sticker or photo-only comment has no text to answer."""
    assert parse_meta_comment_events(_feed_payload(message="")) == []


# ----------------------------------------------------------------------
# Storage and isolation
# ----------------------------------------------------------------------


def test_comment_is_stored_open(service, alpha):
    """A new comment must land in the working queue, not merely be recorded."""
    result = service.record_incoming(
        company_id=alpha["id"],
        channel="messenger",
        provider_comment_id="C1",
        message="Is this available?",
    )

    assert result["duplicate"] is False

    listing = service.list_comments(company_id=alpha["id"])
    assert listing["total"] == 1
    assert listing["items"][0]["status"] == "open"
    assert listing["status_counts"]["open"] == 1


def test_redelivered_comment_is_not_duplicated(service, alpha):
    """Meta re-delivers webhooks. A duplicate would show the team the same
    unanswered public comment twice."""
    service.record_incoming(
        company_id=alpha["id"],
        channel="messenger",
        provider_comment_id="C1",
        message="Hello",
    )
    second = service.record_incoming(
        company_id=alpha["id"],
        channel="messenger",
        provider_comment_id="C1",
        message="Hello",
    )

    assert second["duplicate"] is True
    assert service.list_comments(company_id=alpha["id"])["total"] == 1


def test_one_company_cannot_see_another_companys_comments(service, alpha, beta):
    """Comments name customers and quote them publicly; they are company data
    like any other."""
    service.record_incoming(
        company_id=alpha["id"],
        channel="messenger",
        provider_comment_id="C_ALPHA",
        message="alpha comment",
    )

    assert service.list_comments(company_id=beta["id"])["total"] == 0
    assert service.list_comments(company_id=alpha["id"])["total"] == 1


def test_the_same_provider_id_can_exist_for_two_companies(service, alpha, beta):
    """Uniqueness is per company. A global constraint would let one company's
    comment block another's from ever being stored."""
    first = service.record_incoming(
        company_id=alpha["id"],
        channel="messenger",
        provider_comment_id="SHARED_ID",
        message="one",
    )
    second = service.record_incoming(
        company_id=beta["id"],
        channel="messenger",
        provider_comment_id="SHARED_ID",
        message="two",
    )

    assert first["duplicate"] is False
    assert second["duplicate"] is False


# ----------------------------------------------------------------------
# Replying
# ----------------------------------------------------------------------


def test_a_published_reply_closes_the_comment(service, alpha):
    """Answering is what takes it out of the queue."""
    stored = service.record_incoming(
        company_id=alpha["id"],
        channel="messenger",
        provider_comment_id="C1",
        message="Price?",
    )

    service.record_reply(
        company_id=alpha["id"],
        comment_id=stored["id"],
        body="Sent you a message.",
        author_user_id=5,
        send_status="sent",
    )

    comment = service.get_comment(company_id=alpha["id"], comment_id=stored["id"])

    assert comment["status"] == "answered"
    assert comment["replied_at"]
    assert len(comment["replies"]) == 1


def test_a_failed_reply_leaves_the_comment_open(service, alpha):
    """The comment is still public and still unanswered. Closing it on a failed
    publish would hide it from the team while the customer sees silence."""
    stored = service.record_incoming(
        company_id=alpha["id"],
        channel="messenger",
        provider_comment_id="C1",
        message="Price?",
    )

    service.record_reply(
        company_id=alpha["id"],
        comment_id=stored["id"],
        body="Attempted answer",
        author_user_id=5,
        send_status="failed",
        error="token expired",
    )

    comment = service.get_comment(company_id=alpha["id"], comment_id=stored["id"])

    assert comment["status"] == "open"
    assert comment["replied_at"] is None
    # The employee's text is kept so it is not retyped after a token refresh.
    assert comment["replies"][0]["body"] == "Attempted answer"
    assert comment["replies"][0]["send_status"] == "failed"


def test_ignoring_a_comment_removes_it_from_the_open_queue(service, alpha):
    """Spam should leave the queue without a reply being published."""
    stored = service.record_incoming(
        company_id=alpha["id"],
        channel="messenger",
        provider_comment_id="C1",
        message="spam",
    )

    assert service.set_status(
        company_id=alpha["id"], comment_id=stored["id"], status="ignored"
    )
    assert service.open_count(alpha["id"]) == 0


def test_status_must_be_a_known_value(service, alpha):
    """A typo must fail loudly rather than write a status nothing filters on."""
    stored = service.record_incoming(
        company_id=alpha["id"],
        channel="messenger",
        provider_comment_id="C1",
        message="x",
    )

    with pytest.raises(ValueError):
        service.set_status(
            company_id=alpha["id"], comment_id=stored["id"], status="dealt-with"
        )
