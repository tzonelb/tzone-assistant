"""Tests for the console endpoints that manage plans, allowances and usage.

The three seeded plans were the only ones there could ever be. They were
written with `INSERT OR IGNORE` at first boot, and no endpoint created or
changed one — so the commercial offer was frozen at whatever shipped, and the
only way to adjust a company's ceiling was to edit the database by hand.

There was also no way to accommodate one customer without moving everybody on
their plan, and nothing anywhere reported what a company had actually used.
"""

from __future__ import annotations

import pytest

from tests.test_platform_admin import (  # noqa: F401  (fixtures)
    _bearer,
    _employ,
    _make_admin,
    _make_user,
    _platform_token,
    client,
    service,
)


@pytest.fixture()
def token(client):
    _make_admin()

    return _platform_token(client)


# ----------------------------------------------------------------------- plans


def test_a_plan_can_be_created(client, token):
    response = client.post(
        "/api/platform/plans",
        headers=_bearer(token),
        json={
            "code": "agency",
            "name": "Agency",
            "values": {
                "price_monthly": 149,
                "max_users": 25,
                "max_channel_accounts": 15,
                "voice_ai_enabled": True,
            },
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "agency"
    assert body["max_users"] == 25
    assert body["voice_ai_enabled"] == 1


def test_a_duplicate_plan_code_is_refused(client, token):
    response = client.post(
        "/api/platform/plans",
        headers=_bearer(token),
        json={"code": "starter", "name": "Another Starter", "values": {}},
    )

    assert response.status_code in (400, 409), response.text


def test_an_unknown_plan_field_is_refused_rather_than_dropped(client, token):
    """A stored typo looks like a setting that was applied. The operator would
    believe they had raised a limit that never moved."""
    response = client.post(
        "/api/platform/plans",
        headers=_bearer(token),
        json={"code": "typo", "name": "Typo", "values": {"max_user": 5}},
    )

    assert response.status_code == 400, response.text
    assert "max_user" in response.text


def test_a_negative_allowance_is_refused(client, token):
    response = client.post(
        "/api/platform/plans",
        headers=_bearer(token),
        json={"code": "negative", "name": "Negative", "values": {"max_users": -1}},
    )

    assert response.status_code == 400, response.text


def test_a_plan_can_be_edited(client, token):
    response = client.patch(
        "/api/platform/plans/starter",
        headers=_bearer(token),
        json={"values": {"max_users": 4}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["max_users"] == 4


def test_editing_an_unknown_plan_is_a_404(client, token):
    response = client.patch(
        "/api/platform/plans/nope",
        headers=_bearer(token),
        json={"values": {"max_users": 4}},
    )

    assert response.status_code == 404, response.text


def test_the_plan_list_says_how_many_companies_each_one_carries(
    client, token, alpha
):
    """So an operator editing a ceiling can see how many businesses the edit
    moves before making it."""
    client.post(
        f"/api/platform/companies/{alpha['id']}/plan",
        headers=_bearer(token),
        json={"plan_code": "starter"},
    )

    plans = client.get("/api/platform/plans", headers=_bearer(token)).json()["items"]
    starter = next(plan for plan in plans if plan["code"] == "starter")

    assert starter["companies"] == 1


def test_a_plan_edit_is_written_to_the_audit_log(client, token):
    client.patch(
        "/api/platform/plans/starter",
        headers=_bearer(token),
        json={"values": {"max_users": 9}},
    )

    entries = client.get("/api/platform/audit", headers=_bearer(token)).json()["items"]
    entry = next(item for item in entries if item["action"] == "plan.updated")

    # Before and after, so a review can see what the ceiling used to be rather
    # than only that it changed.
    assert entry["data"]["before"] == {"max_users": 2}
    assert entry["data"]["after"] == {"max_users": 9}


# ------------------------------------------------------------------- overrides


def test_an_override_changes_one_company_only(client, token, alpha, beta):
    for company in (alpha, beta):
        client.post(
            f"/api/platform/companies/{company['id']}/plan",
            headers=_bearer(token),
            json={"plan_code": "starter"},
        )

    response = client.put(
        f"/api/platform/companies/{alpha['id']}/limits/max_users",
        headers=_bearer(token),
        json={"value": 12, "note": "Migrating from another tool"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["limits"]["max_users"] == 12

    other = client.get(
        f"/api/platform/companies/{beta['id']}/limits", headers=_bearer(token)
    ).json()

    assert other["limits"]["max_users"] == 2


def test_the_limits_view_says_where_each_number_came_from(client, token, alpha):
    client.post(
        f"/api/platform/companies/{alpha['id']}/plan",
        headers=_bearer(token),
        json={"plan_code": "starter"},
    )
    client.put(
        f"/api/platform/companies/{alpha['id']}/limits/max_users",
        headers=_bearer(token),
        json={"value": 12},
    )

    body = client.get(
        f"/api/platform/companies/{alpha['id']}/limits", headers=_bearer(token)
    ).json()

    assert body["sources"]["max_users"] == "override"
    assert body["sources"]["max_knowledge_items"] == "plan"


def test_clearing_an_override_returns_the_company_to_its_plan(client, token, alpha):
    client.post(
        f"/api/platform/companies/{alpha['id']}/plan",
        headers=_bearer(token),
        json={"plan_code": "starter"},
    )
    client.put(
        f"/api/platform/companies/{alpha['id']}/limits/max_users",
        headers=_bearer(token),
        json={"value": 12},
    )

    response = client.delete(
        f"/api/platform/companies/{alpha['id']}/limits/max_users",
        headers=_bearer(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["limits"]["max_users"] == 2


def test_an_unknown_limit_key_is_refused(client, token, alpha):
    response = client.put(
        f"/api/platform/companies/{alpha['id']}/limits/max_seats",
        headers=_bearer(token),
        json={"value": 5},
    )

    assert response.status_code == 400, response.text


def test_an_override_is_written_to_the_audit_log(client, token, alpha):
    client.put(
        f"/api/platform/companies/{alpha['id']}/limits/max_users",
        headers=_bearer(token),
        json={"value": 12, "note": "Migrating from another tool"},
    )

    entries = client.get("/api/platform/audit", headers=_bearer(token)).json()["items"]

    assert any(item["action"] == "company.limit_override_set" for item in entries)


# ----------------------------------------------------------------------- usage


def test_the_console_reports_a_company_usage(client, token, alpha):
    from backend.services.plan_service import plan_service

    plan_service.record_usage(
        company_id=alpha["id"], metric="ai_replies", channel="messenger"
    )

    body = client.get(
        f"/api/platform/companies/{alpha['id']}/usage", headers=_bearer(token)
    ).json()

    assert body["ai_replies"] == 1
    assert body["breakdown"][0]["channel"] == "messenger"


def test_usage_carries_no_message_content(client, token, alpha):
    """Numbers only. The counters record a channel and a department and never a
    word of what was said, so nothing here can leak a conversation."""
    from backend.services.plan_service import plan_service

    plan_service.record_usage(
        company_id=alpha["id"], metric="ai_replies", channel="messenger"
    )

    body = client.get(
        f"/api/platform/companies/{alpha['id']}/usage", headers=_bearer(token)
    ).text

    for column in ("text", "message", "body", "content"):
        assert f'"{column}"' not in body


# ------------------------------------------------------------- authorisation


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/platform/plans"),
        ("patch", "/api/platform/plans/starter"),
        ("get", "/api/platform/companies/1/limits"),
        ("put", "/api/platform/companies/1/limits/max_users"),
        ("delete", "/api/platform/companies/1/limits/max_users"),
        ("get", "/api/platform/companies/1/usage"),
    ],
)
def test_none_of_this_is_reachable_without_a_platform_token(client, method, path):
    call = getattr(client, method)
    response = (
        call(path)
        if method in ("get", "delete")
        else call(path, json={"value": 1, "code": "xx", "name": "x"})
    )

    assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"


def test_a_company_token_cannot_reach_the_console(client, service, platform, alpha):
    """A company owner must not be able to raise their own ceiling."""
    user_id = _make_user("owner@alpha.example.com", "CompanyPass123!")
    _employ(platform, alpha, user_id)

    login = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "owner@alpha.example.com",
            "password": "CompanyPass123!",
        },
    )
    assert login.status_code == 200, login.text

    response = client.put(
        f"/api/platform/companies/{alpha['id']}/limits/max_users",
        headers=_bearer(login.json()["access_token"]),
        json={"value": 999},
    )

    assert response.status_code in (401, 403), response.text
