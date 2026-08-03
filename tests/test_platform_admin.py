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
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (?, 'Owner', 'owner', 'Full access', 1)",
            (company_id,),
        )
        owner_role_id = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'", (company_id,)
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (?, 4, ?, 'active')",
            (company_id, owner_role_id),
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
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (?, 'Owner', 'owner', 'Full access', 1)",
            (company_id,),
        )
        owner_role_id = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'", (company_id,)
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (?, 5, ?, 'active')",
            (company_id, owner_role_id),
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
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (?, 'Owner', 'owner', 'Full access', 1)",
            (company_id,),
        )
        owner_role_id = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = 'owner'", (company_id,)
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (?, 6, ?, 'active')",
            (company_id, owner_role_id),
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


def test_change_plan_extends_from_remaining_time_not_from_now(client_and_db):
    """Before this fix, change_plan always set expires_at = now +
    duration_days, discarding any time still remaining on the current
    subscription. Renewing 20 days early with duration_days=30 must
    land around 50 days out, not a flat 30."""
    from datetime import datetime, timedelta, timezone
    from database.database import db

    client = client_and_db(1, is_super_admin=True)

    create_resp = client.post(
        "/api/platform/companies",
        json={"name": "Early Renewer Co", "slug": "early-renewer-co", "plan_id": 1},
    )
    company_id = create_resp.json()["id"]

    future_expiry = datetime.now(timezone.utc) + timedelta(days=20)
    with db.connect() as conn:
        conn.execute(
            "UPDATE subscriptions SET expires_at = ? WHERE company_id = ? AND status IN ('active', 'trialing')",
            (future_expiry.isoformat(), company_id),
        )
        conn.commit()

    resp = client.post(
        f"/api/platform/companies/{company_id}/plan",
        json={"plan_id": 1, "duration_days": 30},
    )
    assert resp.status_code == 200, resp.text
    new_expires_at = datetime.fromisoformat(resp.json()["subscription"]["expires_at"])
    if new_expires_at.tzinfo is None:
        new_expires_at = new_expires_at.replace(tzinfo=timezone.utc)

    days_from_now = (new_expires_at - datetime.now(timezone.utc)).days
    assert 45 <= days_from_now <= 50, f"expected ~50 days remaining, got {days_from_now}"


def test_change_plan_after_expiry_starts_from_now(client_and_db):
    """A lapsed subscription can't be extended from a past date - renewal
    after expiry falls back to now + duration_days like before."""
    from datetime import datetime, timedelta, timezone
    from database.database import db

    client = client_and_db(1, is_super_admin=True)

    create_resp = client.post(
        "/api/platform/companies",
        json={"name": "Lapsed Co", "slug": "lapsed-co", "plan_id": 1},
    )
    company_id = create_resp.json()["id"]

    past_expiry = datetime.now(timezone.utc) - timedelta(days=5)
    with db.connect() as conn:
        conn.execute(
            "UPDATE subscriptions SET expires_at = ? WHERE company_id = ? AND status IN ('active', 'trialing')",
            (past_expiry.isoformat(), company_id),
        )
        conn.commit()

    resp = client.post(
        f"/api/platform/companies/{company_id}/plan",
        json={"plan_id": 1, "duration_days": 30},
    )
    new_expires_at = datetime.fromisoformat(resp.json()["subscription"]["expires_at"])
    if new_expires_at.tzinfo is None:
        new_expires_at = new_expires_at.replace(tzinfo=timezone.utc)
    days_from_now = (new_expires_at - datetime.now(timezone.utc)).days
    assert 28 <= days_from_now <= 30, f"expected ~30 days remaining, got {days_from_now}"


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
    assert body["plan_id"] == 1
    assert body["plan_name"] == "Starter"
    assert "used" in body["users"]
    assert "max" in body["users"]


def test_own_subscription_exposes_plan_id_even_when_plan_later_retired(client_and_db):
    """Before this fix, the frontend derived currentPlanId by matching
    plan_code against the (active-only) plans catalog, so a company whose
    current plan was retired could never renew again - the Renew button
    stayed permanently disabled with no explanation. Exposing plan_id
    directly from the subscription lets the frontend renew regardless of
    whether the plan still shows up in the active catalog."""
    from database.database import db

    admin_client = client_and_db(1, is_super_admin=True)
    admin_client.post("/api/platform/companies", json={"name": "Retire Plan Co", "slug": "retire-plan-co", "plan_id": 1})

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (7, 'member@retire-plan-co.test', 'Member', 'active', 0)"
        )
        company_row = conn.execute("SELECT id FROM companies WHERE slug = 'retire-plan-co'").fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (?, 7, 'active')",
            (company_row["id"],),
        )
        conn.commit()

    # Super admin retires the Starter plan.
    archive_resp = admin_client.patch("/api/platform/plans/1", json={"status": "retired"})
    assert archive_resp.status_code == 200, archive_resp.text

    member_client = client_and_db(7, is_super_admin=False)
    resp = member_client.get("/api/platform/my-subscription")
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan_id"] == 1

    plans_resp = member_client.get("/api/platform/plans-catalog")
    plan_codes = [p["code"] for p in plans_resp.json()["plans"]]
    assert "starter" not in plan_codes  # confirms the retired-plan scenario is real


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


# ---- Audit log viewer -------------------------------------------------


def test_super_admin_can_list_audit_logs(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    create_resp = client.post("/api/platform/companies", json={"name": "Audit Co", "slug": "audit-co"})
    company_id = create_resp.json()["id"]
    client.patch(f"/api/platform/companies/{company_id}/status", json={"status": "suspended"})

    resp = client.get("/api/platform/audit-logs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 2
    actions = [item["action"] for item in body["items"] if item["company_id"] == company_id]
    assert "company_created" in actions
    assert "company_status_set_suspended" in actions
    # newest first
    created_ats = [item["created_at"] for item in body["items"]]
    assert created_ats == sorted(created_ats, reverse=True)
    audited_row = next(item for item in body["items"] if item["action"] == "company_created" and item["company_id"] == company_id)
    assert audited_row["company_name"] == "Audit Co"


def test_audit_logs_filter_by_company_and_action(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    co1 = client.post("/api/platform/companies", json={"name": "Filter Co One", "slug": "filter-co-one"}).json()["id"]
    co2 = client.post("/api/platform/companies", json={"name": "Filter Co Two", "slug": "filter-co-two"}).json()["id"]
    client.patch(f"/api/platform/companies/{co1}/status", json={"status": "suspended"})

    by_company = client.get(f"/api/platform/audit-logs?company_id={co1}").json()
    assert all(item["company_id"] == co1 for item in by_company["items"])
    assert not any(item["company_id"] == co2 for item in by_company["items"])

    by_action = client.get("/api/platform/audit-logs?action=status_set").json()
    assert by_action["items"]
    assert all("status_set" in item["action"] for item in by_action["items"])


def test_non_super_admin_cannot_view_audit_logs(client_and_db):
    client = client_and_db(2, is_super_admin=False)
    resp = client.get("/api/platform/audit-logs")
    assert resp.status_code == 403


# ---- Revenue / MRR summary -------------------------------------------------


def test_new_trialing_company_does_not_count_toward_mrr(client_and_db):
    """Regression test: total_mrr used to sum 'active' AND 'trialing'
    subscriptions, so a batch of new trial signups inflated MRR before
    anyone paid a cent. A brand-new company (created on a trial, per
    create_company's own logic) must show up in trial_count only —
    never in total_mrr or the by_plan breakdown (both real-revenue-only)."""
    client = client_and_db(1, is_super_admin=True)
    before = client.get("/api/platform/revenue").json()

    client.post("/api/platform/companies", json={"name": "Revenue Co", "slug": "revenue-co", "plan_id": 1})

    resp = client.get("/api/platform/revenue")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_mrr"] == before["total_mrr"]
    assert body["trial_count"] >= before["trial_count"] + 1
    assert not any(row["plan_code"] == "starter" for row in body["by_plan"])


def test_active_paying_subscription_does_count_toward_mrr(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    all_plans = client.get("/api/platform/plans?active_only=false").json()["plans"]
    starter_price = next(p["price_monthly"] for p in all_plans if p["code"] == "starter")

    create_resp = client.post("/api/platform/companies", json={"name": "Paying Co", "slug": "paying-co", "plan_id": 1})
    company_id = create_resp.json()["id"]
    client.post(f"/api/platform/companies/{company_id}/plan", json={"plan_id": 1, "duration_days": 30})

    resp = client.get("/api/platform/revenue")
    body = resp.json()
    assert body["total_mrr"] >= starter_price
    starter_row = next(row for row in body["by_plan"] if row["plan_code"] == "starter")
    assert starter_row["active_subscriptions"] >= 1
    assert starter_row["mrr"] >= starter_price


def test_revenue_summary_excludes_cancelled_subscriptions(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    from database.database import db

    create_resp = client.post("/api/platform/companies", json={"name": "Cancel Co", "slug": "cancel-co", "plan_id": 1})
    company_id = create_resp.json()["id"]

    with db.connect() as conn:
        conn.execute(
            "UPDATE subscriptions SET status = 'cancelled' WHERE company_id = ?", (company_id,),
        )
        conn.commit()

    resp = client.get("/api/platform/revenue")
    body = resp.json()
    assert not any(row["plan_code"] == "starter" for row in body["by_plan"])


def test_non_super_admin_cannot_view_revenue(client_and_db):
    client = client_and_db(2, is_super_admin=False)
    resp = client.get("/api/platform/revenue")
    assert resp.status_code == 403


# ---- Plan-change history -------------------------------------------------


def test_subscription_history_lists_all_plan_changes(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    from database.database import db

    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO plans (name, code, price_monthly, max_users) VALUES ('History Pro', 'history-pro', 49.0, 10)"
        )
        pro_plan_id = cursor.lastrowid
        conn.commit()

    create_resp = client.post(
        "/api/platform/companies",
        json={"name": "History Co", "slug": "history-co", "plan_id": 1},
    )
    company_id = create_resp.json()["id"]
    client.post(f"/api/platform/companies/{company_id}/plan", json={"plan_id": pro_plan_id, "duration_days": 30})

    resp = client.get(f"/api/platform/companies/{company_id}/subscription-history")
    assert resp.status_code == 200, resp.text
    history = resp.json()["history"]
    assert len(history) == 2
    assert history[0]["plan_code"] == "history-pro"
    assert history[0]["status"] == "active"
    assert history[1]["plan_code"] == "starter"
    assert history[1]["status"] == "cancelled"


def test_subscription_history_404_for_missing_company(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    resp = client.get("/api/platform/companies/999999/subscription-history")
    assert resp.status_code == 404


def test_non_super_admin_cannot_view_subscription_history(client_and_db):
    client = client_and_db(1, is_super_admin=True)
    create_resp = client.post("/api/platform/companies", json={"name": "Hist Locked Co", "slug": "hist-locked-co"})
    company_id = create_resp.json()["id"]

    regular_client = client_and_db(2, is_super_admin=False)
    resp = regular_client.get(f"/api/platform/companies/{company_id}/subscription-history")
    assert resp.status_code == 403
