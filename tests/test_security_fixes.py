"""
Tests for the four security fixes made to:
  - backend/api/routes/knowledge.py (broken router + missing auth/scoping)
  - backend/api/routes/dashboard.py (get_subscription missing the
    require_dashboard_access check every other route in the file has)
  - backend/api/routes/customers.py (missing RBAC permission checks)
  - backend/api/routes/test_whatsapp.py (unauthenticated debug endpoint
    driving the live message-handling engine)

Run with: python3 -m pytest tests/test_security_fixes.py -v
"""
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test.

    Same approach as tests/test_conversation_ownership.py: mutating the
    existing singleton's db_path (rather than reimporting modules) is the
    reliable way to isolate tests against this codebase's module layout.
    """
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()

    yield db

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


def _make_company_user(db, *, company_id: int, user_id: int, permission_codes: list[str]):
    """Seed a company + role (with the given permissions) + membership.

    Mirrors the real seeding flow (roles/permissions/role_permissions from
    database.py) closely enough to exercise auth_service.has_permission()
    exactly as backend/api/routes/{dashboard,customers,knowledge}.py do.
    """
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workspaces (id, name, slug, status) VALUES (1, 'Test Workspace', 'test-workspace', 'active')",
        )
        conn.execute(
            "INSERT OR IGNORE INTO companies (id, workspace_id, name, slug) VALUES (?, 1, ?, ?)",
            (company_id, f"Company {company_id}", f"company-{company_id}"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status) VALUES (?, ?, ?, 'active')",
            (user_id, f"user{user_id}@test.local", f"User {user_id}"),
        )
        cursor = conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, ?, ?, 'test role', 0)
            """,
            (company_id, f"Role {user_id}", f"role_{user_id}_{company_id}"),
        )
        role_id = cursor.lastrowid

        if permission_codes:
            placeholders = ",".join("?" for _ in permission_codes)
            perm_rows = conn.execute(
                f"SELECT id FROM permissions WHERE code IN ({placeholders})",
                permission_codes,
            ).fetchall()
            conn.executemany(
                "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                [(role_id, row["id"]) for row in perm_rows],
            )

        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status)
            VALUES (?, ?, ?, 'active')
            """,
            (company_id, user_id, role_id),
        )
        conn.commit()

    return role_id


# ---------------------------------------------------------------------
# 1. knowledge.py / KnowledgeManager — the router 500'd on every request
#    because these methods did not exist at all. Verify the real,
#    company-scoped CRUD implementation.
# ---------------------------------------------------------------------

def test_faq_crud_round_trip(fresh_db):
    from core.knowledge_manager import knowledge_manager

    company_id = 1
    faq = {
        "id": "shipping_policy",
        "title_ar": "سياسة الشحن",
        "title_en": "Shipping Policy",
        "body_ar": "نص عربي",
        "body_en": "English body",
        "category": "Sales",
        "enabled": True,
    }

    saved = knowledge_manager.save_faq(company_id, "sales", faq)
    assert saved["id"] == "shipping_policy"
    assert saved["title_en"] == "Shipping Policy"
    assert saved["title_ar"] == "سياسة الشحن"
    assert saved["category"] == "Sales"
    assert saved["enabled"] is True

    listed = knowledge_manager.list_faqs(company_id, "sales")
    assert [item["id"] for item in listed] == ["shipping_policy"]

    fetched = knowledge_manager.get_faq(company_id, "sales", "shipping_policy")
    assert fetched["body_en"] == "English body"

    # Update (upsert on external_id) instead of duplicating the row.
    faq["body_en"] = "Updated body"
    faq["enabled"] = False
    knowledge_manager.save_faq(company_id, "sales", faq)
    updated = knowledge_manager.get_faq(company_id, "sales", "shipping_policy")
    assert updated["body_en"] == "Updated body"
    assert updated["enabled"] is False
    assert len(knowledge_manager.list_faqs(company_id, "sales")) == 1

    assert knowledge_manager.delete_faq(company_id, "sales", "shipping_policy") is True
    assert knowledge_manager.get_faq(company_id, "sales", "shipping_policy") is None
    assert knowledge_manager.delete_faq(company_id, "sales", "shipping_policy") is False


def test_faq_lookup_is_company_scoped(fresh_db):
    """The whole point of fix (a) is that this data is per-company now —
    one company must never see another company's FAQs."""
    from core.knowledge_manager import knowledge_manager

    # knowledge_items.company_id is a real FK to companies(id); company 1
    # is already seeded by db.create_tables(), company 2 needs seeding.
    with fresh_db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workspaces (id, name, slug, status) VALUES (1, 'Test Workspace', 'test-workspace', 'active')",
        )
        conn.execute(
            "INSERT OR IGNORE INTO companies (id, workspace_id, name, slug) VALUES (2, 1, 'Company 2', 'company-2')",
        )
        conn.commit()

    knowledge_manager.save_faq(1, "iptv", {
        "id": "faq_1", "title_en": "Company 1 FAQ", "enabled": True,
    })
    knowledge_manager.save_faq(2, "iptv", {
        "id": "faq_1", "title_en": "Company 2 FAQ", "enabled": True,
    })

    company_1_faqs = knowledge_manager.list_faqs(1, "iptv")
    company_2_faqs = knowledge_manager.list_faqs(2, "iptv")

    assert len(company_1_faqs) == 1
    assert company_1_faqs[0]["title_en"] == "Company 1 FAQ"
    assert len(company_2_faqs) == 1
    assert company_2_faqs[0]["title_en"] == "Company 2 FAQ"

    # Same external_id under a different company must not be visible.
    assert knowledge_manager.get_faq(2, "iptv", "faq_1")["title_en"] == "Company 2 FAQ"


def test_save_faq_requires_title_en(fresh_db):
    from core.knowledge_manager import knowledge_manager

    with pytest.raises(ValueError):
        knowledge_manager.save_faq(1, "iptv", {"id": "x", "title_en": ""})


# ---------------------------------------------------------------------
# 2/3. The RBAC gate shared verbatim by dashboard.get_subscription's new
# require_dashboard_access() call and customers.py's new
# _require_customer_access() call is auth_service.has_permission(). Prove
# it actually denies/allows correctly for the permission codes those
# fixes use ("dashboard.view", "conversations.view", "users.manage").
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "permission_code",
    ["dashboard.view", "conversations.view", "users.manage", "knowledge.view", "knowledge.manage"],
)
def test_has_permission_denies_role_without_grant(fresh_db, permission_code):
    from backend.services.auth_service import auth_service

    company_id = 5
    user_id = 501
    _make_company_user(fresh_db, company_id=company_id, user_id=user_id, permission_codes=[])

    assert auth_service.has_permission(
        user_id=user_id, company_id=company_id, permission_code=permission_code,
    ) is False


@pytest.mark.parametrize(
    "permission_code",
    ["dashboard.view", "conversations.view", "users.manage", "knowledge.view", "knowledge.manage"],
)
def test_has_permission_allows_role_with_grant(fresh_db, permission_code):
    from backend.services.auth_service import auth_service

    company_id = 6
    user_id = 601
    _make_company_user(
        fresh_db, company_id=company_id, user_id=user_id, permission_codes=[permission_code],
    )

    assert auth_service.has_permission(
        user_id=user_id, company_id=company_id, permission_code=permission_code,
    ) is True


def test_has_permission_denies_across_companies(fresh_db):
    """A permission grant in one company must not leak into another."""
    from backend.services.auth_service import auth_service

    _make_company_user(fresh_db, company_id=7, user_id=701, permission_codes=["dashboard.view"])

    assert auth_service.has_permission(
        user_id=701, company_id=8, permission_code="dashboard.view",
    ) is False


# ---------------------------------------------------------------------
# 2. dashboard.py get_subscription() now calls require_dashboard_access(),
# same as the other routes in the file. Exercise the real route with a
# minimal app + dependency override, the same way the route is actually
# wired in main.py.
# ---------------------------------------------------------------------

def test_get_subscription_endpoint_requires_dashboard_access(fresh_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes import dashboard as dashboard_module
    from backend.services.auth_service import get_current_user

    company_id = 9
    user_without_access = 901
    user_with_access = 902

    _make_company_user(fresh_db, company_id=company_id, user_id=user_without_access, permission_codes=[])
    _make_company_user(fresh_db, company_id=company_id, user_id=user_with_access, permission_codes=["dashboard.view"])

    app = FastAPI()
    app.include_router(dashboard_module.router)

    def _override(user_id):
        def _dep():
            return {"id": user_id, "active_company_id": company_id, "is_super_admin": False}
        return _dep

    app.dependency_overrides[get_current_user] = _override(user_without_access)
    client = TestClient(app)
    response = client.get(f"/api/dashboard/subscription?company_id={company_id}")
    assert response.status_code == 403

    app.dependency_overrides[get_current_user] = _override(user_with_access)
    response = client.get(f"/api/dashboard/subscription?company_id={company_id}")
    assert response.status_code == 200
    assert "subscription" in response.json()


# ---------------------------------------------------------------------
# 4. test_whatsapp.py must no longer be reachable without authentication
# — it drives the live message-handling engine with attacker-controlled
# input.
# ---------------------------------------------------------------------

def test_test_whatsapp_endpoint_requires_auth():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes import test_whatsapp as test_whatsapp_module

    app = FastAPI()
    app.include_router(test_whatsapp_module.router)
    client = TestClient(app)

    response = client.post("/test/whatsapp/", json={"message": "hello"})
    assert response.status_code == 401


def test_test_whatsapp_endpoint_works_when_authenticated(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes import test_whatsapp as test_whatsapp_module
    from backend.services.auth_service import get_current_user

    class _FakeResponse:
        text = "ok"
        buttons = []

    monkeypatch.setattr(
        test_whatsapp_module.message_gateway,
        "handle_text",
        lambda **kwargs: _FakeResponse(),
    )

    app = FastAPI()
    app.include_router(test_whatsapp_module.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "is_super_admin": True}
    client = TestClient(app)

    response = client.post("/test/whatsapp/", json={"message": "hello"})
    assert response.status_code == 200
    assert response.json()["reply"] == "ok"


# ---------------------------------------------------------------------
# 3. customers.py now gates every route behind has_permission() instead
# of only authentication + company membership.
# ---------------------------------------------------------------------

def test_customers_endpoints_require_permission(fresh_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes import customers as customers_module
    from backend.services.auth_service import get_current_user
    from backend.services.customer_service import customer_service

    company_id = 11
    user_without_access = 1101
    user_view_only = 1102
    user_full_access = 1103

    _make_company_user(fresh_db, company_id=company_id, user_id=user_without_access, permission_codes=[])
    _make_company_user(fresh_db, company_id=company_id, user_id=user_view_only, permission_codes=["conversations.view"])
    _make_company_user(
        fresh_db, company_id=company_id, user_id=user_full_access,
        permission_codes=["conversations.view", "users.manage"],
    )

    customer_service.ensure_schema()
    monkeypatch.setattr(
        customer_service, "list_customers",
        lambda **kwargs: {"items": [], "total": 0},
    )
    monkeypatch.setattr(
        customer_service, "update_customer",
        lambda **kwargs: {"id": kwargs["customer_id"], "notes": "updated"},
    )

    app = FastAPI()
    app.include_router(customers_module.router)

    def _override(user_id):
        return lambda: {"id": user_id, "active_company_id": company_id, "is_super_admin": False}

    # No permissions at all -> 403 on list.
    app.dependency_overrides[get_current_user] = _override(user_without_access)
    client = TestClient(app)
    assert client.get("/api/customers").status_code == 403
    assert client.put("/api/customers/1", json={"notes": "x"}).status_code == 403

    # View-only permission -> can list, cannot update.
    app.dependency_overrides[get_current_user] = _override(user_view_only)
    assert client.get("/api/customers").status_code == 200
    assert client.put("/api/customers/1", json={"notes": "x"}).status_code == 403

    # Full access -> both succeed.
    app.dependency_overrides[get_current_user] = _override(user_full_access)
    assert client.get("/api/customers").status_code == 200
    assert client.put("/api/customers/1", json={"notes": "x"}).status_code == 200
