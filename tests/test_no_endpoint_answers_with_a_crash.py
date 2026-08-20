"""No route answers a bad request with a 500.

A 500 is two failures at once. It is a bug — the handler met something it did
not expect and fell over instead of deciding — and it is a disclosure, because
an unhandled exception is the one response nobody wrote on purpose and the one
most likely to carry a stack frame, a file path or a query fragment.

It is also the failure that survives review best. Every route here is
individually tested for what it does when it is used correctly. Almost none is
tested for what it does when a path parameter is a word, a page number is
negative, a filter names a value that is not in the enumeration, or an id is
the largest integer SQLite can hold. Those are not attacks so much as a browser
with a stale tab and a user who edits the URL.

So: walk the router table, and for every route with an id or a query in it,
send the shapes that a careless caller really produces. The bar is not that
each is accepted — most should be refused. The bar is that the refusal is a
decision the code made (4xx) rather than one it fell into (5xx).

Coverage is asserted rather than assumed: if the sweep reaches fewer routes
than it should, it fails on that instead of quietly reporting a clean run.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"

# Values a real caller produces by accident, not values invented to be exotic:
# a word where a number goes (a stale link), a negative or zero id (an
# off-by-one in a loop), a huge id (a copied timestamp), and the two SQLite
# integer edges.
NASTY_IDS = ["abc", "-1", "0", "999999999", "9223372036854775807", "1.5", "%20"]

NASTY_QUERIES = [
    {"limit": "-5"},
    {"limit": "0"},
    {"limit": "999999999"},
    {"limit": "abc"},
    {"offset": "-1"},
    {"offset": "999999999"},
    {"status": "not-a-status"},
    {"channel": "not-a-channel"},
    {"search": "%"},
    {"search": "\\x00"},
    {"date": "not-a-date"},
    {"from": "9999-99-99"},
]


@pytest.fixture()
def app_client(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    from backend.api.routes import (
        activity, ai_teaching, analytics, appointments, auth, catalogue,
        channels, comments, company_settings, conversation_tags, conversations,
        customers, dashboard, knowledge, notifications, roles, scheduler,
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
        auth, activity, ai_teaching, analytics, appointments, catalogue,
        channels, comments, company_settings, conversation_tags, conversations,
        customers, dashboard, knowledge, notifications, roles, scheduler,
        team_chat,
    ):
        app.include_router(module.router)

    app.include_router(tickets.router)
    app.include_router(tickets.tasks_router)

    # `raise_server_exceptions=False` matters: without it an unhandled
    # exception is re-raised into the test instead of becoming the 500 a real
    # caller would receive, and the sweep would stop at the first one rather
    # than reporting all of them.
    return TestClient(app, raise_server_exceptions=False), app


@pytest.fixture()
def owner(platform, alpha, app_client):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    client, _ = app_client

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

    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _paths(app):
    """Every route, from the OpenAPI schema rather than from `app.routes`.

    The first version of this walked `app.routes` and found **zero** — this
    FastAPI wraps an included router in an opaque object instead of flattening
    its routes into the application. Three of the four tests below then swept
    an empty list and passed, which is why the coverage assertion exists and
    why it is the first test in the file.
    """
    return app.openapi().get("paths", {})


def _get_routes(app):
    """Every GET route, split into the ones with parameters and the ones without."""
    plain, parameterised = [], []

    for path, operations in _paths(app).items():
        if "get" not in operations:
            continue

        if not path.startswith(("/api/", "/conversations")):
            continue

        # Server-sent events never return; they would hang the sweep.
        if path.endswith("/live/events"):
            continue

        if "{" in path:
            parameterised.append(path)
        else:
            plain.append(path)

    return plain, parameterised


def _fill(path, value):
    """Substitute every path parameter with the same nasty value."""
    import re

    return re.sub(r"\{[^}]+\}", value, path)


def test_the_sweep_actually_reaches_the_routes(app_client, owner):
    """Without this the file passes by testing nothing.

    A renamed prefix, a router left out of the fixture, or a filter that is too
    narrow would all produce an empty sweep and a green tick.
    """
    client, app = app_client
    plain, parameterised = _get_routes(app)

    assert len(plain) >= 15, f"only {len(plain)} plain GET routes found"
    assert len(parameterised) >= 15, (
        f"only {len(parameterised)} parameterised GET routes found"
    )

    answered = sum(
        1
        for path in plain
        if client.get(path, headers=owner).status_code == 200
    )

    assert answered >= 10, (
        f"only {answered} of {len(plain)} plain routes answered 200 — the "
        "sweep below is not exercising a working application"
    )


def test_no_route_crashes_on_a_nonsense_path_parameter(app_client, owner):
    """A word where an id goes. This is a stale link, not an attack."""
    client, app = app_client
    _, parameterised = _get_routes(app)

    crashes = []

    for path in parameterised:
        for value in NASTY_IDS:
            target = _fill(path, value)
            response = client.get(target, headers=owner)

            if response.status_code >= 500:
                crashes.append(
                    f"GET {target} -> {response.status_code}: {response.text[:200]}"
                )

    assert not crashes, (
        f"{len(crashes)} route(s) crashed on a path parameter:\n  "
        + "\n  ".join(crashes[:20])
    )


def test_no_route_crashes_on_a_nonsense_query_string(app_client, owner):
    """A negative page, a limit of nine hundred million, a filter naming a
    status that does not exist."""
    client, app = app_client
    plain, _ = _get_routes(app)

    crashes = []

    for path in plain:
        for params in NASTY_QUERIES:
            response = client.get(path, headers=owner, params=params)

            if response.status_code >= 500:
                crashes.append(
                    f"GET {path} {params} -> {response.status_code}: "
                    f"{response.text[:200]}"
                )

    assert not crashes, (
        f"{len(crashes)} route(s) crashed on a query string:\n  "
        + "\n  ".join(crashes[:20])
    )


def test_no_route_crashes_on_an_empty_or_malformed_body(app_client, owner):
    """Every POST, PUT and PATCH, sent nothing and then sent rubbish.

    A body-less POST is what a form does when JavaScript fails halfway; a body
    of the wrong *shape* is what an old client version sends after the schema
    changes. Both should be a 422, never a 500.
    """
    client, app = app_client

    crashes = []

    for path, operations in _paths(app).items():
        if not path.startswith(("/api/", "/conversations")):
            continue

        for method in ("POST", "PUT", "PATCH"):
            if method.lower() not in operations:
                continue

            # Signing in on purpose would lock the account out mid-sweep.
            if "login" in path:
                continue

            target = _fill(path, "1")

            for body in (None, {}, [], "not json at all", {"unexpected": "field"}):
                response = client.request(
                    method,
                    target,
                    headers=owner,
                    json=body if body != "not json at all" else None,
                    content=b"not json at all" if body == "not json at all" else None,
                )

                if response.status_code >= 500:
                    crashes.append(
                        f"{method} {target} body={body!r} -> "
                        f"{response.status_code}: {response.text[:200]}"
                    )

    assert not crashes, (
        f"{len(crashes)} route(s) crashed on a body:\n  "
        + "\n  ".join(crashes[:20])
    )
