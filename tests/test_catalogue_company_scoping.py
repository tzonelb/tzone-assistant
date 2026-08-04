"""Regression tests for the Master Catalogue API company-scoping and RBAC
gate.

The Catalogue API (backend/api/routes/catalogue.py) manages the company's
product catalogue (the existing `products` table). It must enforce two
audited properties:

  1. Multi-tenant isolation: a user in company A can never list, read,
     update or delete a product that belongs to company B, even with a
     guessable sequential id.
  2. RBAC: viewing (list/get) requires "catalogue.view"; creating, editing
     and deleting requires "catalogue.manage". A user whose role lacks the
     required code gets 403.

These tests also cover the optimistic-concurrency guard on update: a stale
`expected_updated_at` token is rejected with 409 so two editors can't
silently overwrite each other -- and that core/business_connectors.py's
existing get_product_info()/is_enabled("products", ...) behavior (a
separate, independent stub) is unaffected by this module.

Run with: python3 -m pytest tests/test_catalogue_company_scoping.py -v
"""
import os
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_env():
    """Point the shared db singleton at a throwaway SQLite file per test."""
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    # core/engine.py depends on the company_settings schema being present.
    # The products table lives in the central schema
    # (database.py's _create_platform_tables), already created above by
    # db.create_tables().
    company_settings_service.ensure_schema()

    yield db, auth_service

    db.db_path = original_path

    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            break
        except PermissionError:
            time.sleep(0.1)


def _make_company(db, name, slug):
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO workspaces (name, slug, status) VALUES (?, ?, 'active')",
            (f"{name} workspace", f"{slug}-ws"),
        )
        workspace_id = cursor.lastrowid

        cursor = conn.execute(
            """
            INSERT INTO companies (workspace_id, name, slug, status)
            VALUES (?, ?, ?, 'active')
            """,
            (workspace_id, name, slug),
        )
        company_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, 'Owner', 'owner', 'Full access', 1)
            """,
            (company_id,),
        )
        conn.commit()

    return company_id


def _make_role(db, company_id, code, name, permission_codes):
    """Create a non-owner role for a company and grant it a set of codes."""
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, ?, ?, '', 0)
            """,
            (company_id, name, code),
        )
        role_id = cursor.lastrowid
        for permission_code in permission_codes:
            row = conn.execute(
                "SELECT id FROM permissions WHERE code = ?", (permission_code,)
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                    (role_id, row["id"]),
                )
        conn.commit()
    return role_id


def _make_user(db, auth_service, company_id, email, role_code="owner"):
    user_id = auth_service.create_user(
        email=email, password="a-strong-password", full_name=email
    )
    auth_service.assign_user_to_company(user_id, company_id, role_code=role_code)
    session = auth_service.create_session(user_id, company_id=company_id)
    return user_id, session["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _product_payload(name="iPhone 15 Pro", **overrides):
    payload = {"name": name, "sku": "IP15P-256", "price": 999.0, "quantity": 10}
    payload.update(overrides)
    return payload


def test_unauthenticated_requests_are_rejected(fresh_env):
    db, _auth = fresh_env
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/catalogue").status_code == 401
        assert client.get("/api/catalogue/1").status_code == 401
        assert client.post("/api/catalogue", json=_product_payload()).status_code == 401
        assert client.put("/api/catalogue/1", json={"name": "x"}).status_code == 401
        assert client.delete("/api/catalogue/1").status_code == 401


def test_view_without_permission_is_403(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _make_role(db, company, "guest", "Guest", [])
    _, token = _make_user(db, auth_service, company, "guest@test.local", role_code="guest")

    with TestClient(app) as client:
        assert client.get("/api/catalogue", headers=_headers(token)).status_code == 403
        assert (
            client.get("/api/catalogue/categories", headers=_headers(token)).status_code
            == 403
        )


def test_manage_permission_required_to_create(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _make_role(db, company, "viewer", "Viewer", ["catalogue.view"])
    _, token = _make_user(db, auth_service, company, "viewer@test.local", role_code="viewer")

    with TestClient(app) as client:
        assert client.get("/api/catalogue", headers=_headers(token)).status_code == 200
        create = client.post(
            "/api/catalogue", headers=_headers(token), json=_product_payload()
        )
        assert create.status_code == 403


def test_manage_permission_required_to_edit_and_delete(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, owner_token = _make_user(db, auth_service, company, "owner@test.local")
    _make_role(db, company, "viewer", "Viewer", ["catalogue.view"])
    _, viewer_token = _make_user(db, auth_service, company, "viewer2@test.local", role_code="viewer")

    with TestClient(app) as client:
        created = client.post(
            "/api/catalogue", headers=_headers(owner_token), json=_product_payload()
        )
        assert created.status_code == 201
        product_id = created.json()["id"]

        edit = client.put(
            f"/api/catalogue/{product_id}",
            headers=_headers(viewer_token),
            json={"name": "Hacked name"},
        )
        assert edit.status_code == 403

        delete = client.delete(
            f"/api/catalogue/{product_id}", headers=_headers(viewer_token)
        )
        assert delete.status_code == 403

    with db.connect() as conn:
        row = conn.execute("SELECT name FROM products WHERE id = ?", (product_id,)).fetchone()
    assert row["name"] == "iPhone 15 Pro"


def test_owner_can_create_and_list(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner2@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/catalogue", headers=_headers(token), json=_product_payload("Galaxy S24")
        )
        assert created.status_code == 201
        body = created.json()
        assert body["name"] == "Galaxy S24"
        assert body["status"] == "active"

        listed = client.get("/api/catalogue", headers=_headers(token))
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert any(item["id"] == body["id"] for item in items)


def test_list_is_scoped_to_caller_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera@test.local")
    _, token_b = _make_user(db, auth_service, company_b, "ownerb@test.local")

    with TestClient(app) as client:
        created_a = client.post(
            "/api/catalogue", headers=_headers(token_a), json=_product_payload("Company A Product")
        )
        assert created_a.status_code == 201
        product_id_a = created_a.json()["id"]

        created_b = client.post(
            "/api/catalogue", headers=_headers(token_b), json=_product_payload("Company B Product")
        )
        assert created_b.status_code == 201

        # Company B cannot see, read, edit or delete Company A's product,
        # even with the correct sequential id.
        listed_b = client.get("/api/catalogue", headers=_headers(token_b))
        ids_b = {item["id"] for item in listed_b.json()["items"]}
        assert product_id_a not in ids_b

        get_cross = client.get(f"/api/catalogue/{product_id_a}", headers=_headers(token_b))
        assert get_cross.status_code == 404

        edit_cross = client.put(
            f"/api/catalogue/{product_id_a}",
            headers=_headers(token_b),
            json={"name": "Stolen"},
        )
        assert edit_cross.status_code == 404

        delete_cross = client.delete(
            f"/api/catalogue/{product_id_a}", headers=_headers(token_b)
        )
        assert delete_cross.status_code == 404

    with db.connect() as conn:
        row = conn.execute("SELECT name FROM products WHERE id = ?", (product_id_a,)).fetchone()
    assert row["name"] == "Company A Product"


def test_stale_update_is_rejected_with_409(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner3@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/catalogue", headers=_headers(token), json=_product_payload("Stale Test Product")
        )
        product_id = created.json()["id"]
        original_updated_at = created.json()["updated_at"]

        first_edit = client.put(
            f"/api/catalogue/{product_id}",
            headers=_headers(token),
            json={"price": 899.0, "expected_updated_at": original_updated_at},
        )
        assert first_edit.status_code == 200

        stale_edit = client.put(
            f"/api/catalogue/{product_id}",
            headers=_headers(token),
            json={"price": 799.0, "expected_updated_at": original_updated_at},
        )
        assert stale_edit.status_code == 409
        body = stale_edit.json()["detail"]
        assert body["current"]["price"] == 899.0


def test_invalid_status_is_rejected_with_422(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner4@test.local")

    with TestClient(app) as client:
        response = client.post(
            "/api/catalogue",
            headers=_headers(token),
            json=_product_payload("Bad Status Product", status="not_a_real_status"),
        )
        assert response.status_code == 422


def test_business_connectors_product_lookup_is_unaffected(fresh_env):
    """core/business_connectors.py's get_product_info()/is_enabled() is a
    separate, independent stub gate that does not read catalogue rows --
    this module must not change its behavior or signature."""
    db, _auth = fresh_env
    from core.business_connectors import BusinessConnectors

    connectors = BusinessConnectors()
    result = connectors.get_product_info("iphone", company_id=1)
    assert "connected" in result
    assert isinstance(connectors.is_enabled("products", company_id=1), bool)
