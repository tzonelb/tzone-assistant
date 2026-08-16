"""Tests for the reporting figures.

Reports drive staffing and spending decisions, so a wrong number here is worse
than no number. These check the arithmetic against data whose correct answer is
known by construction, and that one company's figures never include another's.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the analytics service at the test platform's databases."""
    import sys

    import backend.services.analytics_service  # noqa: F401
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.analytics_service" in rebound

    from backend.services.analytics_service import analytics_service

    return analytics_service


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _seed(platform, company, *, conversation_id=1, messages):
    """Insert a conversation and its messages. `messages` is (direction, sender_type, hours_ago)."""
    manager = platform["manager"]
    now = datetime.now(timezone.utc).isoformat()

    with manager.tenant(company["id"]) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO conversations (
                id, company_id, channel, external_user_id, created_at, updated_at
            )
            VALUES (?, ?, 'messenger', ?, ?, ?)
            """,
            (conversation_id, company["id"], f"cust-{conversation_id}", now, now),
        )

        for direction, sender_type, hours_ago in messages:
            conn.execute(
                """
                INSERT INTO messages (
                    company_id, conversation_id, channel, external_user_id,
                    direction, sender_type, body, created_at
                )
                VALUES (?, ?, 'messenger', ?, ?, ?, 'x', ?)
                """,
                (
                    company["id"],
                    conversation_id,
                    f"cust-{conversation_id}",
                    direction,
                    sender_type,
                    _iso(hours_ago),
                ),
            )

        conn.commit()


def test_message_totals_are_counted_correctly(service, platform, alpha):
    """The headline figure on the reporting screen; an off-by-one here is
    visible to the owner and undermines every other number."""
    _seed(
        platform,
        alpha,
        messages=[
            ("in", "customer", 5),
            ("in", "customer", 4),
            ("out", "ai", 3),
            ("out", "employee", 2),
        ],
    )

    overview = service.overview(company_id=alpha["id"], days=30)

    assert overview["messages"]["total"] == 4
    assert overview["messages"]["inbound"] == 2
    assert overview["messages"]["outbound"] == 2
    assert overview["messages"]["by_assistant"] == 1
    assert overview["messages"]["by_employee"] == 1


def test_automation_rate_is_share_of_outbound(service, platform, alpha):
    """Reported as a share of replies sent, not of all messages — otherwise the
    assistant looks half as effective as it is."""
    _seed(
        platform,
        alpha,
        messages=[
            ("in", "customer", 5),
            ("out", "ai", 4),
            ("out", "ai", 3),
            ("out", "employee", 2),
        ],
    )

    overview = service.overview(company_id=alpha["id"], days=30)

    assert overview["messages"]["automation_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_automation_rate_is_zero_when_nothing_was_sent(service, alpha):
    """A brand new company must not divide by zero on first load."""
    overview = service.overview(company_id=alpha["id"], days=30)

    assert overview["messages"]["automation_rate"] == 0.0
    assert overview["messages"]["total"] == 0


def test_one_company_never_sees_another_companys_figures(
    service, platform, alpha, beta
):
    """Reports read the tenant database directly, so this is the check that the
    encryption boundary is actually respected by the reporting path too."""
    _seed(platform, alpha, messages=[("in", "customer", 2)] * 5)
    _seed(platform, beta, messages=[("in", "customer", 2)] * 2)

    assert service.overview(company_id=alpha["id"], days=30)["messages"]["total"] == 5
    assert service.overview(company_id=beta["id"], days=30)["messages"]["total"] == 2


def test_messages_outside_the_window_are_excluded(service, platform, alpha):
    """A 7-day report that quietly includes older traffic would overstate
    current volume and hide a real decline."""
    _seed(
        platform,
        alpha,
        messages=[
            ("in", "customer", 2),
            ("in", "customer", 24 * 20),
        ],
    )

    assert service.overview(company_id=alpha["id"], days=7)["messages"]["total"] == 1
    assert service.overview(company_id=alpha["id"], days=90)["messages"]["total"] == 2


def test_requested_range_is_clamped(service, alpha):
    """The range bounds the scan. An unbounded value would turn one screen load
    into a full-history table scan."""
    assert service.overview(company_id=alpha["id"], days=99999)["range_days"] == 365
    assert service.overview(company_id=alpha["id"], days=0)["range_days"] == 1


def test_first_response_time_measures_the_customers_wait(service, platform, alpha):
    """Measured from the customer's first message to the first reply after it,
    which is the delay the customer actually experiences."""
    _seed(
        platform,
        alpha,
        messages=[
            ("in", "customer", 3),
            ("out", "ai", 2),
        ],
    )

    result = service.first_response_times(company_id=alpha["id"], days=30)

    assert result["answered"] == 1
    assert result["unanswered"] == 0
    # One hour between the two, within a tolerance for clock drift in the test.
    assert result["average_seconds"] == pytest.approx(3600, abs=60)


def test_unanswered_conversations_are_reported_separately(service, platform, alpha):
    """Excluding them entirely would make response time look perfect precisely
    when customers are being ignored."""
    _seed(platform, alpha, conversation_id=1, messages=[("in", "customer", 3)])

    result = service.first_response_times(company_id=alpha["id"], days=30)

    assert result["answered"] == 0
    assert result["unanswered"] == 1
    assert result["average_seconds"] is None


def test_hourly_distribution_always_covers_all_24_hours(service, platform, alpha):
    """Quiet hours must appear as zero rather than vanish, or the chart implies
    coverage that does not exist."""
    _seed(platform, alpha, messages=[("in", "customer", 1)])

    hours = service.hourly_distribution(company_id=alpha["id"], days=30)

    assert len(hours) == 24
    assert [entry["hour"] for entry in hours] == [f"{h:02d}" for h in range(24)]
    assert sum(entry["messages"] for entry in hours) == 1


def test_channel_breakdown_counts_distinct_conversations(service, platform, alpha):
    """Message count and conversation count differ, and conflating them makes a
    single chatty customer look like many."""
    _seed(platform, alpha, conversation_id=1, messages=[("in", "customer", 2)] * 4)
    _seed(platform, alpha, conversation_id=2, messages=[("in", "customer", 2)])

    breakdown = service.by_channel(company_id=alpha["id"], days=30)

    assert len(breakdown) == 1
    assert breakdown[0]["channel"] == "messenger"
    assert breakdown[0]["messages"] == 5
    assert breakdown[0]["conversations"] == 2


def test_employee_activity_returns_ids_not_names(service, platform, alpha):
    """Names live in the control database. Returning ids keeps the tenant query
    free of a cross-database join the route resolves in one batch."""
    manager = platform["manager"]
    now = datetime.now(timezone.utc).isoformat()

    with manager.tenant(alpha["id"]) as conn:
        conn.execute(
            """
            INSERT INTO conversations (
                id, company_id, channel, external_user_id, created_at, updated_at
            )
            VALUES (1, ?, 'messenger', 'c1', ?, ?)
            """,
            (alpha["id"], now, now),
        )
        for _ in range(3):
            conn.execute(
                """
                INSERT INTO messages (
                    company_id, conversation_id, channel, external_user_id,
                    direction, sender_type, sender_user_id, body, created_at
                )
                VALUES (?, 1, 'messenger', 'c1', 'out', 'employee', 77, 'x', ?)
                """,
                (alpha["id"], _iso(1)),
            )
        conn.commit()

    activity = service.employee_activity(company_id=alpha["id"], days=30)

    assert activity == [{"user_id": 77, "replies": 3, "takeovers": 0}]


def test_assistant_failure_rate_is_zero_without_attempts(service, alpha):
    """A new company shows a clean assistant, not a divide-by-zero."""
    health = service.assistant_health(company_id=alpha["id"], days=30)

    assert health["failure_rate"] == 0.0
    assert health["replies_sent"] == 0
