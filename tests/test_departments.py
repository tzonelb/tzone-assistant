"""
Real tests for company-scoped Departments — each company defines its
own instead of a fixed hardcoded list shared by everyone.

Run with: python3 -m pytest tests/test_departments.py -v
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


def test_new_company_starts_with_only_unassigned():
    """No hardcoded business-type list (Sales/IPTV/Accounting...) —
    this was the actual bug: every company saw the same fixed set."""
    from database.database import db
    from backend.services.department_service import department_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)
    db.create_tables()
    department_service.ensure_schema()

    try:
        departments = department_service.list_for_company(company_id=999)
        assert departments == ["Unassigned"]
    finally:
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


def test_create_and_list_department(client_and_db):
    client = client_and_db
    resp = client.post("/api/departments", json={"name": "Sales"})
    assert resp.status_code == 200, resp.text
    assert "Sales" in resp.json()["departments"]
    assert "Unassigned" in resp.json()["departments"]


def test_cannot_create_duplicate_department(client_and_db):
    client = client_and_db
    client.post("/api/departments", json={"name": "Support"})
    resp = client.post("/api/departments", json={"name": "support"})  # case-insensitive dup
    assert resp.status_code == 400


def test_cannot_create_unassigned_explicitly(client_and_db):
    client = client_and_db
    resp = client.post("/api/departments", json={"name": "Unassigned"})
    assert resp.status_code == 400


def test_delete_department(client_and_db):
    client = client_and_db
    client.post("/api/departments", json={"name": "Temp Dept"})
    resp = client.delete("/api/departments/Temp Dept")
    assert resp.status_code == 200
    assert "Temp Dept" not in resp.json()["departments"]


def test_departments_are_isolated_per_company(client_and_db):
    from database.database import db
    from backend.services.department_service import department_service

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()
    department_service.create(company_id=2, name="Other Co Only Dept")

    client = client_and_db
    resp = client.get("/api/departments")
    assert "Other Co Only Dept" not in resp.json()["departments"]


def test_conversation_options_returns_real_company_departments(client_and_db):
    """The /conversations/options endpoint (used to populate the
    assign/transfer dropdowns) now returns this company's real
    departments instead of the old hardcoded list."""
    client = client_and_db
    client.post("/api/departments", json={"name": "Custom Dept"})

    resp = client.get("/conversations/options")
    assert resp.status_code == 200
    departments = resp.json()["departments"]
    assert "Custom Dept" in departments
    # The old hardcoded list had IPTV/Accounting/etc by default —
    # confirm those are gone unless this company added them itself.
    assert "IPTV" not in departments
    assert "Accounting" not in departments
