"""The HTTP layer in front of the inventory router.

`inventory_service`'s own ledger math (balance computed from the product's
current quantity, refused once it would go negative) is exercised directly
elsewhere; what has no coverage yet is the router wrapped around it -- the
`inventory.view` / `inventory.manage` split, company isolation, and the
low-stock listing an operator actually reads from the screen.
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager


PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.inventory  # noqa: F401
    import backend.services.inventory_service  # noqa: F401
    import backend.services.catalogue_service  # noqa: F401
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

    from backend.api.routes import auth, inventory

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(inventory.router)

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


def _insert_product(platform, company, *, name="Filter", stock_quantity=10, reorder_point=None):
    from database.manager import utc_now_iso

    now = utc_now_iso()
    with platform["manager"].tenant(company["id"]) as conn:
        cursor = conn.execute(
            """
            INSERT INTO products (
                company_id, name, stock_quantity, reorder_point, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            """,
            (company["id"], name, stock_quantity, reorder_point, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_a_receipt_increases_stock_and_files_the_ledger(client, service, platform, alpha):
    _employee(service, alpha, "inv1@alpha.example.com")
    headers = _login(client, alpha, "inv1@alpha.example.com")
    product_id = _insert_product(platform, alpha, stock_quantity=10)

    recorded = client.post(
        "/api/inventory/movements", headers=headers,
        json={"product_id": product_id, "movement_type": "receipt", "quantity_delta": 5},
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["quantity_after"] == 15

    products = client.get("/api/inventory/products", headers=headers)
    assert products.status_code == 200, products.text
    row = next(item for item in products.json()["items"] if item["id"] == product_id)
    assert row["stock_quantity"] == 15

    movements = client.get("/api/inventory/movements", headers=headers)
    assert movements.status_code == 200, movements.text
    assert any(item["quantity_after"] == 15 for item in movements.json()["items"])


def test_a_sale_that_would_go_negative_is_refused(client, service, platform, alpha):
    _employee(service, alpha, "inv2@alpha.example.com")
    headers = _login(client, alpha, "inv2@alpha.example.com")
    product_id = _insert_product(platform, alpha, stock_quantity=3)

    oversold = client.post(
        "/api/inventory/movements", headers=headers,
        json={"product_id": product_id, "movement_type": "sale", "quantity_delta": -5},
    )
    assert oversold.status_code == 400, oversold.text

    products = client.get("/api/inventory/products", headers=headers)
    row = next(item for item in products.json()["items"] if item["id"] == product_id)
    assert row["stock_quantity"] == 3, "the refused movement must not have changed the balance"


def test_a_view_only_role_cannot_record_a_movement(client, service, platform, alpha):
    _employee(service, alpha, "inv3@alpha.example.com", role_code="viewer")
    headers = _login(client, alpha, "inv3@alpha.example.com")
    product_id = _insert_product(platform, alpha, stock_quantity=10)

    refused = client.post(
        "/api/inventory/movements", headers=headers,
        json={"product_id": product_id, "movement_type": "receipt", "quantity_delta": 1},
    )
    assert refused.status_code == 403, refused.text

    allowed = client.get("/api/inventory/summary", headers=headers)
    assert allowed.status_code == 200, allowed.text


def test_low_stock_lists_only_products_at_or_under_their_own_reorder_point(
    client, service, platform, alpha
):
    _employee(service, alpha, "inv4@alpha.example.com")
    headers = _login(client, alpha, "inv4@alpha.example.com")

    low = _insert_product(platform, alpha, name="Almost gone", stock_quantity=2, reorder_point=5)
    _insert_product(platform, alpha, name="Plenty", stock_quantity=50, reorder_point=5)
    _insert_product(platform, alpha, name="No threshold set", stock_quantity=1, reorder_point=None)

    listed = client.get("/api/inventory/low-stock", headers=headers)
    assert listed.status_code == 200, listed.text
    names = {item["name"] for item in listed.json()["items"]}

    assert names == {"Almost gone"}, (
        f"expected only the product under its reorder point, got {names}"
    )
    assert any(item["id"] == low for item in listed.json()["items"])


def test_a_companys_stock_movements_are_invisible_to_another_company(
    client, service, platform, alpha, beta
):
    _employee(service, alpha, "inv5a@alpha.example.com")
    alpha_headers = _login(client, alpha, "inv5a@alpha.example.com")
    product_id = _insert_product(platform, alpha, stock_quantity=10)
    client.post(
        "/api/inventory/movements", headers=alpha_headers,
        json={"product_id": product_id, "movement_type": "receipt", "quantity_delta": 5},
    )

    _employee(service, beta, "inv5b@beta.example.com")
    beta_headers = _login(client, beta, "inv5b@beta.example.com")

    beta_movements = client.get("/api/inventory/movements", headers=beta_headers)
    assert beta_movements.status_code == 200, beta_movements.text
    assert beta_movements.json()["items"] == []

    # Beta cannot even touch Alpha's product id -- it does not exist in its
    # own company's database.
    cross_company = client.post(
        "/api/inventory/movements", headers=beta_headers,
        json={"product_id": product_id, "movement_type": "sale", "quantity_delta": -1},
    )
    assert cross_company.status_code == 404, cross_company.text


def test_setting_a_reorder_point_round_trips(client, service, platform, alpha):
    _employee(service, alpha, "inv6@alpha.example.com")
    headers = _login(client, alpha, "inv6@alpha.example.com")
    product_id = _insert_product(platform, alpha, stock_quantity=10)

    updated = client.put(
        f"/api/inventory/products/{product_id}/reorder-point",
        headers=headers, json={"reorder_point": 4},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["reorder_point"] == 4
