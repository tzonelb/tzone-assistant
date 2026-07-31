"""
Real tests for the Catalogue module: a company's product catalogue —
name, SKU, description, price, stock quantity, category, status
(active/archived), optional image URL.

Run with: python -m pytest tests/test_catalogue.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.catalogue_service import catalogue_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    catalogue_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')"
        )
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

    from fastapi.testclient import TestClient
    yield TestClient(app)

    app.dependency_overrides.clear()
    db.db_path = original_db_path
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_db_path):
                os.remove(tmp_db_path)
            break
        except PermissionError:
            time.sleep(0.1)


def _make_product(client, **overrides):
    payload = {"name": "Widget"}
    payload.update(overrides)
    resp = client.post("/api/catalogue", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_create_product_defaults_to_active_status(client_and_db):
    client = client_and_db
    product = _make_product(client)
    assert product["status"] == "active"
    assert product["price_cents"] == 0
    assert product["stock_quantity"] == 0


def test_create_product_rejects_empty_name(client_and_db):
    client = client_and_db
    resp = client.post("/api/catalogue", json={"name": "   "})
    assert resp.status_code == 400


def test_create_product_rejects_negative_price(client_and_db):
    client = client_and_db
    resp = client.post("/api/catalogue", json={"name": "Widget", "price_cents": -100})
    assert resp.status_code == 400


def test_create_product_rejects_negative_stock(client_and_db):
    client = client_and_db
    resp = client.post("/api/catalogue", json={"name": "Widget", "stock_quantity": -5})
    assert resp.status_code == 400


def test_create_product_rejects_duplicate_sku_same_company(client_and_db):
    client = client_and_db
    _make_product(client, name="Widget A", sku="SKU-1")
    resp = client.post("/api/catalogue", json={"name": "Widget B", "sku": "SKU-1"})
    assert resp.status_code == 400


def test_create_product_allows_same_sku_different_company(client_and_db):
    from database.database import db
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    from backend.services.catalogue_service import catalogue_service
    product1 = catalogue_service.create_product(company_id=1, name="Widget A", sku="SKU-X")
    product2 = catalogue_service.create_product(company_id=2, name="Widget B", sku="SKU-X")
    assert product1["sku"] == product2["sku"] == "SKU-X"


def test_list_filters_by_category(client_and_db):
    client = client_and_db
    a = _make_product(client, name="A", category="Tools")
    _make_product(client, name="B", category="Toys")

    resp = client.get("/api/catalogue", params={"category": "Tools"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == a["id"]


def test_list_filters_by_status(client_and_db):
    client = client_and_db
    a = _make_product(client, name="A")
    b = _make_product(client, name="B")
    client.put(f"/api/catalogue/{a['id']}", json={"status": "archived"})

    resp = client.get("/api/catalogue", params={"status": "archived"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == a["id"]

    resp_active = client.get("/api/catalogue", params={"status": "active"})
    active_ids = {item["id"] for item in resp_active.json()["items"]}
    assert active_ids == {b["id"]}


def test_list_search_matches_name_sku_description(client_and_db):
    client = client_and_db
    a = _make_product(client, name="Blue Widget", sku="BW-1", description="A very handy widget")
    b = _make_product(client, name="Red Gadget", sku="RG-2", description="Something else")

    resp = client.get("/api/catalogue", params={"search": "widget"})
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {a["id"]}

    resp = client.get("/api/catalogue", params={"search": "RG-2"})
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {b["id"]}

    resp = client.get("/api/catalogue", params={"search": "else"})
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {b["id"]}


def test_update_changes_fields_including_status(client_and_db):
    client = client_and_db
    product = _make_product(client, name="Widget", price_cents=500)

    resp = client.put(
        f"/api/catalogue/{product['id']}",
        json={"name": "Updated Widget", "price_cents": 999, "status": "archived"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Updated Widget"
    assert body["price_cents"] == 999
    assert body["status"] == "archived"


def test_update_rejects_invalid_status(client_and_db):
    client = client_and_db
    product = _make_product(client)
    resp = client.put(f"/api/catalogue/{product['id']}", json={"status": "not-a-real-status"})
    assert resp.status_code == 400


def test_delete_product_removes_it(client_and_db):
    client = client_and_db
    product = _make_product(client)

    resp = client.delete(f"/api/catalogue/{product['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    get_resp = client.get(f"/api/catalogue/{product['id']}")
    assert get_resp.status_code == 404


def test_products_are_isolated_per_company(client_and_db):
    from database.database import db
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    from backend.services.catalogue_service import catalogue_service
    other_product = catalogue_service.create_product(company_id=2, name="Other Co Product")

    client = client_and_db
    list_resp = client.get("/api/catalogue")
    assert all(item["id"] != other_product["id"] for item in list_resp.json()["items"])

    get_resp = client.get(f"/api/catalogue/{other_product['id']}")
    assert get_resp.status_code == 404

    update_resp = client.put(f"/api/catalogue/{other_product['id']}", json={"status": "archived"})
    assert update_resp.status_code == 404

    delete_resp = client.delete(f"/api/catalogue/{other_product['id']}")
    assert delete_resp.status_code == 404


def test_import_csv_creates_products(client_and_db):
    client = client_and_db
    csv_content = (
        "name,sku,description,category,price,stock_quantity\n"
        "Speaker 8 Inch,SPK-8,Loud bass speaker,Audio,49.99,10\n"
        "Wireless Earbuds,EAR-1,,Audio,19.5,25\n"
    )
    resp = client.post(
        "/api/catalogue/import/csv",
        files={"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 2
    assert body["updated"] == 0
    assert body["errors"] == []

    items = client.get("/api/catalogue").json()["items"]
    assert len(items) == 2
    speaker = next(item for item in items if item["sku"] == "SPK-8")
    assert speaker["price_cents"] == 4999
    assert speaker["stock_quantity"] == 10


def test_import_csv_upserts_by_sku_on_rerun(client_and_db):
    client = client_and_db
    csv_content = "name,sku,price\nSpeaker,SPK-8,49.99\n"
    client.post("/api/catalogue/import/csv", files={"file": ("a.csv", csv_content.encode(), "text/csv")})

    updated_csv = "name,sku,price\nSpeaker (updated name),SPK-8,39.99\n"
    resp = client.post("/api/catalogue/import/csv", files={"file": ("b.csv", updated_csv.encode(), "text/csv")})
    body = resp.json()
    assert body["created"] == 0
    assert body["updated"] == 1

    items = client.get("/api/catalogue").json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "Speaker (updated name)"
    assert items[0]["price_cents"] == 3999


def test_import_csv_requires_name_column(client_and_db):
    client = client_and_db
    csv_content = "sku,price\nX,1\n"
    resp = client.post("/api/catalogue/import/csv", files={"file": ("bad.csv", csv_content.encode(), "text/csv")})
    assert resp.status_code == 400


def test_import_csv_skips_rows_missing_name(client_and_db):
    client = client_and_db
    csv_content = "name,price\n,10\nValid Product,5\n"
    resp = client.post("/api/catalogue/import/csv", files={"file": ("mixed.csv", csv_content.encode(), "text/csv")})
    body = resp.json()
    assert body["created"] == 1
    assert len(body["errors"]) == 1


def test_import_whatsapp_requires_connected_channel(client_and_db):
    client = client_and_db
    resp = client.post("/api/catalogue/import/whatsapp", json={"catalog_id": "12345"})
    assert resp.status_code == 400
    assert "Connect a WhatsApp channel" in resp.json()["detail"]


def test_import_whatsapp_catalog_creates_products(client_and_db):
    from unittest.mock import patch

    client = client_and_db
    with patch(
        "backend.services.channel_account_service.channel_account_service.get_active_token",
        return_value="fake-wa-token",
    ), patch("backend.services.catalogue_service.requests.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": [
                {"name": "Router X1", "retailer_id": "RTR-1", "price": "25.00 USD", "description": "Home router"},
                {"name": "Cable 5m", "retailer_id": "CBL-5", "price": "3.00 USD"},
            ],
            "paging": {},
        }
        resp = client.post("/api/catalogue/import/whatsapp", json={"catalog_id": "cat_123"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 2

    items = client.get("/api/catalogue").json()["items"]
    router = next(item for item in items if item["sku"] == "RTR-1")
    assert router["price_cents"] == 2500
    assert router["category"] == "WhatsApp Catalogue"
