"""Tests for the company's record of its own workspace.

Of seventeen modules, three wrote any audit at all — and two of those had no
endpoint to read it back, so the trail existed and nobody could see it. There
was no record of a knowledge item being edited, a price being changed, a channel
being connected, a permission being granted, or an employee signing in.

The price one is the sharpest, and it has its own tests below: the assistant
quotes catalogue prices to customers as confirmed facts, so "who changed that,
and from what" is the question an owner is most likely to need answered.

Two things this file pins that are easy to get wrong later:

* the log never fails the thing it records — a price change must not be refused
  because the note about it could not be filed;
* the control-plane mirror carries security events and nothing else. Copying a
  company's business detail there would walk around the tenant boundary the
  rest of this platform keeps.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.activity_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.activity_service" in rebound

    from backend.services.activity_service import activity_service

    return activity_service


def _entries(service, company, **filters) -> list[dict]:
    return service.list_entries(company_id=company["id"], **filters)["items"]


def _control_rows(platform, company=None) -> list[dict]:
    with platform["manager"].control() as conn:
        if company is None:
            rows = conn.execute("SELECT * FROM audit_log").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE company_id = ?", (company["id"],)
            ).fetchall()

    return [dict(row) for row in rows]


# ------------------------------------------------------------------- basics


def test_an_entry_is_written_and_read_back(wired, alpha):
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.KNOWLEDGE_CREATED,
        category="knowledge",
        actor_user_id=7,
        actor_label="Rita Haddad",
        summary="Taught the assistant: Opening hours",
    )

    entries = _entries(wired, alpha)

    assert len(entries) == 1
    assert entries[0]["action"] == Action.KNOWLEDGE_CREATED
    assert entries[0]["actor_label"] == "Rita Haddad"


def test_the_actor_name_is_stored_not_joined(wired, alpha):
    """`users` lives in the control plane and the log in the tenant file, and
    SQLite cannot join across the two — three existing queries try and render
    every actor as "System". A snapshot also survives the employee leaving,
    which a join never would."""
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.PRODUCT_UPDATED,
        category="catalogue",
        actor_user_id=999_999,  # no such user anywhere
        actor_label="Sami Nasr",
    )

    assert _entries(wired, alpha)[0]["actor_label"] == "Sami Nasr"


def test_before_and_after_survive_the_round_trip(wired, alpha):
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.PRODUCT_PRICE_CHANGED,
        category="catalogue",
        before={"price": 25},
        after={"price": 30},
    )

    entry = _entries(wired, alpha)[0]

    assert entry["before"] == {"price": 25}
    assert entry["after"] == {"price": 30}


def test_recording_never_raises(wired):
    """An audit write that can fail a price update means the price does not
    change because the note about it could not be filed."""
    from backend.services.activity_service import Action

    wired.record(
        company_id=999_999,  # no such company, so no database to open
        action=Action.PRODUCT_UPDATED,
        category="catalogue",
    )


def test_an_unserialisable_value_does_not_lose_the_entry(wired, alpha):
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.PRODUCT_UPDATED,
        category="catalogue",
        after={"handle": object()},
    )

    assert len(_entries(wired, alpha)) == 1


def test_an_unknown_kind_is_filed_as_a_change_rather_than_dropped(wired, alpha):
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.PRODUCT_UPDATED,
        category="catalogue",
        kind="whatever",
    )

    assert _entries(wired, alpha)[0]["kind"] == "change"


# ------------------------------------------------------------------ isolation


def test_one_company_log_is_not_another_company_log(wired, alpha, beta):
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.PRODUCT_PRICE_CHANGED,
        category="catalogue",
        summary="Changed the price of the blue widget",
    )

    assert len(_entries(wired, alpha)) == 1
    assert _entries(wired, beta) == []


# --------------------------------------------------------------------- mirror


def test_a_security_event_is_mirrored_to_the_control_plane(wired, platform, alpha):
    """An attack spread across a thousand companies is invisible in any single
    company's log."""
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.CHANNEL_CONNECTED,
        category="channels",
        kind="security",
        summary="Connected a messenger account",
    )

    mirrored = _control_rows(platform, alpha)

    assert [row["action"] for row in mirrored] == [Action.CHANNEL_CONNECTED]


def test_an_ordinary_change_is_not_mirrored(wired, platform, alpha):
    """What a business sells is not an operator's business."""
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.PRODUCT_PRICE_CHANGED,
        category="catalogue",
        summary="Changed the price of the blue widget",
        before={"price": 25},
        after={"price": 30},
    )

    assert _control_rows(platform, alpha) == []


def test_the_mirror_carries_no_business_detail(wired, platform, alpha):
    """Copying before/after into the shared table would walk around the tenant
    boundary the rest of this platform keeps."""
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.PERMISSIONS_CHANGED,
        category="roles",
        kind="security",
        summary="Changed what the Supervisor role may do",
        before={"permissions": ["conversations.view"]},
        after={"permissions": ["conversations.view", "customers.export"]},
    )

    mirrored = _control_rows(platform, alpha)[0]

    assert "customers.export" not in str(mirrored["data_json"])
    assert "Supervisor" in str(mirrored["data_json"]), (
        "the operator still needs a one-line summary"
    )


def test_an_unattributed_failure_reaches_the_control_plane_only(wired, platform):
    """A refused sign-in names an email that may belong to nobody. Looking it
    up to find a company would take a different amount of time depending on
    whether the account exists — a timing oracle for enumerating employees on
    the one endpoint an attacker is already pointed at."""
    from backend.services.activity_service import Action

    wired.record_unattributed(
        action=Action.SIGN_IN_FAILED,
        summary="A sign-in was refused",
        ip_address="203.0.113.9",
    )

    rows = _control_rows(platform)

    assert len(rows) == 1
    assert rows[0]["company_id"] is None
    assert rows[0]["ip_address"] == "203.0.113.9"


# ------------------------------------------------------------------ filtering


def test_the_log_filters_by_category(wired, alpha):
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"], action=Action.PRODUCT_UPDATED, category="catalogue"
    )
    wired.record(
        company_id=alpha["id"], action=Action.KNOWLEDGE_UPDATED, category="knowledge"
    )

    assert len(_entries(wired, alpha, category="catalogue")) == 1


def test_the_log_filters_by_kind(wired, alpha):
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.CONVERSATION_OPENED,
        category="conversations",
        kind="read",
    )
    wired.record(
        company_id=alpha["id"], action=Action.PRODUCT_UPDATED, category="catalogue"
    )

    assert len(_entries(wired, alpha, kind="read")) == 1
    assert len(_entries(wired, alpha, kind="change")) == 1


def test_the_log_filters_by_actor(wired, alpha):
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.PRODUCT_UPDATED,
        category="catalogue",
        actor_user_id=1,
    )
    wired.record(
        company_id=alpha["id"],
        action=Action.PRODUCT_UPDATED,
        category="catalogue",
        actor_user_id=2,
    )

    assert len(_entries(wired, alpha, actor_user_id=2)) == 1


def test_the_log_searches_the_summary(wired, alpha):
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.PRODUCT_PRICE_CHANGED,
        category="catalogue",
        summary="Changed the price of the blue widget",
    )
    wired.record(
        company_id=alpha["id"],
        action=Action.PRODUCT_UPDATED,
        category="catalogue",
        summary="Edited the red widget",
    )

    assert len(_entries(wired, alpha, search="blue")) == 1


def test_the_options_are_built_from_what_actually_happened(wired, alpha):
    """A dropdown listing thirty actions a company has never performed is a
    dropdown nobody can use."""
    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"], action=Action.PRODUCT_UPDATED, category="catalogue"
    )

    options = wired.options(alpha["id"])

    assert options["categories"] == ["catalogue"]
    assert options["actions"] == [Action.PRODUCT_UPDATED]


def test_the_newest_entry_comes_first(wired, alpha):
    from backend.services.activity_service import Action

    for index in range(3):
        wired.record(
            company_id=alpha["id"],
            action=Action.PRODUCT_UPDATED,
            category="catalogue",
            summary=f"edit {index}",
        )

    assert _entries(wired, alpha)[0]["summary"] == "edit 2"


def test_the_total_counts_matches_not_the_page(wired, alpha):
    from backend.services.activity_service import Action

    for index in range(5):
        wired.record(
            company_id=alpha["id"],
            action=Action.PRODUCT_UPDATED,
            category="catalogue",
            summary=f"edit {index}",
        )

    page = wired.list_entries(company_id=alpha["id"], limit=2)

    assert len(page["items"]) == 2
    assert page["total"] == 5


# ----------------------------------------------------------------- retention


def test_reads_expire_sooner_than_changes(wired, platform, alpha):
    """Reads are the highest volume by far. Sharing a retention with changes
    would mean recording who read what buries who changed what."""
    from datetime import datetime, timedelta, timezone

    from backend.services.activity_service import Action

    wired.record(
        company_id=alpha["id"],
        action=Action.CONVERSATION_OPENED,
        category="conversations",
        kind="read",
    )
    wired.record(
        company_id=alpha["id"], action=Action.PRODUCT_UPDATED, category="catalogue"
    )

    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()

    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.execute("UPDATE activity_log SET created_at = ?", (old,))
        conn.commit()

    removed = wired.prune(alpha["id"])

    assert removed["read"] == 1
    assert removed["change"] == 0
    assert len(_entries(wired, alpha)) == 1


def test_pruning_never_raises(wired):
    assert wired.prune(999_999) == {}


# --------------------------------------------------- the misspelled counters


def test_the_analytics_event_names_match_the_writer():
    """`analytics_service` counted `'human_took_over'` and
    `'assigned_user_changed'` while the writers wrote `human_takeover` and
    `assignment_changed`. The report read zero for every company since it
    shipped — and a zero is exactly the kind of wrong answer nobody questions.

    Both ends now import the same constant, so this asserts the constant is
    what is actually written to the timeline.
    """
    import inspect

    from backend.services.conversation_control_service import (
        EVENT_ASSIGNMENT_CHANGED,
        EVENT_HUMAN_TAKEOVER,
        conversation_control_service,
    )

    source = inspect.getsource(type(conversation_control_service))

    assert f'"{EVENT_HUMAN_TAKEOVER}"' in source
    assert f'"{EVENT_ASSIGNMENT_CHANGED}"' in source
    assert "human_took_over" not in source
    assert "assigned_user_changed" not in source
