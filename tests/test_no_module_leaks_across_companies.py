"""One question, asked of every module: can a company reach another's row by id?

Ids on this platform are integers assigned per table. Nothing about `42` says
which company owns it, so every endpoint that takes an id from the URL has to
check ownership itself. There are dozens of them, written by different hands at
different times, and a single one that forgets is a cross-tenant read.

This is the failure this platform is built to prevent, and it has happened
here before — `channel_accounts.branch_id` accepted another company's branch
and three joins displayed its name. That one was found by reading. Reading
does not scale to a hundred and forty-one routes.

So the sweep is mechanical, and it runs both ways. For each module: Beta's
owner creates a real resource through the real API, then

* **Beta's owner can reach it** — the positive control. Without it a `404`
  proves nothing, because a `404` is also what you get for a row that was
  never created. Every finding below would be a false alarm and every clean
  result a false comfort.
* **Alpha's owner cannot** — 403 or 404, never 200, and never 500.

Both owners hold every permission in their own company. Permissions are not
what is being tested here; ownership is. An employee who is fully entitled to
read products *in their own company* is exactly the attacker this check is
about, because they are the one whose token opens the door.

**What mutation showed about this file, and it matters.** The resources here
divide into two kinds, and only one of them can actually fail:

* Rows in a company's own encrypted database — products, knowledge, tasks,
  team chat, customers, comments. Deleting `AND company_id = ?` from the
  lookup **does not** make this file fail, because the query runs against a
  different file: Alpha's connection cannot see Beta's rows at all. The
  `company_id` column there is defence in depth, not the control.
* Rows in the shared control database — channel accounts, branches, users,
  roles. An id from another company is a real row on the same connection, so
  the `WHERE` clause *is* the only control. Deleting it from either the branch
  lookup or the channel-account lookup makes this file fail immediately, which
  was verified rather than assumed.

That is the whole risk surface for reading another company by id, and it is
worth stating plainly instead of letting twenty-five green ticks imply the
same strength everywhere. The platform's real tenant isolation is the
separate encrypted file; the control plane is where care is required, and it
is where this platform has already had a leak (`channel_accounts.branch_id`).
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def app_client(platform, monkeypatch):
    """Every customer-facing router, wired to this test's databases."""
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import (
        activity,
        ai_teaching,
        appointments,
        auth,
        catalogue,
        channels,
        comments,
        company_settings,
        conversations,
        customers,
        knowledge,
        notifications,
        roles,
        scheduler,
        team_chat,
        tickets,
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
    ), "auth_service is not talking to the test database"

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    for module in (
        auth, activity, ai_teaching, appointments, catalogue, channels,
        comments, company_settings, conversations, customers, knowledge,
        notifications, roles, scheduler, team_chat,
    ):
        app.include_router(module.router)

    app.include_router(tickets.router)
    app.include_router(tickets.tasks_router)

    return TestClient(app, raise_server_exceptions=False)


def _owner(platform, company, app_client, email):
    """An owner of `company`, signed in, holding every permission it has."""
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email=email, password=PASSWORD, full_name=f"Owner of {company['name']}"
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (company["id"],),
        ).fetchone()

        assert role, f"{company['name']} has no owner role to grant"

        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
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

    return {
        "user_id": user_id,
        "company_id": company["id"],
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


@pytest.fixture()
def alpha_owner(platform, alpha, app_client):
    return _owner(platform, alpha, app_client, "owner@alpha.example.com")


@pytest.fixture()
def beta_owner(platform, beta, app_client):
    return _owner(platform, beta, app_client, "owner@beta.example.com")


# --------------------------------------------------------------------- the sweep


def _id_from(body):
    """Pull the new row's id out of whatever shape the route answers with.

    Four shapes are in use across the routers — a bare `id`, a wrapper keyed by
    the resource name, `{"success": true, "branch_id": n}`, and `{"item": ...}`.
    Rather than normalise the API for a test's convenience, the test reads all
    four and fails loudly on a fifth.
    """
    if not isinstance(body, dict):
        raise AssertionError(f"unexpected response shape: {body!r}")

    if "id" in body and isinstance(body["id"], int):
        return int(body["id"])

    for key, value in body.items():
        if isinstance(value, dict) and isinstance(value.get("id"), int):
            return int(value["id"])
        if key.endswith("_id") and isinstance(value, int):
            return int(value)

    raise AssertionError(f"could not find an id in {body!r}")


def _seed_customer(platform, company_id, name):
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].tenant(company_id) as conn:
        cursor = conn.execute(
            """
            INSERT INTO customers (
                company_id, display_name, phone, email,
                first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, '+9611234567', 'secret@beta.example', ?, ?, ?, ?)
            """,
            (company_id, name, now, now, now, now),
        )
        conn.commit()

    return int(cursor.lastrowid)


def _seed_comment(platform, company_id):
    """Comments arrive from Meta, so there is no create endpoint to call."""
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].tenant(company_id) as conn:
        cursor = conn.execute(
            """
            INSERT INTO post_comments (
                company_id, channel, provider_comment_id, post_id,
                author_name, message, status, created_at, updated_at
            )
            VALUES (?, 'messenger', 'CMT-BETA-1', 'POST-BETA-1',
                    'Beta Customer', 'Beta secret comment', 'open', ?, ?)
            """,
            (company_id, now, now),
        )
        conn.commit()

    return int(cursor.lastrowid)


# module -> (how Beta makes one, what Alpha must not be able to do to it).
#
# Written out rather than derived from the router. A generated list would
# silently shrink the day a route is renamed, and a security sweep that
# quietly stops covering something still reports success.
CASES = {
    "catalogue/product": (
        ("POST", "/api/catalogue/products",
         {"name": "Beta Secret Product", "price": 999, "status": "active"}),
        [
            ("GET", "/api/catalogue/products/{id}", None),
            ("PUT", "/api/catalogue/products/{id}", {"name": "Stolen"}),
            ("DELETE", "/api/catalogue/products/{id}", None),
        ],
    ),
    "catalogue/category": (
        ("POST", "/api/catalogue/categories", {"name": "Beta Secret Category"}),
        [
            ("PUT", "/api/catalogue/categories/{id}", {"name": "Stolen"}),
            ("DELETE", "/api/catalogue/categories/{id}", None),
        ],
    ),
    "knowledge": (
        ("POST", "/api/knowledge",
         {"title": "Beta Secret Fact", "content_ar": "سر بيتا"}),
        [
            ("GET", "/api/knowledge/{id}", None),
            ("PUT", "/api/knowledge/{id}", {"title": "Stolen"}),
            ("DELETE", "/api/knowledge/{id}", None),
        ],
    ),
    "team_chat": (
        ("POST", "/api/team-chat/channels",
         {"name": "beta-secret-room", "topic": "private"}),
        [
            ("GET", "/api/team-chat/channels/{id}", None),
            ("GET", "/api/team-chat/channels/{id}/members", None),
            ("GET", "/api/team-chat/channels/{id}/messages", None),
            ("POST", "/api/team-chat/channels/{id}/messages", {"body": "listening"}),
            ("POST", "/api/team-chat/channels/{id}/join", None),
        ],
    ),
    "branches": (
        ("POST", "/api/admin/access/branches",
         {"name": "Beta Secret Branch", "code": "BSB"}),
        [
            ("PATCH", "/api/admin/access/branches/{id}", {"name": "Stolen"}),
            ("DELETE", "/api/admin/access/branches/{id}", None),
        ],
    ),
    "scheduler": (
        ("POST", "/api/scheduler",
         {"channel": "messenger", "body": "Beta secret campaign",
          "scheduled_for": "2030-01-01T10:00:00Z"}),
        [
            ("GET", "/api/scheduler/{id}", None),
            ("PATCH", "/api/scheduler/{id}", {"body": "Stolen"}),
            ("POST", "/api/scheduler/{id}/approve", None),
            ("POST", "/api/scheduler/{id}/cancel", None),
        ],
    ),
    "tasks": (
        ("POST", "/api/tasks", {"title": "Beta secret task"}),
        [
            ("GET", "/api/tasks/{id}", None),
            ("PUT", "/api/tasks/{id}", {"title": "Stolen"}),
            ("PATCH", "/api/tasks/{id}/status", {"status": "closed"}),
            ("GET", "/api/tasks/{id}/comments", None),
            ("POST", "/api/tasks/{id}/comments", {"body": "listening"}),
        ],
    ),
    "ai_teaching/department": (
        ("POST", "/api/ai-teaching/departments",
         {"code": "beta_secret", "name_en": "Beta Secret", "name_ar": "سر"}),
        [
            ("GET", "/api/ai-teaching/departments/{id}", None),
            ("PUT", "/api/ai-teaching/departments/{id}", {"name_en": "Stolen"}),
            ("DELETE", "/api/ai-teaching/departments/{id}", None),
        ],
    ),
    "ai_teaching/profile": (
        ("POST", "/api/ai-teaching/profiles", {"name": "Beta Secret Persona"}),
        [
            ("GET", "/api/ai-teaching/profiles/{id}", None),
            ("PUT", "/api/ai-teaching/profiles/{id}", {"name": "Stolen"}),
            ("DELETE", "/api/ai-teaching/profiles/{id}", None),
        ],
    ),
    "channels": (
        ("POST", "/api/channels",
         {"channel": "messenger", "name": "Beta Secret Page",
          "page_id": "BETA-PAGE-1"}),
        [
            ("GET", "/api/channels/{id}", None),
            ("PATCH", "/api/channels/{id}", {"name": "Stolen"}),
            ("DELETE", "/api/channels/{id}", None),
        ],
    ),
}


def _make(app_client, owner, spec):
    verb, path, payload = spec
    response = app_client.request(verb, path, headers=owner["headers"], json=payload)

    assert response.status_code in (200, 201), (
        f"setup failed: {verb} {path} -> {response.status_code} {response.text}"
    )

    return _id_from(response.json())


def _probe(app_client, owner, method, path, payload):
    return app_client.request(method, path, headers=owner["headers"], json=payload)


@pytest.mark.parametrize("case", sorted(CASES), ids=sorted(CASES))
def test_alpha_cannot_touch_betas_row_by_id(
    app_client, alpha_owner, beta_owner, case
):
    """Alpha holds every permission — in Alpha. That is the point.

    The attacker worth modelling is not someone without a login; it is an
    ordinary employee with a valid token who changes a number in the URL.
    """
    create, probes = CASES[case]
    resource_id = _make(app_client, beta_owner, create)

    for method, template, payload in probes:
        path = template.format(id=resource_id)
        response = _probe(app_client, alpha_owner, method, path, payload)

        assert response.status_code != 200, (
            f"CROSS-TENANT ACCESS in {case}: Alpha succeeded at "
            f"{method} {path}\n{response.text}"
        )
        assert response.status_code < 500, (
            f"{case}: {method} {path} crashed with {response.status_code} "
            f"instead of refusing:\n{response.text}"
        )
        assert response.status_code in (400, 403, 404, 409, 422), (
            f"{case}: {method} {path} answered Alpha with "
            f"{response.status_code}, which is not a refusal:\n{response.text}"
        )


@pytest.mark.parametrize("case", sorted(CASES), ids=sorted(CASES))
def test_beta_can_reach_its_own_row(app_client, beta_owner, case):
    """The control, and the reason the test above means anything.

    A 404 is what you get for a row that was never created. Without this,
    every refusal above could be the sweep quietly testing nothing — which is
    the exact way a security check rots into decoration.
    """
    create, probes = CASES[case]
    resource_id = _make(app_client, beta_owner, create)

    method, template, payload = probes[0]
    path = template.format(id=resource_id)
    response = _probe(app_client, beta_owner, method, path, payload)

    assert response.status_code in (200, 201, 204), (
        f"{case}: the owner cannot {method} its own {path} "
        f"({response.status_code} {response.text}) — the refusals asserted for "
        "Alpha would prove nothing"
    )


# ------------------------------------------------------- rows with no create route


def test_a_customer_belonging_to_another_company_is_not_readable(
    app_client, alpha_owner, beta_owner, platform, beta
):
    """Customers arrive from a webhook, so they are seeded rather than posted."""
    customer_id = _seed_customer(platform, beta["id"], "Beta Secret Customer")

    mine = app_client.get(
        f"/api/customers/{customer_id}", headers=beta_owner["headers"]
    )

    assert mine.status_code == 200, mine.text
    assert mine.json()["display_name"] == "Beta Secret Customer"

    theirs = app_client.get(
        f"/api/customers/{customer_id}", headers=alpha_owner["headers"]
    )

    assert theirs.status_code != 200, (
        f"CROSS-TENANT READ: Alpha read Beta's customer\n{theirs.text}"
    )


def test_another_companys_customer_cannot_be_edited(
    app_client, alpha_owner, platform, beta
):
    customer_id = _seed_customer(platform, beta["id"], "Beta Secret Customer")

    response = app_client.put(
        f"/api/customers/{customer_id}",
        headers=alpha_owner["headers"],
        json={"display_name": "Stolen", "phone": "+000"},
    )

    assert response.status_code != 200, (
        f"CROSS-TENANT WRITE: Alpha edited Beta's customer\n{response.text}"
    )

    with platform["manager"].tenant(beta["id"]) as conn:
        name = conn.execute(
            "SELECT display_name FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()["display_name"]

    assert name == "Beta Secret Customer", "Beta's customer was renamed by Alpha"


def test_another_companys_customer_history_is_not_readable(
    app_client, alpha_owner, platform, beta
):
    """The history endpoint takes a customer id and reads the audit detail —
    a second door onto the same row, and one added late enough to be worth
    checking separately."""
    customer_id = _seed_customer(platform, beta["id"], "Beta Secret Customer")

    response = app_client.get(
        f"/api/activity/customers/{customer_id}/history",
        headers=alpha_owner["headers"],
    )

    assert response.status_code != 200 or response.json().get("items") == [], (
        f"CROSS-TENANT READ: Alpha read the history of Beta's customer\n"
        f"{response.text}"
    )


def test_another_companys_comment_is_not_readable(
    app_client, alpha_owner, beta_owner, platform, beta
):
    comment_id = _seed_comment(platform, beta["id"])

    mine = app_client.get(
        f"/api/comments/{comment_id}", headers=beta_owner["headers"]
    )

    assert mine.status_code == 200, mine.text

    theirs = app_client.get(
        f"/api/comments/{comment_id}", headers=alpha_owner["headers"]
    )

    assert theirs.status_code != 200, (
        f"CROSS-TENANT READ: Alpha read Beta's comment\n{theirs.text}"
    )


def test_another_companys_comment_cannot_be_replied_to(
    app_client, alpha_owner, platform, beta
):
    """Worse than a read: a reply goes out publicly under Beta's page."""
    comment_id = _seed_comment(platform, beta["id"])

    response = app_client.post(
        f"/api/comments/{comment_id}/reply",
        headers=alpha_owner["headers"],
        json={"message": "Posted by a stranger"},
    )

    assert response.status_code != 200, (
        f"CROSS-TENANT WRITE: Alpha replied publicly on Beta's comment\n"
        f"{response.text}"
    )
