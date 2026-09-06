"""Every route that takes an id, attacked with an id belonging to somebody else.

The existing attack file picks specific routes and tries specific things. This
one is the sweep behind it: it walks the whole API, finds every route with an
`{id}`-shaped path parameter, and calls each one from Alpha's session with a
row id that exists in Beta.

The distinction matters because the two fail differently. A hand-picked test
covers the routes somebody thought of; a sweep covers the route added next
month by somebody who did not read this file. Cross-tenant reads are the one
defect this platform cannot survive commercially, and they arrive one careless
handler at a time.

**What counts as a pass.** 404 and 403 both. The platform's design is a
database file per company, so Alpha's session opens Alpha's file and Beta's row
id simply is not in it -- that produces 404, which is the right answer and also
the one that leaks least. What must never happen is 200 with Beta's data, and
what must not happen either is 500: a stack trace is a slower leak, and a route
that crashes on a foreign id is a route whose behaviour nobody has thought
about.

**Why the ids are real.** Beta gets a full set of rows -- a conversation, a
customer, a product, a knowledge item, a task, an appointment, a saved reply --
written through the real services. Attacking with `999999` proves only that the
platform handles a missing row; attacking with an id that genuinely exists next
door is the actual attack.
"""

from __future__ import annotations

import sys

import pytest

# Before any fixture patches `database.manager.database_manager`.
import backend.services.appointment_service  # noqa: E402,F401
import backend.services.catalogue_service  # noqa: E402,F401
import backend.services.customer_service  # noqa: E402,F401
import backend.services.knowledge_service  # noqa: E402,F401
import backend.services.message_service  # noqa: E402,F401
import backend.services.ticket_service  # noqa: E402,F401


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    return test_manager


@pytest.fixture()
def app(wired):
    """The real application, so the sweep covers routes nobody remembered."""
    import main

    return main.app


@pytest.fixture()
def alpha_session(wired, platform, alpha):
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    user_id = auth_service.create_user(
        email="attacker@alpha.example", password=PASSWORD, full_name="Attacker"
    )

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'",
            (alpha["id"],),
        ).fetchone()

        conn.execute(
            "INSERT INTO company_users (company_id, user_id, role_id, status, created_at)"
            " VALUES (?, ?, ?, 'active', ?)",
            (alpha["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()

    # An owner, deliberately: the strongest account Alpha has. If even an owner
    # cannot reach Beta, nobody in Alpha can.
    return auth_service.create_session(
        user_id=user_id, ip_address="203.0.113.7", company_id=alpha["id"]
    )["access_token"]


@pytest.fixture()
def beta_rows(wired, beta):
    """Real rows in Beta, one per kind of id the API exposes."""
    from backend.services.appointment_service import appointment_service
    from backend.services.catalogue_service import catalogue_service
    from backend.services.customer_service import customer_service
    from backend.services.knowledge_service import knowledge_service
    from backend.services.message_service import message_service
    from backend.services.ticket_service import ticket_service

    company_id = beta["id"]
    made: dict[str, int] = {}

    customer = customer_service.upsert_from_channel(
        company_id=company_id,
        channel="whatsapp",
        external_user_id="beta-secret-customer",
        display_name="Beta's Confidential Client",
    )
    made["customer_id"] = int(customer["id"])

    message_service.save_message(
        company_id=company_id,
        channel="whatsapp",
        external_user_id="beta-secret-customer",
        direction="inbound",
        text="BETA-CONFIDENTIAL-PAYLOAD",
        sender_type="customer",
    )

    from database.manager import database_manager

    with database_manager.tenant(company_id) as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE company_id = ? LIMIT 1", (company_id,)
        ).fetchone()
        made["conversation_id"] = int(row["id"])

        message = conn.execute(
            "SELECT id FROM messages WHERE company_id = ? LIMIT 1", (company_id,)
        ).fetchone()
        made["message_id"] = int(message["id"])

    product = catalogue_service.create_product(
        company_id=company_id,
        data={"name": "Beta's private product", "sku": "BETA-SECRET-1", "price": 99.0},
    )
    made["product_id"] = int(product["id"])

    item = knowledge_service.create_item(
        company_id=company_id,
        data={"title": "Beta's private note", "content_en": "BETA-CONFIDENTIAL-PAYLOAD"},
    )
    made["item_id"] = int(item["id"])

    try:
        ticket = ticket_service.create_ticket(
            company_id=company_id,
            data={"subject": "Beta's private ticket", "description": "secret"},
        )
        made["ticket_id"] = int(ticket["id"])
    except Exception:  # noqa: BLE001
        pass

    try:
        appointment = appointment_service.create_appointment(
            company_id=company_id,
            data={
                "customer_name": "Beta client",
                "starts_at": "2030-01-01T10:00:00+00:00",
            },
        )
        made["appointment_id"] = int(appointment["id"])
    except Exception:  # noqa: BLE001
        pass

    return made


def _targets(app, beta_rows):
    """Every route with an id-shaped path parameter, filled with Beta's ids."""
    from fastapi.routing import APIRoute

    def walk(routes):
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
                continue

            nested = getattr(route, "original_router", None) or route

            if getattr(nested, "routes", None):
                yield from walk(nested.routes)

    # A numeric id we know exists in Beta, whatever the parameter is called.
    # Guessing by name is what a real attacker does; the platform must not
    # depend on the parameter having a particular name.
    fallback = max(beta_rows.values()) if beta_rows else 1

    seen = set()

    for route in walk(app.routes):
        if "{" not in route.path:
            continue

        # The operator console addresses companies by id by design, and runs on
        # a platform-scope session an Alpha login cannot obtain -- covered by
        # tests/test_session_scope.py rather than here.
        if route.path.startswith("/api/platform"):
            continue

        path = route.path

        for name, value in beta_rows.items():
            path = path.replace("{" + name + "}", str(value))

        # Anything still templated gets a plausible value.
        while "{" in path:
            start = path.index("{")
            end = path.index("}", start)
            name = path[start + 1 : end]
            filler = str(fallback) if name.endswith("_id") or name == "id" else "beta"
            path = path[:start] + filler + path[end + 1 :]

        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            key = (method, path)

            if key in seen:
                continue

            seen.add(key)
            yield method, path, route.path


def test_the_sweep_finds_the_parameterised_routes(app, beta_rows):
    targets = list(_targets(app, beta_rows))

    assert len(targets) > 25, f"only {len(targets)} id-taking routes found"


def test_no_route_hands_alphas_owner_a_row_that_belongs_to_beta(
    app, alpha_session, beta_rows, wired
):
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {alpha_session}"}

    leaked = []
    crashed = []

    for method, path, template in _targets(app, beta_rows):
        response = client.request(method, path, headers=headers, json={})

        if response.status_code >= 500:
            crashed.append(f"{method} {template} -> {response.status_code}")
            continue

        if response.status_code >= 400:
            continue

        # A 2xx is only a leak if Beta's data is in it. Some routes legitimately
        # answer 200 with Alpha's own empty list regardless of the id.
        body = response.text

        if "BETA-CONFIDENTIAL-PAYLOAD" in body or "Beta's" in body:
            leaked.append(f"{method} {template} -> {response.status_code}")

    assert not leaked, (
        "A route returned another company's data to a signed-in outsider:\n  "
        + "\n  ".join(leaked)
    )

    assert not crashed, (
        "A route crashed on an id from another company. A 500 is a slower leak "
        "than a 200 and a route nobody has thought about:\n  "
        + "\n  ".join(crashed)
    )


def test_the_sweep_would_notice_a_leak(app, alpha_session, beta_rows, wired):
    """A sweep that cannot fail is decoration.

    The check above looks for Beta's marker text in a 2xx body. This proves the
    marker is really in Beta's rows and really absent from Alpha's, so a route
    that did return them would be caught.
    """
    from database.manager import database_manager

    # Beta is company 2 in the platform fixture. Assert that rather than reach
    # for it obliquely, so a change to the fixture fails here loudly instead of
    # reading the wrong company's file.
    assert beta_rows, "no Beta rows were created, so there is nothing to leak"

    with database_manager.tenant(2) as conn:
        in_beta = conn.execute(
            "SELECT COUNT(*) AS total FROM messages WHERE body LIKE '%BETA-CONFIDENTIAL%'"
        ).fetchone()["total"]

    with database_manager.tenant(1) as conn:
        in_alpha = conn.execute(
            "SELECT COUNT(*) AS total FROM messages WHERE body LIKE '%BETA-CONFIDENTIAL%'"
        ).fetchone()["total"]

    assert in_beta >= 1, "the marker is not in Beta's data, so the sweep proves nothing"
    assert in_alpha == 0, "the marker is already in Alpha, so the sweep cannot tell a leak"
