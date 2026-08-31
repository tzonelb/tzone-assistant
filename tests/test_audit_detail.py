"""The before-and-after that was written for years and never read.

`company_setting_audit` and `customer_audit` have held the old and new values
of every settings change and every customer edit since each shipped. No
endpoint read either one. The sweep that checks every table for readers and
writers reported them, and the honest first answer was to declare them: rows
accumulating where nobody could open them and nothing pruned them.

Declaring a gap is not closing it. This is the reader, and the retention that
should always have come with it.

Why these live behind a different permission from the rest of the log: the
unified log says a settings section changed and names the keys, never the
values, because a section is an open bag and a customer field is somebody's
phone number. The values are the sensitive half. Reading what a setting used to
be sits closer to being able to change it than to being able to see it, and
reading what a customer's number used to be belongs to whoever may see the
number now — not to everyone who may read the log.
"""

from __future__ import annotations

import sys

import pytest


ADMIN_PASSWORD = "AdminPass123456"


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.activity  # noqa: F401
    import backend.api.routes.auth  # noqa: F401
    import backend.services.activity_service  # noqa: F401
    import backend.services.company_settings_service  # noqa: F401
    import backend.services.customer_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.activity_service" in rebound
    assert "backend.services.company_settings_service" in rebound

    from backend.services.activity_service import activity_service

    return activity_service


@pytest.fixture()
def client(wired):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import activity, auth

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(activity.router)

    return TestClient(app)


def _token(client, platform, company, email, role):
    from backend.services.auth_service import auth_service

    user_id = auth_service.create_user(email, ADMIN_PASSWORD, "Person")
    auth_service.assign_user_to_company(user_id, company["id"], role)

    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"],
            "company": company["name"],
            "email": email,
            "password": ADMIN_PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return response.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _change_a_setting(company_id, seconds):
    from backend.services.company_settings_service import company_settings_service

    company_settings_service.update_section(
        company_id,
        "ai_behavior",
        {"collect_message_delay_seconds": seconds},
        None,
    )


def _make_customer(company_id, **fields):
    from backend.services.customer_service import customer_service

    customer = customer_service.upsert_from_channel(
        company_id=company_id,
        channel="messenger",
        external_user_id="cust-1",
        display_name=fields.get("display_name", "Original Name"),
        profile_picture=None,
        username=None,
    )

    return customer["id"]


# --------------------------------------------------------------- the settings


def test_a_settings_change_can_be_read_back_with_its_old_value(wired, alpha):
    """The property the table was written for and never had."""
    _change_a_setting(alpha["id"], 30)
    _change_a_setting(alpha["id"], 45)

    history = wired.settings_history(company_id=alpha["id"], section="ai_behavior")

    assert len(history) == 2
    assert history[0]["after"]["collect_message_delay_seconds"] == 45
    assert history[0]["before"]["collect_message_delay_seconds"] == 30


def test_the_history_is_newest_first(wired, alpha):
    """An owner asking what a setting used to be wants the last change, not the
    first one ever made."""
    for seconds in (25, 35, 55):
        _change_a_setting(alpha["id"], seconds)

    history = wired.settings_history(company_id=alpha["id"], section="ai_behavior")

    assert [item["after"]["collect_message_delay_seconds"] for item in history] == [
        55,
        35,
        25,
    ]


def test_only_the_section_asked_for_comes_back(wired, alpha):
    _change_a_setting(alpha["id"], 30)

    assert wired.settings_history(company_id=alpha["id"], section="notifications") == []


def test_a_settings_history_stops_at_the_company_that_owns_it(wired, alpha, beta):
    """Each company's audit lives in its own encrypted database, so this cannot
    cross by construction — which is exactly why it is asserted rather than
    assumed. A future reader taking a company id from a parameter would break
    it silently."""
    _change_a_setting(alpha["id"], 45)

    assert wired.settings_history(company_id=beta["id"], section="ai_behavior") == []


# --------------------------------------------------------------- the customer


def test_a_customer_edit_can_be_read_back(wired, alpha):
    from backend.services.customer_service import customer_service

    customer_id = _make_customer(alpha["id"])

    customer_service.update_customer(
        company_id=alpha["id"],
        customer_id=customer_id,
        values={"display_name": "Corrected Name"},
        actor_user_id=None,
    )

    history = wired.customer_history(company_id=alpha["id"], customer_id=customer_id)

    assert len(history) == 1
    assert history[0]["action"] == "customer_updated"
    assert history[0]["changed"]["display_name"] == "Corrected Name"


def test_one_customers_history_is_not_anothers(wired, alpha):
    from backend.services.customer_service import customer_service

    first = _make_customer(alpha["id"])

    customer_service.update_customer(
        company_id=alpha["id"],
        customer_id=first,
        values={"display_name": "Corrected"},
        actor_user_id=None,
    )

    assert wired.customer_history(
        company_id=alpha["id"], customer_id=first + 999
    ) == []


# ------------------------------------------------------------------ the limit


def test_the_limit_is_bounded_however_it_is_asked_for(wired, alpha):
    """A history is unbounded by nature. A caller asking for a million rows of
    a company's own encrypted database should not get them."""
    for seconds in range(20, 30):
        _change_a_setting(alpha["id"], seconds)

    assert (
        len(
            wired.settings_history(
                company_id=alpha["id"], section="ai_behavior", limit=10_000
            )
        )
        <= 200
    )
    assert (
        len(
            wired.settings_history(
                company_id=alpha["id"], section="ai_behavior", limit=0
            )
        )
        >= 1
    )


# ------------------------------------------------------------- unreadable rows


def test_a_row_that_will_not_parse_does_not_take_the_history_down(wired, alpha):
    """The history is most needed when something is wrong, which is exactly
    when a row is most likely to be malformed."""
    from database.manager import database_manager, utc_now_iso

    _change_a_setting(alpha["id"], 30)

    with database_manager.tenant(alpha["id"]) as conn:
        conn.execute(
            """
            INSERT INTO company_setting_audit (
                company_id, section, actor_user_id, old_value_json,
                new_value_json, created_at
            ) VALUES (?, 'ai_behavior', NULL, 'not json at all', '{{{', ?)
            """,
            (alpha["id"], utc_now_iso()),
        )
        conn.commit()

    history = wired.settings_history(company_id=alpha["id"], section="ai_behavior")

    assert len(history) == 2
    assert history[0]["before"] is None
    assert history[0]["after"] is None


# --------------------------------------------------------------- the retention


def test_the_detail_is_pruned_on_the_same_clock_as_the_entry(wired, alpha):
    """Nothing pruned these two tables. Keeping the values longer than the
    record of who changed them would leave a company holding old phone numbers
    with no trace of who touched them; keeping them for less would leave a log
    entry pointing at a detail that is gone."""
    from database.manager import database_manager

    _change_a_setting(alpha["id"], 30)

    # A real customer, because `customer_audit` declares a foreign key to one.
    # Inventing an id here would fail on the constraint rather than testing the
    # retention, which is how the first version of this test was written.
    customer_id = _make_customer(alpha["id"])

    with database_manager.tenant(alpha["id"]) as conn:
        conn.execute(
            """
            INSERT INTO company_setting_audit (
                company_id, section, actor_user_id, old_value_json,
                new_value_json, created_at
            ) VALUES (?, 'ai_behavior', NULL, '{}', '{}', '2019-01-01T00:00:00+00:00')
            """,
            (alpha["id"],),
        )
        conn.execute(
            """
            INSERT INTO customer_audit (
                company_id, customer_id, actor_user_id, action, data_json,
                created_at
            ) VALUES (?, ?, NULL, 'customer_updated', '{}',
                      '2019-01-01T00:00:00+00:00')
            """,
            (alpha["id"], customer_id),
        )
        conn.commit()

    removed = wired.prune(alpha["id"])

    assert removed["company_setting_audit"] == 1
    assert removed["customer_audit"] == 1

    # And the recent one survived.
    assert len(wired.settings_history(company_id=alpha["id"], section="ai_behavior")) == 1


# ------------------------------------------------------------- the permissions


def test_the_values_need_more_than_permission_to_read_the_log(
    client, platform, alpha
):
    """An employee who may read the log may not read what a setting used to be.
    The log names the keys; the detail names the values.

    `manager` on purpose: it is the seeded role that holds `settings.view` and
    not `settings.manage`, so it can open the log and must not be able to open
    this. The first version of this test used `agent`, which holds neither —
    it returned 403 whichever permission the endpoint asked for, and passed
    with the guard weakened to `settings.view`. Found by mutation.
    """
    viewer = _token(client, platform, alpha, "viewer@alpha.example.com", "manager")

    assert client.get(
        "/api/activity", headers=_headers(viewer)
    ).status_code == 200, "manager should be able to read the log itself"

    refused = client.get(
        "/api/activity/settings/ai_behavior/history", headers=_headers(viewer)
    )

    assert refused.status_code == 403


def test_an_administrator_can_read_it(client, platform, alpha):
    owner = _token(client, platform, alpha, "owner@alpha.example.com", "owner")

    allowed = client.get(
        "/api/activity/settings/ai_behavior/history", headers=_headers(owner)
    )

    assert allowed.status_code == 200, allowed.text
    assert "items" in allowed.json()


def test_the_endpoint_never_takes_a_company_from_the_caller(client, platform, alpha):
    """The company comes from the session. A history endpoint is the last place
    that should accept one from the person reading it."""
    import backend.api.routes.activity as module

    source = module.__file__

    with open(source) as handle:
        text = handle.read()

    assert "company_id: int = Query" not in text
    assert "_company(current_user)" in text
