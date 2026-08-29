"""The platform attacked on purpose, from the position an attacker really has.

Not "is the code careful" — that is what the rest of the suite asks. This file
takes a working account and tries to get more than it was given, because the
realistic attacker on a multi-tenant platform is not an anonymous stranger. It
is somebody who already signed in: a junior employee, a departing one, a
customer whose supplier shares the platform.

Each test states the attack, performs it against the real API, and asserts the
specific thing that must not happen. Where an attack fails, the assertion says
what it would have got — otherwise a green tick is just a green tick and nobody
can tell a closed door from a door nobody tried.

The accounts:

* `agent` — the lowest real role, thirteen permissions, no `users.manage`, no
  `settings.manage`. This is the disgruntled-employee account.
* `owner` — every permission in Alpha, and none anywhere else.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import (
        activity, ai_teaching, appointments, auth, catalogue, channels,
        comments, company_settings, conversations, customers, knowledge,
        notifications, platform as platform_routes, roles, scheduler,
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

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (
        auth, activity, ai_teaching, appointments, catalogue, channels,
        comments, company_settings, conversations, customers, knowledge,
        notifications, platform_routes, roles, scheduler, team_chat,
    ):
        app.include_router(module.router)

    app.include_router(tickets.router)
    app.include_router(tickets.tasks_router)

    return TestClient(app, raise_server_exceptions=False)


def _account(platform, company, email, name, role_code):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(email=email, password=PASSWORD, full_name=name)

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = ?",
            (company["id"], role_code),
        ).fetchone()

        assert role, f"{company['name']} has no {role_code} role"

        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (company["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    return user_id


def _sign_in(app_client, company, email):
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
    return response.json()["access_token"]


@pytest.fixture()
def agent(platform, alpha, app_client):
    """The lowest real role in Alpha. The disgruntled-employee account."""
    user_id = _account(
        platform, alpha, "agent@alpha.example.com", "Alpha Agent", "agent"
    )
    token = _sign_in(app_client, alpha, "agent@alpha.example.com")

    return {
        "user_id": user_id,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.fixture()
def owner(platform, alpha, app_client):
    user_id = _account(
        platform, alpha, "owner@alpha.example.com", "Alpha Owner", "owner"
    )
    token = _sign_in(app_client, alpha, "owner@alpha.example.com")

    return {
        "user_id": user_id,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


# ==================================================== privilege escalation


def test_an_employee_cannot_promote_themselves(app_client, agent, platform, alpha):
    """The first thing anybody tries. `PATCH /users/{id}` sets a role, and the
    id in the path is the caller's own."""
    with platform["manager"].control() as conn:
        owner_role = int(
            conn.execute(
                "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
                (alpha["id"],),
            ).fetchone()["id"]
        )

    response = app_client.patch(
        f"/api/admin/access/users/{agent['user_id']}",
        headers=agent["headers"],
        json={"role_id": owner_role, "status": "active"},
    )

    assert response.status_code in (401, 403), (
        f"PRIVILEGE ESCALATION: an agent set their own role\n{response.text}"
    )

    with platform["manager"].control() as conn:
        current = int(
            conn.execute(
                "SELECT role_id FROM company_users WHERE user_id = ? AND company_id = ?",
                (agent["user_id"], alpha["id"]),
            ).fetchone()["role_id"]
        )

    assert current != owner_role, "the agent is now an owner"


def test_an_employee_cannot_add_permissions_to_their_own_role(
    app_client, agent, platform, alpha
):
    """The quieter version: leave the role alone and change what it means."""
    with platform["manager"].control() as conn:
        agent_role = int(
            conn.execute(
                "SELECT id FROM roles WHERE company_id = ? AND code = 'agent'",
                (alpha["id"],),
            ).fetchone()["id"]
        )

    response = app_client.patch(
        f"/api/admin/access/roles/{agent_role}",
        headers=agent["headers"],
        json={"permission_codes": ["settings.manage", "users.manage"]},
    )

    assert response.status_code in (401, 403), (
        f"PRIVILEGE ESCALATION: an agent rewrote its own role\n{response.text}"
    )


def test_an_employee_cannot_create_an_account_for_themselves(app_client, agent):
    """Making a second, more privileged account is escalation by another name."""
    response = app_client.post(
        "/api/admin/access/users",
        headers=agent["headers"],
        json={
            "email": "backdoor@alpha.example.com",
            "password": "BackdoorPass123!",
            "full_name": "Backdoor",
            "role_id": 1,
        },
    )

    assert response.status_code in (401, 403), (
        f"PRIVILEGE ESCALATION: an agent created a user\n{response.text}"
    )


def test_an_employee_cannot_reach_settings_they_may_only_view(app_client, agent):
    """`agent` has no settings permission at all — not even view."""
    read = app_client.get("/api/company-settings/ai_behavior", headers=agent["headers"])
    write = app_client.put(
        "/api/company-settings/ai_behavior",
        headers=agent["headers"],
        json={"values": {"tone": "hostile"}},
    )

    assert read.status_code in (401, 403), read.text
    assert write.status_code in (401, 403), (
        f"An agent rewrote the company's AI behaviour\n{write.text}"
    )


def test_an_employee_cannot_unlock_a_locked_account(app_client, agent, owner):
    """Unlocking is how a lockout is escaped. If any employee can do it, the
    lockout protects nobody."""
    response = app_client.post(
        f"/api/admin/access/users/{owner['user_id']}/unlock",
        headers=agent["headers"],
        json={},
    )

    assert response.status_code in (401, 403), response.text


def test_an_employee_cannot_trigger_a_password_reset_for_the_owner(
    app_client, agent, owner
):
    """A reset link sent to an address the attacker does not control is not
    itself a takeover — but it is a denial of service against the owner, and
    it revokes their sessions."""
    response = app_client.post(
        f"/api/admin/access/users/{owner['user_id']}/force-password-reset",
        headers=agent["headers"],
        json={},
    )

    assert response.status_code in (401, 403), response.text


# ==================================================== crossing into the console


def test_a_company_session_cannot_reach_the_platform_console(app_client, owner):
    """Two scopes, one token format. An owner is the most privileged person in
    their company and must be nobody at all in the console."""
    for path in (
        "/api/platform/companies",
        "/api/platform/overview",
        "/api/platform/plans",
    ):
        response = app_client.get(path, headers=owner["headers"])

        assert response.status_code in (401, 403, 404), (
            f"SCOPE CROSSING: a company owner reached {path}\n{response.text}"
        )


def test_a_company_session_cannot_suspend_a_company(app_client, owner, beta):
    response = app_client.post(
        f"/api/platform/companies/{beta['id']}/suspend",
        headers=owner["headers"],
        json={"reason": "because I can"},
    )

    assert response.status_code in (401, 403, 404), (
        f"SCOPE CROSSING: a company owner suspended another company\n{response.text}"
    )


# ==================================================== tokens


def test_a_tampered_token_is_refused(app_client, owner):
    """The tokens are opaque and stored as hashes, so flipping a character
    should simply not match anything."""
    broken = owner["token"][:-4] + ("aaaa" if owner["token"][-4:] != "aaaa" else "bbbb")

    response = app_client.get(
        "/api/company-settings", headers={"Authorization": f"Bearer {broken}"}
    )

    assert response.status_code == 401, (
        f"a modified token was accepted\n{response.text}"
    )


def test_no_token_is_refused(app_client):
    response = app_client.get("/api/company-settings")

    assert response.status_code in (401, 403), response.text


def test_a_signed_out_token_stops_working(app_client, owner):
    """Signing out must revoke, not merely forget."""
    out = app_client.post("/api/auth/logout", headers=owner["headers"])

    assert out.status_code in (200, 204), out.text

    after = app_client.get("/api/company-settings", headers=owner["headers"])

    assert after.status_code == 401, (
        f"a token kept working after sign-out\n{after.text}"
    )


def test_an_empty_bearer_is_refused(app_client):
    for value in ("Bearer ", "Bearer", "", "Bearer null", "Bearer undefined"):
        response = app_client.get("/api/company-settings", headers={"Authorization": value})

        assert response.status_code in (401, 403), (
            f"Authorization: {value!r} was accepted\n{response.text}"
        )


# ==================================================== injection


SQL_PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE customers; --",
    "\\'; DELETE FROM products WHERE '1'='1",
    "1 UNION SELECT password_hash FROM users",
    "%' OR name LIKE '%",
]


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_a_search_box_does_not_execute_what_is_typed_into_it(
    app_client, owner, platform, alpha, payload
):
    """Search terms reach a LIKE clause. If they are concatenated rather than
    bound, the first of these returns every row and the second destroys the
    table."""
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.execute(
            """
            INSERT INTO customers (
                company_id, display_name, first_seen_at, last_seen_at,
                created_at, updated_at
            )
            VALUES (?, 'Ordinary Customer', ?, ?, ?, ?)
            """,
            (alpha["id"], now, now, now, now),
        )
        conn.commit()

    response = app_client.get(
        "/api/customers", headers=owner["headers"], params={"search": payload}
    )

    assert response.status_code < 500, (
        f"search payload {payload!r} crashed the endpoint\n{response.text}"
    )

    # The table must still be there afterwards, whatever the query did.
    with platform["manager"].tenant(alpha["id"]) as conn:
        remaining = conn.execute("SELECT COUNT(*) AS n FROM customers").fetchone()["n"]

    assert int(remaining) == 1, (
        f"search payload {payload!r} changed the customers table: "
        f"{remaining} rows left"
    )


def test_a_sort_or_filter_value_cannot_name_a_column(app_client, owner):
    """Filters that reach an ORDER BY or a WHERE by name rather than by value
    are the other injection door, and one that parameterisation does not
    close."""
    for value in ("id; DROP TABLE products", "(SELECT password_hash FROM users)"):
        response = app_client.get(
            "/api/activity", headers=owner["headers"], params={"kind": value}
        )

        assert response.status_code < 500, (
            f"filter {value!r} crashed the endpoint\n{response.text}"
        )


# ==================================================== secrets in responses


# Exact key names that must never appear in a response body. Matched as JSON
# *keys*, not as substrings of the text — the first version of this test
# searched the raw body for "app_secret" and failed on `"has_app_secret": false`,
# which is the deliberate, correct way the API reports that a secret is set
# without sending it. A substring check that flags the safe design as a leak is
# worse than no check: it gets muted.
SECRET_KEYS = frozenset({
    "password_hash",
    "token_hash",
    "access_token_sealed",
    "verify_token_sealed",
    "app_secret_sealed",
    "bot_token_sealed",
    "access_token",
    "app_secret",
    "verify_token",
    "bot_token",
    "workspace_code",
    "master_key",
    "encrypted_key",
})

# Values planted in the database before the sweep. If any of these strings
# comes back under any key at all, the value itself escaped — which no key
# whitelist would catch.
PLANTED = ("SEALED-APP-SECRET", "SEALED-ACCESS-TOKEN", "SEALED-VERIFY-TOKEN")


def _walk_keys(node, path="$"):
    """Every (json path, key, value) in a decoded response."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", key, value
            yield from _walk_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_keys(value, f"{path}[{index}]")


def test_no_response_carries_a_secret(app_client, owner, platform, alpha):
    """Every screen the owner can open, checked two ways.

    `workspace_code` is on the key list because it is the second factor of the
    company's sign-in — it unlocks the tenant key, so a screen that echoes it
    turns a stolen password into a full sign-in.
    """
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].control() as conn:
        conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, channel, name, page_id, status,
                app_secret_sealed, access_token_sealed, verify_token_sealed,
                created_at, updated_at
            )
            VALUES (?, 'messenger', 'Alpha Page', 'ALPHA-1', 'active',
                    'SEALED-APP-SECRET', 'SEALED-ACCESS-TOKEN',
                    'SEALED-VERIFY-TOKEN', ?, ?)
            """,
            (alpha["id"], now, now),
        )
        conn.commit()

    paths = [
        "/api/channels",
        "/api/company-settings",
        "/api/admin/access/overview",
        "/api/customers",
        "/api/knowledge",
        "/api/activity",
        "/api/notifications",
        "/api/team-chat/overview",
        "/api/team-chat/directory",
        "/api/ai-teaching/profile",
        "/api/ai-teaching/profiles",
        "/api/catalogue/products",
        "/api/scheduler",
        "/api/appointments",
        "/api/tasks",
        "/api/comments",
    ]

    reached = 0
    leaks = []

    for path in paths:
        response = app_client.get(path, headers=owner["headers"])

        if response.status_code != 200:
            continue

        reached += 1

        for value in PLANTED:
            if value in response.text:
                leaks.append(f"{path} returned the sealed value {value}")

        try:
            body = response.json()
        except ValueError:
            continue

        for where, key, _ in _walk_keys(body):
            if key in SECRET_KEYS:
                leaks.append(f"{path} returned the key {key} at {where}")

    # Without this the sweep would pass by reaching nothing.
    assert reached >= 10, (
        f"only {reached} of {len(paths)} screens answered 200 — the sweep is "
        "not covering what it claims to"
    )

    assert not leaks, "A response carries a secret:\n  " + "\n  ".join(leaks)


def test_the_secret_sweep_would_notice_a_leak(app_client, owner, platform, alpha):
    """The control for the sweep above.

    A whitelist of key names is exactly the kind of check that quietly stops
    matching. This plants a response-shaped object containing a sealed value
    and asserts the walker finds it, so a refactor that breaks the walker
    fails here rather than turning the sweep into decoration.
    """
    planted = {
        "items": [
            {"id": 1, "name": "Page", "has_app_secret": True},
            {"id": 2, "nested": {"app_secret_sealed": "SEALED-APP-SECRET"}},
        ]
    }

    found = [key for _, key, _ in _walk_keys(planted) if key in SECRET_KEYS]

    assert found == ["app_secret_sealed"], (
        f"the walker found {found} — it must find the sealed column and must "
        "not flag `has_app_secret`, which is how the API correctly says a "
        "secret is set without sending it"
    )


# ==================================================== user enumeration


def test_a_wrong_password_and_a_missing_account_answer_the_same(app_client, alpha):
    """Different answers turn the sign-in form into a list of who works here."""
    real = app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "owner@alpha.example.com",
            "password": "definitely-not-the-password",
        },
    )
    fake = app_client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "nobody-here@alpha.example.com",
            "password": "definitely-not-the-password",
        },
    )

    assert real.status_code == fake.status_code, (
        f"a real address answers {real.status_code} and an unknown one "
        f"{fake.status_code} — the form enumerates employees"
    )
    assert real.json() == fake.json(), (
        f"the two answers differ in body:\n{real.text}\n{fake.text}"
    )


# ==================================================== mass assignment


def test_extra_fields_in_a_payload_are_not_written(app_client, owner, platform, alpha):
    """Sending a field the form never shows is the cheapest attack there is.

    `company_id` is the one that matters: a product that accepted it could be
    written straight into another company's catalogue.
    """
    response = app_client.post(
        "/api/catalogue/products",
        headers=owner["headers"],
        json={
            "name": "Trojan",
            "price": 1,
            "status": "active",
            "company_id": 999,
            "id": 4242,
        },
    )

    assert response.status_code < 500, response.text

    if response.status_code in (200, 201):
        with platform["manager"].tenant(alpha["id"]) as conn:
            row = conn.execute(
                "SELECT id, company_id FROM products WHERE name = 'Trojan'"
            ).fetchone()

        assert row is not None, "the product was reported created and is not there"
        assert int(row["company_id"]) == alpha["id"], (
            "a payload field set the owning company"
        )
        assert int(row["id"]) != 4242, "a payload field set the primary key"
