"""
Real test for the admin take-over permission fix in
backend/api/routes/conversations.py (GET /{channel}/{user_id}/control).

Before this fix: `can_take_over` was `assigned_user_id is None or is_owner`
- an admin viewing a conversation owned by another employee got no way
to take it over directly (only Release / Return to AI, a two-step path).
Business rule requested: admin/owner can take over directly. Employee
vs employee must stay blocked exactly as before - this test verifies
both sides.

Run with: python3 -m pytest tests/test_admin_takeover_permission.py -v
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
CHANNEL = "messenger"
CUSTOMER_ID = "test_customer_admin_perm"


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (101, 'employee1@test.local', 'Employee One', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (202, 'employee2@test.local', 'Employee Two', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (999, 'admin@test.local', 'Admin', 'active', 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO companies (id, name) VALUES (1, 'Test Co')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 101, 'active')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 202, 'active')"
        )
        conn.commit()

    conversation_control_service.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=101,
    )

    from backend.main import app
    from backend.services.auth_service import get_current_user

    def make_client_as(user_id, is_super_admin=False):
        async def _override():
            return {
                "id": user_id,
                "email": f"user{user_id}@test.local",
                "is_super_admin": is_super_admin,
                "active_company_id": COMPANY_ID,
            }
        app.dependency_overrides[get_current_user] = _override
        return TestClient(app)

    yield make_client_as

    app.dependency_overrides.clear()
    db.db_path = original_path

    # Same Windows-only cleanup issue fixed in test_conversation_ownership.py
    # (UPDATE_002): sqlite3 connections aren't fully released just by
    # exiting `with db.connect() as conn:`, so os.remove() can hit a
    # PermissionError even though every assertion already passed. Forgot
    # to carry this fix into this newer test file the first time.
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            break
        except PermissionError:
            time.sleep(0.1)


def test_admin_gets_can_take_over_true_on_employee_owned_conversation(client_and_db):
    make_client_as = client_and_db
    client = make_client_as(999, is_super_admin=True)
    resp = client.get(f"/conversations/{CHANNEL}/{CUSTOMER_ID}/control")
    assert resp.status_code == 200
    assert resp.json()["permissions"]["can_take_over"] is True


def test_other_employee_still_gets_can_take_over_false_on_employee_owned_conversation(client_and_db):
    """The protection this must NOT break: employee vs employee stays blocked."""
    make_client_as = client_and_db
    client = make_client_as(202, is_super_admin=False)
    resp = client.get(f"/conversations/{CHANNEL}/{CUSTOMER_ID}/control")
    assert resp.status_code == 200
    assert resp.json()["permissions"]["can_take_over"] is False


def test_owner_still_gets_can_take_over_true(client_and_db):
    make_client_as = client_and_db
    client = make_client_as(101, is_super_admin=False)
    resp = client.get(f"/conversations/{CHANNEL}/{CUSTOMER_ID}/control")
    assert resp.status_code == 200
    assert resp.json()["permissions"]["can_take_over"] is True
