"""
Real tests for Support Tickets — a company opens a support/maintenance
ticket to the T-ZONE platform team. Company-scoped and isolated.

Run with: python3 -m pytest tests/test_support_tickets.py -v
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
    from backend.services.support_ticket_service import support_ticket_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    support_ticket_service.ensure_schema()

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
        conn.commit()

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


def test_create_and_list_support_ticket(client_and_db):
    client = client_and_db
    create_resp = client.post(
        "/api/support-tickets",
        json={"subject": "Webhook failing", "description": "Deliveries return 500 since today.", "priority": "high"},
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    assert created["status"] == "open"
    assert created["priority"] == "high"

    list_resp = client.get("/api/support-tickets")
    assert list_resp.status_code == 200
    subjects = [t["subject"] for t in list_resp.json()["tickets"]]
    assert "Webhook failing" in subjects


def test_create_requires_subject_and_description(client_and_db):
    client = client_and_db
    resp = client.post("/api/support-tickets", json={"subject": "  ", "description": "text"})
    assert resp.status_code == 400


def test_invalid_priority_falls_back_to_normal(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/support-tickets",
        json={"subject": "Question", "description": "How do I export?", "priority": "bogus"},
    )
    assert resp.status_code == 200
    assert resp.json()["priority"] == "normal"


def test_support_tickets_are_scoped_per_company(client_and_db):
    from backend.services.support_ticket_service import support_ticket_service
    from database.database import db

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    support_ticket_service.create(
        company_id=2, subject="Other Co Ticket", description="not mine", actor_user_id=None,
    )

    client = client_and_db
    resp = client.get("/api/support-tickets")
    subjects = [t["subject"] for t in resp.json()["tickets"]]
    assert "Other Co Ticket" not in subjects
