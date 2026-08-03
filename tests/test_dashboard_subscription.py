"""
Regression test: GET /api/dashboard/subscription used to skip the
require_dashboard_access() check its sibling endpoints (/summary,
/company, /channels) all enforce — any authenticated company member,
even one explicitly denied dashboard.view, could read the company's
plan/billing status through this one endpoint.
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

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'noperm@test.local', 'No Perm', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')")
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "noperm@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
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


def test_subscription_endpoint_requires_dashboard_view_permission(client_and_db):
    client = client_and_db
    resp = client.get("/api/dashboard/subscription")
    assert resp.status_code == 403


def test_company_endpoint_also_requires_dashboard_view_permission(client_and_db):
    """Sanity check that the sibling endpoint's existing gate still works,
    confirming this is an apples-to-apples comparison."""
    client = client_and_db
    resp = client.get("/api/dashboard/company")
    assert resp.status_code == 403
