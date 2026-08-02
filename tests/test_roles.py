"""
Real tests for Roles & Permissions — specifically employee-to-department
assignment, since an employee can legitimately belong to more than one
department (multi-select, not a single field).

Run with: python3 -m pytest tests/test_roles.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.department_service import department_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    department_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'owner@test.local', 'Owner', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Owner', 'owner', 'Full access', 1)"
        )
        owner_role_id = conn.execute("SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'").fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, 1, ?, 'active')",
            (owner_role_id,),
        )

        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (2, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, 2, ?, 'active')",
            (owner_role_id,),
        )
        conn.commit()

    department_service.create(company_id=COMPANY_ID, name="Sales")
    department_service.create(company_id=COMPANY_ID, name="Support")

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "owner@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

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


def test_overview_includes_company_departments_and_empty_membership(client_and_db):
    client = client_and_db
    resp = client.get("/api/admin/access/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "Sales" in body["departments"]
    assert "Support" in body["departments"]
    agent = next(u for u in body["users"] if u["email"] == "agent@test.local")
    assert agent["departments"] == []


def test_assign_multiple_departments_to_employee(client_and_db):
    client = client_and_db
    overview = client.get("/api/admin/access/overview").json()
    agent = next(u for u in overview["users"] if u["email"] == "agent@test.local")

    resp = client.patch(
        f"/api/admin/access/users/{agent['id']}",
        json={"role_id": agent["role_id"], "branch_id": None, "status": "active", "departments": ["Sales", "Support"]},
    )
    assert resp.status_code == 200, resp.text

    overview = client.get("/api/admin/access/overview").json()
    agent = next(u for u in overview["users"] if u["email"] == "agent@test.local")
    assert sorted(agent["departments"]) == ["Sales", "Support"]


def test_assigning_unregistered_department_is_rejected(client_and_db):
    client = client_and_db
    overview = client.get("/api/admin/access/overview").json()
    agent = next(u for u in overview["users"] if u["email"] == "agent@test.local")

    resp = client.patch(
        f"/api/admin/access/users/{agent['id']}",
        json={"role_id": agent["role_id"], "branch_id": None, "status": "active", "departments": ["Not A Real Department"]},
    )
    assert resp.status_code == 400


def test_create_user_with_departments(client_and_db):
    client = client_and_db
    overview = client.get("/api/admin/access/overview").json()
    role_id = overview["roles"][0]["id"]

    resp = client.post(
        "/api/admin/access/users",
        json={
            "full_name": "New Hire",
            "email": "newhire@test.local",
            "password": "supersecret1",
            "role_id": role_id,
            "departments": ["Sales"],
        },
    )
    assert resp.status_code == 200, resp.text

    overview = client.get("/api/admin/access/overview").json()
    new_hire = next(u for u in overview["users"] if u["email"] == "newhire@test.local")
    assert new_hire["departments"] == ["Sales"]


def test_employees_endpoint_filters_by_department_and_keeps_unassigned_visible(client_and_db):
    from backend.api.routes.conversations import _company_employees

    client = client_and_db
    overview = client.get("/api/admin/access/overview").json()
    agent = next(u for u in overview["users"] if u["email"] == "agent@test.local")
    client.patch(
        f"/api/admin/access/users/{agent['id']}",
        json={"role_id": agent["role_id"], "branch_id": None, "status": "active", "departments": ["Sales"]},
    )

    sales_employees = _company_employees(COMPANY_ID, department="Sales")
    assert any(e["email"] == "agent@test.local" for e in sales_employees)
    assert any(e["email"] == "owner@test.local" for e in sales_employees)  # owner has no department yet — stays visible everywhere

    support_employees = _company_employees(COMPANY_ID, department="Support")
    assert not any(e["email"] == "agent@test.local" for e in support_employees)
