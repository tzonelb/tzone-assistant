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

    from backend.services.platform_admin_service import platform_admin_service
    platform_admin_service.ensure_schema()

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


def test_company_can_request_a_plan_change(client_and_db):
    from database.database import db

    admin_client = client_and_db(1, is_super_admin=True)
    create_resp = admin_client.post("/api/platform/companies", json={"name": "Request Co", "slug": "request-co", "plan_id": 1})
    company_id = create_resp.json()["id"]

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (4, 'req@request-co.test', 'Requester', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (?, 4, 'active')",
            (company_id,),
        )
        conn.commit()

    member_client = client_and_db(4, is_super_admin=False)
    resp = member_client.post("/api/platform/subscription-requests", json={"plan_id": 1, "note": "Please renew"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"

    my_requests = member_client.get("/api/platform/my-subscription-requests")
    assert len(my_requests.json()["requests"]) == 1


def test_super_admin_can_approve_a_subscription_request(client_and_db):
    from database.database import db

    admin_client = client_and_db(1, is_super_admin=True)
    create_resp = admin_client.post("/api/platform/companies", json={"name": "Approve Co", "slug": "approve-co"})
    company_id = create_resp.json()["id"]

    with db.connect() as conn:
        cursor = conn.execute("INSERT INTO plans (name, code, max_users) VALUES ('Pro Plan', 'pro-plan-approve', 20)")
        plan_id = cursor.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (5, 'req2@approve-co.test', 'Requester', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (?, 5, 'active')",
            (company_id,),
        )
        conn.commit()

    member_client = client_and_db(5, is_super_admin=False)
    request_resp = member_client.post("/api/platform/subscription-requests", json={"plan_id": plan_id})
    request_id = request_resp.json()["id"]

    admin_client = client_and_db(1, is_super_admin=True)
    approve_resp = admin_client.post(f"/api/platform/subscription-requests/{request_id}/review", json={"approve": True})
    assert approve_resp.status_code == 200, approve_resp.text
    assert approve_resp.json()["status"] == "approved"

    company_detail = admin_client.get(f"/api/platform/companies/{company_id}")
    assert company_detail.json()["subscription"]["plan_code"] == "pro-plan-approve"


def test_reviewing_an_already_reviewed_request_is_rejected(client_and_db):
    from database.database import db

    admin_client = client_and_db(1, is_super_admin=True)
    create_resp = admin_client.post("/api/platform/companies", json={"name": "Twice Co", "slug": "twice-co"})
    company_id = create_resp.json()["id"]

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (6, 'req3@twice-co.test', 'Requester', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (?, 6, 'active')",
            (company_id,),
        )
        conn.commit()

    member_client = client_and_db(6, is_super_admin=False)
    request_resp = member_client.post("/api/platform/subscription-requests", json={"plan_id": 1})
    request_id = request_resp.json()["id"]

    admin_client = client_and_db(1, is_super_admin=True)
    admin_client.post(f"/api/platform/subscription-requests/{request_id}/review", json={"approve": True})
    second_resp = admin_client.post(f"/api/platform/subscription-requests/{request_id}/review", json={"approve": True})
    assert second_resp.status_code == 400


def test_plans_catalog_accessible_to_regular_company_member(client_and_db):
    client = client_and_db(2, is_super_admin=False)
    resp = client.get("/api/platform/plans-catalog")
    assert resp.status_code == 200
    assert len(resp.json()["plans"]) >= 1


def test_super_admin_can_toggle_company_modules(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    create_resp = client.post("/api/platform/companies", json={"name": "Module Co", "slug": "module-co"})
    company_id = create_resp.json()["id"]

    resp = client.patch(
        f"/api/platform/companies/{company_id}/modules",
        json={"appointments": True, "team_chat": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["module_appointments_enabled"] == 1
    assert body["module_team_chat_enabled"] == 0
    assert body["module_scheduler_enabled"] == 1  # untouched field keeps its default


def test_non_super_admin_cannot_toggle_modules(client_and_db):
    admin_client = client_and_db(1, is_super_admin=True)
    create_resp = admin_client.post("/api/platform/companies", json={"name": "Locked Co", "slug": "locked-co"})
    company_id = create_resp.json()["id"]

    regular_client = client_and_db(2, is_super_admin=False)
    resp = regular_client.patch(f"/api/platform/companies/{company_id}/modules", json={"appointments": True})
    assert resp.status_code == 403


def test_company_can_see_its_own_enabled_modules_readonly(client_and_db):
    from database.database import db

    admin_client = client_and_db(1, is_super_admin=True)
    create_resp = admin_client.post("/api/platform/companies", json={"name": "Own Modules Co", "slug": "own-modules-co"})
    company_id = create_resp.json()["id"]
    admin_client.patch(f"/api/platform/companies/{company_id}/modules", json={"appointments": True})

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (3, 'member@own-modules.test', 'Member', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (?, 3, 'active')",
            (company_id,),
        )
        conn.commit()

    member_client = client_and_db(3, is_super_admin=False)
    resp = member_client.get("/api/platform/my-modules")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["appointments"] is True
    assert body["scheduler"] is True  # default-enabled


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


def test_company_gets_an_auto_generated_license_code(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    resp = client.post("/api/platform/companies", json={"name": "License Co", "slug": "license-co"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["license_code"]
    assert body["license_code"].startswith("TZ-")


def test_company_stores_admin_email_and_purchase_metadata(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    resp = client.post(
        "/api/platform/companies",
        json={
            "name": "Contact Co", "slug": "contact-co",
            "main_admin_email": "admin@contactco.com", "contact_phone": "+96170000000",
            "license_code": "TZ-CUST-0001",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["main_admin_email"] == "admin@contactco.com"
    assert body["contact_phone"] == "+96170000000"
    assert body["license_code"] == "TZ-CUST-0001"
    assert body["purchased_at"]


def test_duplicate_license_code_is_rejected(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    client.post("/api/platform/companies", json={"name": "A", "slug": "a-co", "license_code": "TZ-DUP-0001"})
    resp = client.post("/api/platform/companies", json={"name": "B", "slug": "b-co", "license_code": "TZ-DUP-0001"})
    assert resp.status_code == 400
    assert "License code" in resp.json()["detail"]


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


def test_company_user_can_see_own_subscription_readonly(client_and_db):
    """A regular (non-super-admin) company member can see their own
    company's real plan/limits — this replaces a fake placeholder tab."""
    from database.database import db

    admin_client = client_and_db(1, is_super_admin=True)
    admin_client.post("/api/platform/companies", json={"name": "View Co", "slug": "view-co", "plan_id": 1})

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (2, 'member@view-co.test', 'Member', 'active', 0)"
        )
        conn.execute(
            "SELECT id FROM companies WHERE slug = 'view-co'"
        )
        company_row = conn.execute("SELECT id FROM companies WHERE slug = 'view-co'").fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (?, 2, 'active')",
            (company_row["id"],),
        )
        conn.commit()

    member_client = client_and_db(2, is_super_admin=False)
    resp = member_client.get("/api/platform/my-subscription")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_subscription"] is True
    assert body["plan_name"] == "Starter"
    assert "used" in body["users"]
    assert "max" in body["users"]


def test_super_admin_can_create_a_plan(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    resp = client.post(
        "/api/platform/plans",
        json={
            "name": "Custom Enterprise", "code": "custom-enterprise-test", "price_monthly": 299,
            "max_users": 50, "max_channel_accounts": 10,
            "voice_ai_enabled": True, "accounting_connector_enabled": True,
        },
    )
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["max_users"] == 50
    assert plan["voice_ai_enabled"] == 1


def test_duplicate_plan_code_is_rejected(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    client.post("/api/platform/plans", json={"name": "A", "code": "dup-code"})
    resp = client.post("/api/platform/plans", json={"name": "B", "code": "dup-code"})
    assert resp.status_code == 400


def test_super_admin_can_toggle_a_plan_feature(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    create_resp = client.post("/api/platform/plans", json={"name": "Toggle Plan", "code": "toggle-plan"})
    plan_id = create_resp.json()["id"]
    assert create_resp.json()["voice_ai_enabled"] == 0

    update_resp = client.patch(f"/api/platform/plans/{plan_id}", json={"voice_ai_enabled": True, "max_users": 25})
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["voice_ai_enabled"] == 1
    assert body["max_users"] == 25


def test_non_super_admin_cannot_create_or_edit_plans(client_and_db):
    client = client_and_db(2, is_super_admin=False)
    resp = client.post("/api/platform/plans", json={"name": "X", "code": "x-plan"})
    assert resp.status_code == 403


def test_company_cannot_add_users_beyond_plan_limit(client_and_db):
    from backend.services.auth_service import auth_service
    from database.database import db

    admin_client = client_and_db(1, is_super_admin=True)

    plan_resp = admin_client.post(
        "/api/platform/plans", json={"name": "Tiny", "code": "tiny-plan", "max_users": 1},
    )
    plan_id = plan_resp.json()["id"]

    company_resp = admin_client.post(
        "/api/platform/companies",
        json={"name": "Small Co", "slug": "small-co", "plan_id": plan_id},
    )
    company_id = company_resp.json()["id"]

    # Set up a role and an owner membership (role_id required for create_user's validation).
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO roles (company_id, name, code) VALUES (?, 'Owner', 'owner')",
            (company_id,),
        )
        role_id = cursor.lastrowid
        conn.commit()

    owner_id = auth_service.create_user(
        email="owner@small-co.test", password="password123", full_name="Owner",
    )
    auth_service.assign_user_to_company(owner_id, company_id, role_code="owner")

    company_admin_client = client_and_db(owner_id, is_super_admin=False)

    resp = company_admin_client.post(
        "/api/admin/access/users",
        json={
            "email": "second@small-co.test", "password": "password123",
            "full_name": "Second User", "role_id": role_id,
        },
    )
    assert resp.status_code == 400
    assert "Tiny" in resp.json()["detail"]
