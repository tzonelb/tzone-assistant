"""The HTTP layer in front of the invoices router.

The one behaviour worth a dedicated test, same reasoning as the other two
commerce modules: `amount_paid` is never set directly, only `record_payment`
reaches it, and it refuses -- before writing anything -- a payment that
would overpay the invoice.
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager


PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.invoices  # noqa: F401
    import backend.services.invoice_service  # noqa: F401
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

    from backend.api.routes import auth, invoices

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(invoices.router)

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


def _create_invoice(client, headers, customer_id, total_price=100.0):
    created = client.post(
        "/api/invoices", headers=headers,
        json={
            "customer_id": customer_id,
            "items": [{"description": "Service fee", "quantity": 1, "unit_price": total_price}],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_creating_an_invoice_and_paying_it_in_full(client, service, platform, alpha):
    _employee(service, alpha, "inv1@alpha.example.com")
    headers = _login(client, alpha, "inv1@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    invoice = _create_invoice(client, headers, customer_id, total_price=150.0)
    assert invoice["status"] == "draft"
    assert float(invoice["balance"]) == 150.0

    sent = client.put(
        f"/api/invoices/{invoice['id']}/status", headers=headers, json={"status": "sent"}
    )
    assert sent.status_code == 200, sent.text

    paid = client.post(
        f"/api/invoices/{invoice['id']}/payments", headers=headers,
        json={"amount": 150.0, "method": "cash"},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"
    assert float(paid.json()["balance"]) == 0.0
    assert len(paid.json()["payments"]) == 1


def test_a_partial_payment_leaves_the_invoice_partially_paid(
    client, service, platform, alpha
):
    _employee(service, alpha, "inv2@alpha.example.com")
    headers = _login(client, alpha, "inv2@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    invoice = _create_invoice(client, headers, customer_id, total_price=100.0)
    client.put(f"/api/invoices/{invoice['id']}/status", headers=headers, json={"status": "sent"})

    partial = client.post(
        f"/api/invoices/{invoice['id']}/payments", headers=headers, json={"amount": 40.0},
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["status"] == "partially_paid"
    assert float(partial.json()["balance"]) == 60.0


def test_overpaying_an_invoice_is_refused_and_changes_nothing(
    client, service, platform, alpha
):
    _employee(service, alpha, "inv3@alpha.example.com")
    headers = _login(client, alpha, "inv3@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    invoice = _create_invoice(client, headers, customer_id, total_price=50.0)
    client.put(f"/api/invoices/{invoice['id']}/status", headers=headers, json={"status": "sent"})

    refused = client.post(
        f"/api/invoices/{invoice['id']}/payments", headers=headers, json={"amount": 999.0},
    )
    assert refused.status_code == 400, refused.text

    unchanged = client.get(f"/api/invoices/{invoice['id']}", headers=headers)
    assert float(unchanged.json()["amount_paid"]) == 0.0


def test_a_draft_invoice_cannot_take_a_payment(client, service, platform, alpha):
    _employee(service, alpha, "inv4@alpha.example.com")
    headers = _login(client, alpha, "inv4@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    invoice = _create_invoice(client, headers, customer_id)

    refused = client.post(
        f"/api/invoices/{invoice['id']}/payments", headers=headers, json={"amount": 10.0},
    )
    assert refused.status_code == 400, refused.text


def test_a_view_only_role_cannot_create_an_invoice(client, service, platform, alpha):
    _employee(service, alpha, "inv5@alpha.example.com", role_code="viewer")
    headers = _login(client, alpha, "inv5@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)

    refused = client.post(
        "/api/invoices", headers=headers,
        json={"customer_id": customer_id, "items": [{"description": "X", "unit_price": 1}]},
    )
    assert refused.status_code == 403, refused.text

    allowed = client.get("/api/invoices", headers=headers)
    assert allowed.status_code == 200, allowed.text


def test_a_companys_invoices_are_invisible_to_another_company(
    client, service, platform, alpha, beta
):
    _employee(service, alpha, "inv6a@alpha.example.com")
    alpha_headers = _login(client, alpha, "inv6a@alpha.example.com")
    customer_id = _insert_customer(platform, alpha)
    invoice = _create_invoice(client, alpha_headers, customer_id)

    _employee(service, beta, "inv6b@beta.example.com")
    beta_headers = _login(client, beta, "inv6b@beta.example.com")

    assert client.get(f"/api/invoices/{invoice['id']}", headers=beta_headers).status_code == 404

    cross_company = client.post(
        "/api/invoices", headers=beta_headers,
        json={"customer_id": customer_id, "items": [{"description": "X", "unit_price": 1}]},
    )
    assert cross_company.status_code == 400, cross_company.text
