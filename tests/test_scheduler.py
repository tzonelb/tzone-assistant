"""Tests for scheduled publishing.

A scheduled post goes out when nobody is watching. Publishing the wrong thing,
publishing twice, or silently not publishing at all are all visible to the
public, so each has a test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the scheduler service at the test platform's databases."""
    import sys

    import backend.services.scheduler_service  # noqa: F401
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.scheduler_service" in rebound

    from backend.services.scheduler_service import scheduler_service

    return scheduler_service


def _at(minutes_from_now: float) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
    ).isoformat()


def _create(service, company, *, when=-5, body="Hello"):
    return service.create_post(
        company_id=company["id"],
        channel="messenger",
        body=body,
        scheduled_for=_at(when),
        created_by_user_id=1,
    )


# ----------------------------------------------------------------------
# Approval gate
# ----------------------------------------------------------------------


def test_a_new_post_starts_as_a_draft(service, alpha):
    """A half-written post must never go out because a clock ticked over."""
    post = _create(service, alpha)

    assert post["status"] == "draft"


def test_an_unapproved_post_is_never_published(service, alpha):
    """The approval gate is the whole safety mechanism: a due draft must be
    ignored by the publisher, not picked up."""
    _create(service, alpha, when=-60)

    assert service.claim_due(alpha["id"]) == []


def test_an_approved_and_due_post_is_claimed(service, alpha):
    """The happy path. A gate nothing can pass is the same as no publishing."""
    post = _create(service, alpha, when=-5)
    assert service.approve(
        company_id=alpha["id"], post_id=post["id"], approver_user_id=2
    )

    claimed = service.claim_due(alpha["id"])

    assert len(claimed) == 1
    assert claimed[0]["id"] == post["id"]


def test_a_future_post_is_not_claimed_early(service, alpha):
    """Publishing before the scheduled time defeats the point of scheduling."""
    post = _create(service, alpha, when=120)
    service.approve(company_id=alpha["id"], post_id=post["id"], approver_user_id=2)

    assert service.claim_due(alpha["id"]) == []


# ----------------------------------------------------------------------
# The lease
# ----------------------------------------------------------------------


def test_a_claimed_post_is_not_claimed_again(service, alpha):
    """Two overlapping sweeps would otherwise publish the same post twice, and
    a duplicate post is public and embarrassing."""
    post = _create(service, alpha, when=-5)
    service.approve(company_id=alpha["id"], post_id=post["id"], approver_user_id=2)

    first = service.claim_due(alpha["id"])
    second = service.claim_due(alpha["id"])

    assert len(first) == 1
    assert second == []


def test_a_published_post_is_never_claimed_again(service, alpha):
    """The terminal state has to actually be terminal."""
    post = _create(service, alpha, when=-5)
    service.approve(company_id=alpha["id"], post_id=post["id"], approver_user_id=2)
    service.claim_due(alpha["id"])

    service.mark_published(
        company_id=alpha["id"], post_id=post["id"], provider_post_id="PROVIDER_1"
    )

    assert service.claim_due(alpha["id"]) == []
    assert (
        service.get_post(company_id=alpha["id"], post_id=post["id"])["status"]
        == "published"
    )


# ----------------------------------------------------------------------
# Failure handling
# ----------------------------------------------------------------------


def test_a_failed_publish_is_retried(service, alpha):
    """A revoked token or a network blip must not lose the post."""
    post = _create(service, alpha, when=-5)
    service.approve(company_id=alpha["id"], post_id=post["id"], approver_user_id=2)
    service.claim_due(alpha["id"])

    assert service.mark_failed(
        company_id=alpha["id"], post_id=post["id"], error="token expired"
    )

    # Back in the queue rather than gone.
    assert len(service.claim_due(alpha["id"])) == 1


def test_a_post_stops_retrying_and_stays_visible(service, alpha):
    """Retrying forever hides a real problem; deleting it hides the post. It
    ends as failed so somebody notices."""
    post = _create(service, alpha, when=-5)
    service.approve(company_id=alpha["id"], post_id=post["id"], approver_user_id=2)

    for _ in range(10):
        service.claim_due(alpha["id"])
        if not service.mark_failed(
            company_id=alpha["id"], post_id=post["id"], error="still failing"
        ):
            break

    stored = service.get_post(company_id=alpha["id"], post_id=post["id"])

    assert stored["status"] == "failed"
    assert stored["last_error"] == "still failing"
    assert service.claim_due(alpha["id"]) == []


def test_a_failed_post_can_be_approved_again(service, alpha):
    """After fixing the token, re-approving must reset the attempt count rather
    than leaving the post one failure from being abandoned."""
    post = _create(service, alpha, when=-5)
    service.approve(company_id=alpha["id"], post_id=post["id"], approver_user_id=2)

    for _ in range(10):
        service.claim_due(alpha["id"])
        if not service.mark_failed(
            company_id=alpha["id"], post_id=post["id"], error="failing"
        ):
            break

    assert service.approve(
        company_id=alpha["id"], post_id=post["id"], approver_user_id=2
    )

    stored = service.get_post(company_id=alpha["id"], post_id=post["id"])
    assert stored["status"] == "approved"
    assert stored["attempts"] == 0


# ----------------------------------------------------------------------
# Editing and isolation
# ----------------------------------------------------------------------


def test_a_published_post_cannot_be_edited(service, alpha):
    """The copy on the platform is the real one. Letting the record drift from
    it would make the calendar lie about what was posted."""
    post = _create(service, alpha, when=-5)
    service.approve(company_id=alpha["id"], post_id=post["id"], approver_user_id=2)
    service.mark_published(
        company_id=alpha["id"], post_id=post["id"], provider_post_id="P1"
    )

    assert (
        service.update_post(
            company_id=alpha["id"], post_id=post["id"], values={"body": "edited"}
        )
        is None
    )


def test_a_published_post_cannot_be_cancelled(service, alpha):
    """Cancelling something already public would tell the team it never went
    out, when in fact it needs deleting on the platform."""
    post = _create(service, alpha, when=-5)
    service.mark_published(
        company_id=alpha["id"], post_id=post["id"], provider_post_id="P1"
    )

    assert service.cancel(company_id=alpha["id"], post_id=post["id"]) is False


def test_a_cancelled_post_is_not_published(service, alpha):
    """Cancelling has to actually stop it, including after approval."""
    post = _create(service, alpha, when=-5)
    service.approve(company_id=alpha["id"], post_id=post["id"], approver_user_id=2)
    assert service.cancel(company_id=alpha["id"], post_id=post["id"])

    assert service.claim_due(alpha["id"]) == []


def test_one_company_cannot_see_another_companys_posts(service, alpha, beta):
    """Unpublished marketing is commercially sensitive."""
    _create(service, alpha, body="alpha campaign")

    assert service.list_posts(company_id=beta["id"])["total"] == 0
    assert service.list_posts(company_id=alpha["id"])["total"] == 1


def test_one_company_cannot_approve_another_companys_post(service, alpha, beta):
    """Post ids are guessable, so ownership is checked on every write."""
    post = _create(service, alpha)

    assert (
        service.approve(
            company_id=beta["id"], post_id=post["id"], approver_user_id=99
        )
        is False
    )
    assert (
        service.get_post(company_id=alpha["id"], post_id=post["id"])["status"]
        == "draft"
    )


def test_due_posts_are_claimed_only_for_their_own_company(service, alpha, beta):
    """A sweep runs per company; claiming across the boundary would publish one
    company's content through another's page."""
    alpha_post = _create(service, alpha, when=-5)
    beta_post = _create(service, beta, when=-5)

    service.approve(
        company_id=alpha["id"], post_id=alpha_post["id"], approver_user_id=2
    )
    service.approve(company_id=beta["id"], post_id=beta_post["id"], approver_user_id=2)

    claimed = service.claim_due(alpha["id"])

    assert len(claimed) == 1
    assert claimed[0]["id"] == alpha_post["id"]
    assert claimed[0]["company_id"] == alpha["id"]
