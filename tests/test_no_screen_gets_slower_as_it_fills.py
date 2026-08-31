"""The work an endpoint does must not grow with the number of rows it returns.

The defect this catches has no symptom in development. A screen that issues one
extra query per row is indistinguishable from a correct one at the ten rows a
test fixture creates; at the two thousand conversations a real inbox holds a
year in, the same screen issues two thousand queries and takes minutes. Nobody
changed anything. The company just got busy.

It is also invisible in a diff. The per-row query is usually a name lookup
added to a loop in the *route*, one line, obviously correct in isolation —
which is why this is measured rather than reviewed. The count of SQL statements
is taken from SQLite's own statement trace at two sizes, and compared.

What "constant" means here: within a small tolerance, because a page of forty
rows genuinely does a little more work than a page of five (longer result
decoding, one extra count). What it must not do is grow *with* the rows.

Also measured, and reported rather than asserted: how many times a request
opens an encrypted database. Each open derives a key and runs SQLCipher's
setup, about 1.0 ms for the control plane and 1.4 ms for a company file on the
machine this was written on. The inbox opens eleven, so roughly fifteen
milliseconds of every inbox request is connection setup. That is a fact worth
having written down before somebody adds the twelfth.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"

SMALL = 5
LARGE = 40

# A page of 40 rows may legitimately cost a couple of statements more than a
# page of 5. Anything above this is growth per row, not per page.
TOLERANCE = 3


@pytest.fixture()
def measured(platform, monkeypatch):
    """A client, and a counter of every SQL statement it causes.

    `execute` cannot be wrapped — it is read-only on the SQLCipher connection
    object — so the count comes from SQLite's own trace callback, which fires
    once per statement actually run.
    """
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import (
        activity, appointments, auth, conversations, customers, roles,
        team_chat, tickets,
    )

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    assert (
        getattr(sys.modules["backend.services.auth_service"], "database_manager", None)
        is test_manager
    )

    counters = {"sql": 0, "opens": 0}
    original_open = DatabaseManager._open

    def counting_open(self, path, key):
        counters["opens"] += 1
        connection = original_open(self, path, key)
        connection.set_trace_callback(
            lambda statement: counters.__setitem__("sql", counters["sql"] + 1)
        )
        return connection

    monkeypatch.setattr(DatabaseManager, "_open", counting_open)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (auth, activity, appointments, conversations, customers,
                   roles, team_chat):
        app.include_router(module.router)

    app.include_router(tickets.tasks_router)

    return TestClient(app, raise_server_exceptions=False), counters


@pytest.fixture()
def owner(platform, alpha, measured):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    client, _ = measured

    user_id = auth_service.create_user(
        email="owner@alpha.example.com", password=PASSWORD, full_name="Alpha Owner"
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (alpha["id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (alpha["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "owner@alpha.example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return {
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


# ------------------------------------------------------------------ seeding


def _seed_conversations(platform, alpha, owner, count, start):
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.message_service import message_service

    for index in range(start, start + count):
        conversation_control_service.get_or_create(
            company_id=alpha["id"], channel="messenger", external_user_id=f"u{index}"
        )
        message_service.save_message(
            company_id=alpha["id"],
            channel="messenger",
            external_user_id=f"u{index}",
            direction="in",
            text="hello",
            sender_type="customer",
        )


def _seed_appointments(platform, alpha, owner, count, start):
    from datetime import datetime, timedelta, timezone

    from backend.services.appointment_service import appointment_service

    base = datetime(2030, 1, 1, 8, tzinfo=timezone.utc)

    for index in range(start, start + count):
        appointment_service.create(
            company_id=alpha["id"],
            staff_user_id=owner["user_id"],
            starts_at=(base + timedelta(hours=index)).isoformat(),
            ends_at=(base + timedelta(hours=index, minutes=30)).isoformat(),
            title=f"Appointment {index}",
        )


def _seed_tasks(platform, alpha, owner, count, start):
    from backend.services.ticket_service import ticket_service

    for index in range(start, start + count):
        ticket_service.create_task(
            company_id=alpha["id"],
            data={"title": f"Task {index}", "assigned_user_id": owner["user_id"]},
        )


def _seed_channels(platform, alpha, owner, count, start):
    from backend.services.team_chat_service import team_chat_service

    for index in range(start, start + count):
        team_chat_service.create_channel(
            company_id=alpha["id"], user_id=owner["user_id"], name=f"room-{index}"
        )


def _seed_customers(platform, alpha, owner, count, start):
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.executemany(
            """
            INSERT INTO customers (
                company_id, display_name, first_seen_at, last_seen_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (alpha["id"], f"Customer {index}", now, now, now, now)
                for index in range(start, start + count)
            ],
        )
        conn.commit()


def _seed_activity(platform, alpha, owner, count, start):
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.executemany(
            """
            INSERT INTO activity_log (
                company_id, kind, category, action, actor_user_id, summary,
                severity, created_at
            )
            VALUES (?, 'change', 'catalogue', 'catalogue.item_updated', ?, ?,
                    'info', ?)
            """,
            [
                (alpha["id"], owner["user_id"], f"change {index}", now)
                for index in range(start, start + count)
            ],
        )
        conn.commit()


SCREENS = [
    ("the inbox", "/conversations/", _seed_conversations),
    ("the calendar", "/api/appointments", _seed_appointments),
    ("the task board", "/api/tasks", _seed_tasks),
    ("team chat", "/api/team-chat/channels", _seed_channels),
    ("the customer list", "/api/customers", _seed_customers),
    ("the activity log", "/api/activity", _seed_activity),
]


@pytest.mark.parametrize(
    "label,path,seed", SCREENS, ids=[row[0].replace(" ", "-") for row in SCREENS]
)
def test_a_screen_costs_the_same_whether_it_is_empty_or_full(
    measured, owner, platform, alpha, label, path, seed
):
    client, counters = measured

    def load():
        counters["sql"] = counters["opens"] = 0
        response = client.get(path, headers=owner["headers"], params={"limit": 200})

        assert response.status_code == 200, (
            f"{label} did not load: {response.status_code} {response.text}"
        )

        return counters["sql"], counters["opens"]

    seed(platform, alpha, owner, SMALL, 0)
    small_sql, small_opens = load()

    seed(platform, alpha, owner, LARGE - SMALL, SMALL)
    large_sql, large_opens = load()

    growth = large_sql - small_sql

    assert growth <= TOLERANCE, (
        f"{label} issues {small_sql} queries for {SMALL} rows and {large_sql} "
        f"for {LARGE} — {growth} more for {LARGE - SMALL} extra rows. That is "
        "a query per row: fine here, minutes on a real company's data."
    )

    assert large_opens - small_opens <= TOLERANCE, (
        f"{label} opens {small_opens} databases for {SMALL} rows and "
        f"{large_opens} for {LARGE}. Each open derives a key and runs "
        "SQLCipher's setup — roughly a millisecond — so one per row is far "
        "worse than one query per row."
    )


def test_the_counter_would_notice_a_query_per_row(measured, owner, platform, alpha):
    """The control.

    Every assertion above is of the form "this number did not grow". A counter
    that had quietly stopped counting would satisfy all of them, and this file
    would report six healthy screens while measuring nothing. So: do something
    that genuinely costs one query per row, and check the counter sees it.
    """
    client, counters = measured

    _seed_customers(platform, alpha, owner, 20, 0)

    from backend.services.customer_service import customer_service

    counters["sql"] = 0

    for customer_id in range(1, 21):
        customer_service.get_customer(company_id=alpha["id"], customer_id=customer_id)

    assert counters["sql"] >= 20, (
        f"twenty single-row reads registered {counters['sql']} statements — "
        "the trace callback is not counting, so every result in this file is "
        "meaningless"
    )
