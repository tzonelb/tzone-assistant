"""The HTTP layer in front of the orders router.

The seam worth a dedicated test, same reasoning as test_purchasing_route.py:
fulfilling an order line has to move the same stock number
`inventory_service` owns, and must be refused -- before writing anything --
once there isn't enough of it, the mirror image of purchasing's over-receive
guard.
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager


PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.orders  # noqa: F401
    import backend.services.order_service  # noqa: F401
    import backend.services.inventory_service  # noqa: F401
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)
        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, orders

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(orders.router)

    return TestClient(app)


def _employee(service, company, email, role_code="manager"):
    user_id = service.create_user(email, PASSWORD, "Test Person")
    service.assign_user_to_company(user_id, company["id"], role_code)
    return user_id


def _login(client, company, email):
    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"], "company": company["name"],
            "email": email, "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _insert_product(platform, company, *, name="Widget", stock_quantity=0):
    from database.manager import utc_now_iso

    now = utc_now_iso()
    with platform["manager"].tenant(company["id"]) as conn:
        cursor = conn.execute(
            """
            INSERT INTO products (company_id, name, stock_quantity, status, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?)
            """,
            (company["id"], name, stock_quantity, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _insert_customer(platform, company, *, name="Walk-in Customer"):
    from database.manager import utc_now_iso

    now = utc_now_iso()
    with platform["manager"].tenant(company["id"]) as conn:
        cursor = conn.execute(
            """
            INSERT INTO customers (
                company_id, display_name, first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (company["id"], name, now, now, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_creating_and_fulfilling_an_order_decreases_stock_through_inventory(
    client, service, platform, alpha
):
    _employee(service, alpha, "ord1@alpha.example.com")
    headers = _login(client, alpha, "ord1@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    product_id = _insert_product(platform, alpha, stock_quantity=20)

    created = client.post(
        "/api/orders", headers=headers,
        json={
            "customer_id": customer_id,
            "items": [{"product_id": product_id, "quantity": 5, "unit_price": 9.0}],
        },
    )
    assert created.status_code == 201, created.text
    order = created.json()
    assert order["status"] == "draft"
    assert float(order["total"]) == 45.0
    item_id = order["items"][0]["id"]

    confirmed = client.put(
        f"/api/orders/{order['id']}/status", headers=headers, json={"status": "confirmed"}
    )
    assert confirmed.status_code == 200, confirmed.text

    fulfilled = client.post(
        f"/api/orders/{order['id']}/fulfil", headers=headers,
        json={"items": [{"item_id": item_id, "quantity": 5}]},
    )
    assert fulfilled.status_code == 200, fulfilled.text
    assert fulfilled.json()["status"] == "fulfilled"

    with platform["manager"].tenant(alpha["id"]) as conn:
        product_row = conn.execute(
            "SELECT stock_quantity FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        movement_row = conn.execute(
            "SELECT movement_type, quantity_after FROM stock_movements "
            "WHERE product_id = ? ORDER BY id DESC LIMIT 1",
            (product_id,),
        ).fetchone()

    assert product_row["stock_quantity"] == 15, (
        "fulfilling the order must move the product's real stock"
    )
    assert movement_row["movement_type"] == "sale"
    assert movement_row["quantity_after"] == 15


def test_fulfilling_past_available_stock_is_refused_and_changes_nothing(
    client, service, platform, alpha
):
    _employee(service, alpha, "ord2@alpha.example.com")
    headers = _login(client, alpha, "ord2@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    product_id = _insert_product(platform, alpha, stock_quantity=2)

    created = client.post(
        "/api/orders", headers=headers,
        json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 10}]},
    )
    order = created.json()
    item_id = order["items"][0]["id"]
    client.put(f"/api/orders/{order['id']}/status", headers=headers, json={"status": "confirmed"})

    refused = client.post(
        f"/api/orders/{order['id']}/fulfil", headers=headers,
        json={"items": [{"item_id": item_id, "quantity": 5}]},
    )
    assert refused.status_code == 400, refused.text

    with platform["manager"].tenant(alpha["id"]) as conn:
        row = conn.execute(
            "SELECT stock_quantity FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    assert row["stock_quantity"] == 2, "a refused over-fulfilment must not touch stock"


def test_a_partial_fulfilment_leaves_the_order_partially_fulfilled(
    client, service, platform, alpha
):
    _employee(service, alpha, "ord3@alpha.example.com")
    headers = _login(client, alpha, "ord3@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    product_id = _insert_product(platform, alpha, stock_quantity=20)

    created = client.post(
        "/api/orders", headers=headers,
        json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 10}]},
    )
    order = created.json()
    item_id = order["items"][0]["id"]
    client.put(f"/api/orders/{order['id']}/status", headers=headers, json={"status": "confirmed"})

    fulfilled = client.post(
        f"/api/orders/{order['id']}/fulfil", headers=headers,
        json={"items": [{"item_id": item_id, "quantity": 4}]},
    )
    assert fulfilled.status_code == 200, fulfilled.text
    assert fulfilled.json()["status"] == "partially_fulfilled"
    assert fulfilled.json()["items"][0]["quantity_fulfilled"] == 4


def test_a_draft_order_cannot_be_fulfilled(client, service, platform, alpha):
    _employee(service, alpha, "ord4@alpha.example.com")
    headers = _login(client, alpha, "ord4@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    product_id = _insert_product(platform, alpha, stock_quantity=20)

    created = client.post(
        "/api/orders", headers=headers,
        json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 1}]},
    )
    order = created.json()
    item_id = order["items"][0]["id"]

    refused = client.post(
        f"/api/orders/{order['id']}/fulfil", headers=headers,
        json={"items": [{"item_id": item_id, "quantity": 1}]},
    )
    assert refused.status_code == 400, refused.text


def test_a_view_only_role_cannot_create_an_order(client, service, platform, alpha):
    _employee(service, alpha, "ord5@alpha.example.com", role_code="viewer")
    headers = _login(client, alpha, "ord5@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    product_id = _insert_product(platform, alpha, stock_quantity=1)

    refused = client.post(
        "/api/orders", headers=headers,
        json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 1}]},
    )
    assert refused.status_code == 403, refused.text

    allowed = client.get("/api/orders", headers=headers)
    assert allowed.status_code == 200, allowed.text


def test_a_companys_orders_are_invisible_to_and_uncreatable_by_another_company(
    client, service, platform, alpha, beta
):
    _employee(service, alpha, "ord6a@alpha.example.com")
    alpha_headers = _login(client, alpha, "ord6a@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    product_id = _insert_product(platform, alpha, stock_quantity=5)
    created = client.post(
        "/api/orders", headers=alpha_headers,
        json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 1}]},
    )
    order_id = created.json()["id"]

    _employee(service, beta, "ord6b@beta.example.com")
    beta_headers = _login(client, beta, "ord6b@beta.example.com")

    assert client.get(f"/api/orders/{order_id}", headers=beta_headers).status_code == 404

    cross_company = client.post(
        "/api/orders", headers=beta_headers,
        json={"customer_id": customer_id, "items": [{"product_id": product_id, "quantity": 1}]},
    )
    assert cross_company.status_code == 400, cross_company.text
