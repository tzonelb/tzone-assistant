"""
Tests for Chatbot Control (the consolidated bot-behaviour settings section,
stored under the "ai_behavior" company-settings key).

Covers:
- The section round-trips: save then load returns the saved values.
- Auto-read is always reported as True even if stored False or absent, and the
  dedicated consumption helper reports it as always-on.

Run with: python3 -m pytest tests/test_chatbot_control_settings.py -v
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
    from backend.services.company_settings_service import company_settings_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    company_settings_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
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


def test_chatbot_control_section_round_trips(client_and_db):
    client = client_and_db
    resp = client.put(
        "/api/company-settings/ai_behavior",
        json={"values": {
            "mode": "human_first",
            "greeting_message": "Welcome to T-ZONE!",
            "reply_access_mode": "shared_until_taken",
            "return_to_ai_timeout_minutes": 9,
            "auto_release_to_ai": False,
        }},
    )
    assert resp.status_code == 200, resp.text

    load = client.get("/api/company-settings/ai_behavior")
    assert load.status_code == 200
    values = load.json()["values"]
    assert values["mode"] == "human_first"
    assert values["greeting_message"] == "Welcome to T-ZONE!"
    assert values["reply_access_mode"] == "shared_until_taken"
    assert values["return_to_ai_timeout_minutes"] == 9
    assert values["auto_release_to_ai"] is False


def test_auto_read_is_always_true_when_absent(client_and_db):
    client = client_and_db
    values = client.get("/api/company-settings/ai_behavior").json()["values"]
    assert values["auto_read"] is True


def test_auto_read_forced_true_even_if_stored_false(client_and_db):
    client = client_and_db
    # Try to persist auto_read disabled — the read path must still report True.
    client.put(
        "/api/company-settings/ai_behavior",
        json={"values": {"auto_read": False}},
    )
    values = client.get("/api/company-settings/ai_behavior").json()["values"]
    assert values["auto_read"] is True


def test_auto_read_consumption_helper_is_always_on(client_and_db):
    from backend.services.company_settings_service import company_settings_service
    assert company_settings_service.is_auto_read_enabled(COMPANY_ID) is True


def test_retired_auto_read_mode_is_not_exposed(client_and_db):
    client = client_and_db
    values = client.get("/api/company-settings/ai_behavior").json()["values"]
    assert "auto_read_mode" not in values
