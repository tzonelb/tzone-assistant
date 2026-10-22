"""The HTTP layer in front of the purchasing router.

The one behaviour worth a dedicated test here is the seam with Inventory:
receiving a purchase-order line has to move the same number
`inventory_service` owns, through the same ledger, not a shadow copy kept on
the order alone. Everything else (CRUD, permission split, company isolation)
mirrors the same shape already proven for catalogue/inventory.
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager


PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.purchasing  # noqa: F401
    import backend.services.purchasing_service  # noqa: F401
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

    from backend.api.routes import auth, purchasing

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(purchasing.router)

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


def _create_supplier(client, headers, name="Acme Supplies"):
    response = client.post("/api/purchasing/suppliers", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_creating_and_receiving_an_order_increases_stock_through_inventory(
    client, service, platform, alpha
):
    _employee(service, alpha, "pur1@alpha.example.com")
    headers = _login(client, alpha, "pur1@alpha.example.com")
    supplier_id = _create_supplier(client, headers)
    product_id = _insert_product(platform, alpha, stock_quantity=5)

    created = client.post(
        "/api/purchasing/orders", headers=headers,
        json={
            "supplier_id": supplier_id,
            "items": [{"product_id": product_id, "quantity": 10, "unit_cost": 2.5}],
        },
    )
    assert created.status_code == 201, created.text
    order = created.json()
    assert order["status"] == "draft"
    assert float(order["total"]) == 25.0
    item_id = order["items"][0]["id"]

    sent = client.put(
        f"/api/purchasing/orders/{order['id']}/status", headers=headers, json={"status": "sent"}
    )
    assert sent.status_code == 200, sent.text

    received = client.post(
        f"/api/purchasing/orders/{order['id']}/receive", headers=headers,
        json={"items": [{"item_id": item_id, "quantity": 10}]},
    )
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "received"

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
        "receiving the PO must move the product's real stock"
    )
    assert movement_row["movement_type"] == "receipt"
    assert movement_row["quantity_after"] == 15, (
        "the receipt must appear in the same ledger inventory_service writes"
    )


def test_a_partial_receipt_leaves_the_order_partially_received(
    client, service, platform, alpha
):
    _employee(service, alpha, "pur2@alpha.example.com")
    headers = _login(client, alpha, "pur2@alpha.example.com")
    supplier_id = _create_supplier(client, headers)
    product_id = _insert_product(platform, alpha, stock_quantity=0)

    created = client.post(
        "/api/purchasing/orders", headers=headers,
        json={"supplier_id": supplier_id, "items": [{"product_id": product_id, "quantity": 10}]},
    )
    order = created.json()
    item_id = order["items"][0]["id"]
    client.put(f"/api/purchasing/orders/{order['id']}/status", headers=headers, json={"status": "sent"})

    received = client.post(
        f"/api/purchasing/orders/{order['id']}/receive", headers=headers,
        json={"items": [{"item_id": item_id, "quantity": 4}]},
    )
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "partially_received"
    assert received.json()["items"][0]["quantity_received"] == 4


def test_over_receiving_a_line_is_refused_and_changes_nothing(
    client, service, platform, alpha
):
    _employee(service, alpha, "pur3@alpha.example.com")
    headers = _login(client, alpha, "pur3@alpha.example.com")
    supplier_id = _create_supplier(client, headers)
    product_id = _insert_product(platform, alpha, stock_quantity=0)

    created = client.post(
        "/api/purchasing/orders", headers=headers,
        json={"supplier_id": supplier_id, "items": [{"product_id": product_id, "quantity": 5}]},
    )
    order = created.json()
    item_id = order["items"][0]["id"]
    client.put(f"/api/purchasing/orders/{order['id']}/status", headers=headers, json={"status": "sent"})

    refused = client.post(
        f"/api/purchasing/orders/{order['id']}/receive", headers=headers,
        json={"items": [{"item_id": item_id, "quantity": 6}]},
    )
    assert refused.status_code == 400, refused.text

    with platform["manager"].tenant(alpha["id"]) as conn:
        row = conn.execute(
            "SELECT stock_quantity FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    assert row["stock_quantity"] == 0, "a refused over-receipt must not touch stock"


def test_a_draft_order_cannot_receive_stock(client, service, platform, alpha):
    _employee(service, alpha, "pur4@alpha.example.com")
    headers = _login(client, alpha, "pur4@alpha.example.com")
    supplier_id = _create_supplier(client, headers)
    product_id = _insert_product(platform, alpha, stock_quantity=0)

    created = client.post(
        "/api/purchasing/orders", headers=headers,
        json={"supplier_id": supplier_id, "items": [{"product_id": product_id, "quantity": 5}]},
    )
    order = created.json()
    item_id = order["items"][0]["id"]

    refused = client.post(
        f"/api/purchasing/orders/{order['id']}/receive", headers=headers,
        json={"items": [{"item_id": item_id, "quantity": 1}]},
    )
    assert refused.status_code == 400, refused.text


def test_a_view_only_role_cannot_create_a_supplier(client, service, platform, alpha):
    _employee(service, alpha, "pur5@alpha.example.com", role_code="viewer")
    headers = _login(client, alpha, "pur5@alpha.example.com")

    refused = client.post(
        "/api/purchasing/suppliers", headers=headers, json={"name": "Nope Inc"}
    )
    assert refused.status_code == 403, refused.text

    allowed = client.get("/api/purchasing/suppliers", headers=headers)
    assert allowed.status_code == 200, allowed.text


def test_archiving_a_supplier_keeps_its_past_orders_readable(
    client, service, platform, alpha
):
    _employee(service, alpha, "pur6@alpha.example.com")
    headers = _login(client, alpha, "pur6@alpha.example.com")
    supplier_id = _create_supplier(client, headers)
    product_id = _insert_product(platform, alpha)

    created = client.post(
        "/api/purchasing/orders", headers=headers,
        json={"supplier_id": supplier_id, "items": [{"product_id": product_id, "quantity": 1}]},
    )
    order_id = created.json()["id"]

    archived = client.delete(f"/api/purchasing/suppliers/{supplier_id}", headers=headers)
    assert archived.status_code == 200, archived.text

    suppliers = client.get("/api/purchasing/suppliers", headers=headers).json()["items"]
    assert next(s for s in suppliers if s["id"] == supplier_id)["status"] == "archived"

    order = client.get(f"/api/purchasing/orders/{order_id}", headers=headers)
    assert order.status_code == 200, order.text
    assert order.json()["supplier_name"]


def test_a_companys_suppliers_and_orders_are_invisible_to_another_company(
    client, service, platform, alpha, beta
):
    _employee(service, alpha, "pur7a@alpha.example.com")
    alpha_headers = _login(client, alpha, "pur7a@alpha.example.com")
    supplier_id = _create_supplier(client, alpha_headers)
    product_id = _insert_product(platform, alpha)
    created = client.post(
        "/api/purchasing/orders", headers=alpha_headers,
        json={"supplier_id": supplier_id, "items": [{"product_id": product_id, "quantity": 1}]},
    )
    order_id = created.json()["id"]

    _employee(service, beta, "pur7b@beta.example.com")
    beta_headers = _login(client, beta, "pur7b@beta.example.com")

    assert client.get("/api/purchasing/suppliers", headers=beta_headers).json()["items"] == []
    assert client.get(f"/api/purchasing/orders/{order_id}", headers=beta_headers).status_code == 404

    cross_company = client.post(
        "/api/purchasing/orders", headers=beta_headers,
        json={"supplier_id": supplier_id, "items": [{"product_id": product_id, "quantity": 1}]},
    )
    assert cross_company.status_code == 400, cross_company.text
