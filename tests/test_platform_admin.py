"""
Real tests for the Platform Admin (Super Admin Dashboard) backend:
company management, plans, subscriptions, usage summary, and the
super-admin-only access gate.

Run with: python3 -m pytest tests/test_platform_admin.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'admin@tzone.local', 'Platform Admin', 'active', 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (2, 'regular@tzone.local', 'Regular User', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO plans (id, name, code, price_monthly, max_users) "
            "VALUES (1, 'Starter', 'starter', 29.0, 5)"
        )
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    def make_client_as(user_id, is_super_admin):
        async def _override():
            return {"id": user_id, "email": f"user{user_id}@test.local", "is_super_admin": is_super_admin}
        app.dependency_overrides[get_current_user] = _override
        return TestClient(app)

    yield make_client_as

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


def test_non_super_admin_cannot_access_platform_routes(client_and_db):
    client = client_and_db(2, is_super_admin=False)
    resp = client.get("/api/platform/companies")
    assert resp.status_code == 403


def test_super_admin_can_create_and_list_company(client_and_db):
    client = client_and_db(1, is_super_admin=True)

    create_resp = client.post(
        "/api/platform/companies",
        json={"name": "Acme Support", "slug": "acme-support", "plan_id": 1, "trial_days": 14},
    )
    assert create_resp.status_code == 200, create_resp.text
    company = create_resp.json()
    assert company["name"] == "Acme Support"
    assert company["subscription"]["status"] == "trialing"
    assert company["subscription"]["plan_code"] == "starter"

    list_resp = client.get("/api/platform/companies")
    assert list_resp.status_code == 200
    names = [c["name"] for c in list_resp.json()["companies"]]
    assert "Acme Support" in names


def test_duplicate_slug_is_rejected(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    client.post("/api/platform/companies", json={"name": "First", "slug": "dupe-slug"})
    resp = client.post("/api/platform/companies", json={"name": "Second", "slug": "dupe-slug"})
    assert resp.status_code == 400


def test_super_admin_can_suspend_and_reactivate_company(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    create_resp = client.post("/api/platform/companies", json={"name": "Suspend Me", "slug": "suspend-me"})
    company_id = create_resp.json()["id"]

    suspend_resp = client.patch(
        f"/api/platform/companies/{company_id}/status", json={"status": "suspended"},
    )
    assert suspend_resp.status_code == 200
    assert suspend_resp.json()["status"] == "suspended"

    reactivate_resp = client.patch(
        f"/api/platform/companies/{company_id}/status", json={"status": "active"},
    )
    assert reactivate_resp.status_code == 200
    assert reactivate_resp.json()["status"] == "active"


def test_invalid_status_is_rejected(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    create_resp = client.post("/api/platform/companies", json={"name": "X", "slug": "x-co"})
    company_id = create_resp.json()["id"]
    resp = client.patch(f"/api/platform/companies/{company_id}/status", json={"status": "not_a_real_status"})
    assert resp.status_code == 422


def test_change_plan_replaces_active_subscription(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    from database.database import db
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO plans (name, code, price_monthly, max_users) "
            "VALUES ('Pro', 'pro', 99.0, 20)"
        )
        pro_plan_id = cursor.lastrowid
        conn.commit()

    create_resp = client.post(
        "/api/platform/companies",
        json={"name": "Upgrade Co", "slug": "upgrade-co", "plan_id": 1},
    )
    company_id = create_resp.json()["id"]

    upgrade_resp = client.post(
        f"/api/platform/companies/{company_id}/plan",
        json={"plan_id": pro_plan_id, "duration_days": 30},
    )
    assert upgrade_resp.status_code == 200
    body = upgrade_resp.json()
    assert body["subscription"]["plan_code"] == "pro"
    assert body["subscription"]["status"] == "active"


def test_usage_summary_accessible_to_super_admin(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    resp = client.get("/api/platform/usage")
    assert resp.status_code == 200
    assert "companies_by_status" in resp.json()
