"""Tests for plan allowances, which were displayed and enforced nowhere.

`plans.max_users`, `max_channel_accounts`, `max_knowledge_items` and
`max_ai_messages` were read in exactly one place — the dashboard, to draw a
number on a card. Nothing refused a sixth user on a five-user plan. There was no
`usage_records` table, so `max_ai_messages` had nothing to count against.

Two resolution defects had to go first, because they made "which plan is this
company on" answer differently depending on who asked:

* a blank expiry read as expired, while the console's own form says "Leave the
  date empty for a plan that does not expire";
* an expired subscription still named the company's plan in the console,
  because that query filtered on status and never looked at `expires_at`.

The tests that matter most here are the bypasses: every limit is enforced on
two paths, and a limit guarded only on the create is one anybody can step
around by disabling a member and re-enabling them.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import backend.services.knowledge_service  # noqa: F401
    import backend.services.plan_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    for required in (
        "backend.services.plan_service",
        "backend.services.knowledge_service",
        "backend.services.channel_account_service",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    from backend.services.plan_service import plan_service

    return plan_service


def _subscribe(
    platform,
    company,
    *,
    plan_code: str = "starter",
    status: str = "active",
    expires_at: str | None = None,
    grace_period_until: str | None = None,
) -> None:
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].control() as conn:
        plan = conn.execute(
            "SELECT id FROM plans WHERE code = ?", (plan_code,)
        ).fetchone()

        conn.execute(
            """
            INSERT INTO subscriptions (
                company_id, plan_id, status, starts_at, expires_at,
                grace_period_until, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company["id"],
                int(plan["id"]),
                status,
                now,
                expires_at,
                grace_period_until,
                now,
                now,
            ),
        )
        conn.commit()


def _iso(days: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


# ------------------------------------------------------- subscription defects


def test_a_blank_expiry_does_not_expire(wired, platform, alpha):
    """The console's own form says "Leave the date empty for a plan that does
    not expire", and the check returned False for exactly that. Every company
    deliberately set up never to expire read as unsubscribed."""
    _subscribe(platform, alpha, expires_at=None)

    assert wired.is_active(wired.subscription(alpha["id"])) is True


def test_an_expired_subscription_is_not_active(wired, platform, alpha):
    _subscribe(platform, alpha, expires_at=_iso(-1))

    assert wired.is_active(wired.subscription(alpha["id"])) is False


def test_a_grace_period_keeps_a_lapsed_subscription_alive(wired, platform, alpha):
    """The difference between a lapsed payment and a cancelled account. A
    business must not lose its inbox mid-conversation over a card that failed
    this morning."""
    _subscribe(
        platform, alpha, expires_at=_iso(-1), grace_period_until=_iso(7)
    )

    assert wired.is_active(wired.subscription(alpha["id"])) is True


def test_a_cancelled_subscription_is_not_active(wired, platform, alpha):
    _subscribe(platform, alpha, status="cancelled", expires_at=_iso(30))

    assert wired.is_active(wired.subscription(alpha["id"])) is False


def test_the_console_shows_an_expired_plan_rather_than_no_plan(
    wired, platform, alpha, monkeypatch
):
    """The console query filtered on `status = 'active'` and ignored
    `expires_at`, so a subscription that ran out last year went on naming the
    company's plan with nothing to say it had lapsed.

    Hiding the expired row instead would be a different error: an operator
    would see a company with no plan at all, which is much less actionable
    than a plan that ended on a date.
    """
    from backend.services.platform_service import platform_service

    _subscribe(platform, alpha, expires_at=_iso(-40))

    company = next(
        item
        for item in platform_service.list_companies()
        if int(item["id"]) == alpha["id"]
    )

    assert company["plan_code"] == "starter"
    assert company["plan_active"] is False


def test_the_console_and_the_dashboard_agree(wired, platform, alpha):
    """Two resolutions of the same question is how they came to disagree."""
    from backend.api.routes.dashboard import _subscription_is_active

    _subscribe(platform, alpha, expires_at=None)

    subscription = wired.subscription(alpha["id"])

    assert _subscription_is_active(subscription) is wired.is_active(subscription)


# --------------------------------------------------------------- limit maths


def test_zero_means_unlimited(wired, platform, alpha):
    """Every allowance defaults to 0 in the schema. Read as "none allowed", a
    plan created by leaving the fields blank would forbid its customer from
    adding a single user — a plan nobody could use."""
    wired.set_override(company_id=alpha["id"], limit_key="max_users", value=0)

    wired.check(alpha["id"], "max_users", used=10_000)


def test_a_company_with_no_subscription_is_not_locked_out(wired, alpha):
    """Zero here would mean a billing lapse silently locks a business out of
    its own workspace. That is a decision for an operator to make explicitly,
    not a side effect of a limit lookup."""
    assert wired.limits(alpha["id"])["max_users"] == 0

    wired.check(alpha["id"], "max_users", used=50)


def test_an_override_beats_the_plan(wired, platform, alpha):
    _subscribe(platform, alpha, plan_code="starter")  # 2 users
    assert wired.limit(alpha["id"], "max_users") == 2

    wired.set_override(company_id=alpha["id"], limit_key="max_users", value=7)

    assert wired.limit(alpha["id"], "max_users") == 7


def test_clearing_an_override_puts_the_company_back_on_its_plan(
    wired, platform, alpha
):
    _subscribe(platform, alpha, plan_code="starter")
    wired.set_override(company_id=alpha["id"], limit_key="max_users", value=7)

    wired.clear_override(company_id=alpha["id"], limit_key="max_users")

    assert wired.limit(alpha["id"], "max_users") == 2


def test_an_override_reaches_only_the_company_it_names(wired, platform, alpha, beta):
    """The whole reason overrides exist rather than editing the plan row:
    accommodating one customer must not raise the ceiling for everybody on
    that plan."""
    _subscribe(platform, alpha, plan_code="starter")
    _subscribe(platform, beta, plan_code="starter")

    wired.set_override(company_id=alpha["id"], limit_key="max_users", value=7)

    assert wired.limit(alpha["id"], "max_users") == 7
    assert wired.limit(beta["id"], "max_users") == 2


def test_the_refusal_says_what_the_limit_was_and_how_much_is_used(
    wired, platform, alpha
):
    from backend.services.plan_service import PlanLimitExceeded

    _subscribe(platform, alpha, plan_code="starter")

    with pytest.raises(PlanLimitExceeded) as raised:
        wired.check(alpha["id"], "max_users", used=2)

    assert raised.value.limit == 2
    assert raised.value.used == 2
    assert "2" in str(raised.value)
    assert raised.value.as_detail()["limit_key"] == "max_users"


def test_the_check_refuses_at_the_limit_not_past_it(wired, platform, alpha):
    """The caller is asking to add one more, so `used >= limit` is the test."""
    from backend.services.plan_service import PlanLimitExceeded

    _subscribe(platform, alpha, plan_code="starter")  # 2 users

    wired.check(alpha["id"], "max_users", used=1)

    with pytest.raises(PlanLimitExceeded):
        wired.check(alpha["id"], "max_users", used=2)


def test_an_unknown_limit_key_is_refused_rather_than_ignored(wired, alpha):
    with pytest.raises(KeyError):
        wired.limit(alpha["id"], "max_seats")


def test_features_are_off_without_a_subscription(wired, alpha):
    """Unlike an allowance, a feature nobody paid for was never theirs."""
    assert wired.features(alpha["id"])["voice_ai_enabled"] is False


def test_features_follow_the_plan(wired, platform, alpha):
    _subscribe(platform, alpha, plan_code="business")

    assert wired.features(alpha["id"])["voice_ai_enabled"] is True


# ------------------------------------------------------------------ knowledge


def test_knowledge_stops_at_the_plan_limit(wired, platform, alpha):
    from backend.services.knowledge_service import knowledge_service

    _subscribe(platform, alpha, plan_code="starter")
    wired.set_override(
        company_id=alpha["id"], limit_key="max_knowledge_items", value=2
    )

    for index in range(2):
        knowledge_service.create_item(
            company_id=alpha["id"],
            data={"title": f"Item {index}", "content_en": "text"},
        )

    with pytest.raises(ValueError, match="knowledge items"):
        knowledge_service.create_item(
            company_id=alpha["id"],
            data={"title": "One too many", "content_en": "text"},
        )


def test_archiving_does_not_free_a_knowledge_slot(wired, platform, alpha):
    """Every row counts, not only active ones. An archived item is storage the
    company is still using, and counting only the active ones would let a base
    grow without limit by archiving as it goes."""
    from backend.services.knowledge_service import knowledge_service

    wired.set_override(
        company_id=alpha["id"], limit_key="max_knowledge_items", value=1
    )

    created = knowledge_service.create_item(
        company_id=alpha["id"],
        data={"title": "Item", "content_en": "text", "status": "archived"},
    )
    assert created

    with pytest.raises(ValueError):
        knowledge_service.create_item(
            company_id=alpha["id"],
            data={"title": "Second", "content_en": "text"},
        )


# ------------------------------------------------------------------- channels


def test_channels_stop_at_the_plan_limit(wired, platform, alpha):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    wired.set_override(
        company_id=alpha["id"], limit_key="max_channel_accounts", value=1
    )

    channel_account_service.create_account(
        company_id=alpha["id"],
        channel="messenger",
        name="First",
        values={"page_id": "PAGE_1", "access_token": "t"},
    )

    with pytest.raises(ChannelAccountError, match="connected channels"):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="messenger",
            name="Second",
            values={"page_id": "PAGE_2", "access_token": "t"},
        )


def test_the_bundle_limits_how_many_channels_not_which_kinds(
    wired, platform, alpha
):
    """A three-channel plan may be spent on three Instagram accounts, or on one
    each of three types. The limit counts rows and nothing else."""
    from backend.services.channel_account_service import channel_account_service

    wired.set_override(
        company_id=alpha["id"], limit_key="max_channel_accounts", value=3
    )

    for index in range(3):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="instagram",
            name=f"IG {index}",
            values={"instagram_business_id": f"IG_{index}", "access_token": "t"},
        )

    assert len(channel_account_service.list_accounts(alpha["id"])) == 3


def test_disabling_and_re_enabling_does_not_bypass_the_channel_limit(
    wired, platform, alpha
):
    """The bypass. A limit guarded only on the create is one anybody can step
    around by disabling an account and re-enabling it."""
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    wired.set_override(
        company_id=alpha["id"], limit_key="max_channel_accounts", value=1
    )

    first = channel_account_service.create_account(
        company_id=alpha["id"],
        channel="messenger",
        name="First",
        values={"page_id": "PAGE_1", "access_token": "t"},
    )

    channel_account_service.update_account(
        company_id=alpha["id"],
        account_id=int(first["id"]),
        values={"status": "disabled"},
    )

    second = channel_account_service.create_account(
        company_id=alpha["id"],
        channel="messenger",
        name="Second",
        values={"page_id": "PAGE_2", "access_token": "t"},
    )
    assert second, "a disabled account should free its slot"

    with pytest.raises(ChannelAccountError, match="connected channels"):
        channel_account_service.update_account(
            company_id=alpha["id"],
            account_id=int(first["id"]),
            values={"status": "active"},
        )


def test_editing_an_active_channel_is_not_refused_for_its_own_slot(
    wired, platform, alpha
):
    """Re-saving an already-active account — renaming it, pointing it at a
    different department — must not be refused for occupying the slot it
    already occupies."""
    from backend.services.channel_account_service import channel_account_service

    wired.set_override(
        company_id=alpha["id"], limit_key="max_channel_accounts", value=1
    )

    created = channel_account_service.create_account(
        company_id=alpha["id"],
        channel="messenger",
        name="First",
        values={"page_id": "PAGE_1", "access_token": "t"},
    )

    updated = channel_account_service.update_account(
        company_id=alpha["id"],
        account_id=int(created["id"]),
        values={"name": "Renamed", "status": "active"},
    )

    assert updated["name"] == "Renamed"


# ---------------------------------------------------------------------- usage


def test_usage_counts_per_month(wired, alpha):
    wired.record_usage(company_id=alpha["id"], metric="ai_replies", period="2026-01")
    wired.record_usage(company_id=alpha["id"], metric="ai_replies", period="2026-01")
    wired.record_usage(company_id=alpha["id"], metric="ai_replies", period="2026-02")

    assert wired.usage_total(company_id=alpha["id"], metric="ai_replies", period="2026-01") == 2
    assert wired.usage_total(company_id=alpha["id"], metric="ai_replies", period="2026-02") == 1


def test_usage_is_recorded_per_channel_and_department(wired, alpha):
    wired.record_usage(
        company_id=alpha["id"],
        metric="ai_replies",
        channel="messenger",
        department_id=1,
        period="2026-03",
    )
    wired.record_usage(
        company_id=alpha["id"],
        metric="ai_replies",
        channel="whatsapp",
        department_id=2,
        period="2026-03",
    )

    breakdown = wired.usage_breakdown(company_id=alpha["id"], period="2026-03")

    assert {row["channel"] for row in breakdown} == {"messenger", "whatsapp"}
    assert wired.usage_total(company_id=alpha["id"], metric="ai_replies", period="2026-03") == 2


def test_one_company_usage_is_not_another_company_bill(wired, alpha, beta):
    wired.record_usage(company_id=alpha["id"], metric="ai_replies", period="2026-04")

    assert wired.usage_total(company_id=beta["id"], metric="ai_replies", period="2026-04") == 0


def test_recording_usage_never_raises(wired, alpha):
    """A counter that can fail a reply costs a customer their answer over a
    number."""
    wired.record_usage(company_id=999_999, metric="ai_replies")
    wired.record_usage(company_id=alpha["id"], metric="ai_replies", quantity=-5)


def test_headroom_reports_what_is_left(wired, platform, alpha):
    _subscribe(platform, alpha, plan_code="starter")  # 2 users

    assert wired.headroom(alpha["id"], "max_users", used=1) == {
        "limit_key": "max_users",
        "limit": 2,
        "unlimited": False,
        "used": 1,
        "remaining": 1,
        "percent": 50.0,
    }


def test_headroom_on_an_unlimited_allowance_reports_no_ceiling(wired, alpha):
    result = wired.headroom(alpha["id"], "max_users", used=40)

    assert result["unlimited"] is True
    assert result["remaining"] is None


# ---------------------------------------------------------------- reply gate


def test_the_assistant_stops_when_the_monthly_allowance_is_spent(
    wired, platform, alpha
):
    from channels.meta.smart_reply import _ai_allowance_spent

    wired.set_override(
        company_id=alpha["id"], limit_key="max_ai_messages", value=2
    )

    assert _ai_allowance_spent(alpha["id"]) is False

    wired.record_usage(company_id=alpha["id"], metric="ai_replies")
    wired.record_usage(company_id=alpha["id"], metric="ai_replies")

    assert _ai_allowance_spent(alpha["id"]) is True


def test_an_unlimited_allowance_never_stops_the_assistant(wired, alpha):
    from channels.meta.smart_reply import _ai_allowance_spent

    for _ in range(5):
        wired.record_usage(company_id=alpha["id"], metric="ai_replies")

    assert _ai_allowance_spent(alpha["id"]) is False


def test_an_unreadable_control_plane_lets_the_assistant_reply(
    wired, alpha, monkeypatch
):
    """The same direction every other guard here fails. A billing lookup must
    never cost a customer an answer."""
    import channels.meta.smart_reply as smart_reply_module
    from database.manager import DatabaseError

    wired.set_override(
        company_id=alpha["id"], limit_key="max_ai_messages", value=1
    )
    wired.record_usage(company_id=alpha["id"], metric="ai_replies")
    assert smart_reply_module._ai_allowance_spent(alpha["id"]) is True

    def explode(*args, **kwargs):
        raise DatabaseError("control plane is unavailable")

    monkeypatch.setattr(
        smart_reply_module.plan_service, "limit", explode, raising=True
    )

    assert smart_reply_module._ai_allowance_spent(alpha["id"]) is False
