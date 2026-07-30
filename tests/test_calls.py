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
    from backend.services.call_log_service import call_log_service
    from backend.services.customer_service import customer_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    call_log_service.ensure_schema()
    customer_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
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


def test_create_call_log_by_phone_number(client_and_db):
    client = client_and_db
    resp = client.post("/api/calls", json={"direction": "outbound", "phone_number": "+96170000000", "duration_seconds": 90})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["duration_seconds"] == 90


def test_create_requires_phone_or_customer(client_and_db):
    client = client_and_db
    resp = client.post("/api/calls", json={"direction": "inbound"})
    assert resp.status_code == 400


def test_create_rejects_invalid_direction(client_and_db):
    client = client_and_db
    resp = client.post("/api/calls", json={"direction": "sideways", "phone_number": "123"})
    assert resp.status_code == 400


def test_create_rejects_negative_duration(client_and_db):
    client = client_and_db
    resp = client.post("/api/calls", json={"direction": "inbound", "phone_number": "123", "duration_seconds": -5})
    assert resp.status_code == 400


def test_create_linked_to_customer_includes_customer_name(client_and_db):
    from backend.services.customer_service import customer_service
    contact = customer_service.upsert_from_channel(company_id=COMPANY_ID, channel="telegram", external_user_id="u1", display_name="Rami")

    client = client_and_db
    resp = client.post("/api/calls", json={"direction": "inbound", "customer_id": contact["id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["customer_name"] == "Rami"


def test_create_rejects_unknown_customer(client_and_db):
    client = client_and_db
    resp = client.post("/api/calls", json={"direction": "inbound", "customer_id": 9999})
    assert resp.status_code == 404


def test_list_filters_by_direction_and_status(client_and_db):
    client = client_and_db
    client.post("/api/calls", json={"direction": "inbound", "phone_number": "1", "status": "missed"})
    client.post("/api/calls", json={"direction": "outbound", "phone_number": "2", "status": "completed"})

    resp = client.get("/api/calls", params={"direction": "inbound"})
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "missed"

    resp = client.get("/api/calls", params={"status": "completed"})
    assert len(resp.json()["items"]) == 1


def test_delete_call_log(client_and_db):
    client = client_and_db
    created = client.post("/api/calls", json={"direction": "inbound", "phone_number": "1"}).json()
    resp = client.delete(f"/api/calls/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/calls").json()["items"] == []


def test_delete_unknown_call_404s(client_and_db):
    client = client_and_db
    resp = client.delete("/api/calls/9999")
    assert resp.status_code == 404


def test_calls_are_isolated_per_company(client_and_db):
    from backend.services.call_log_service import call_log_service
    call_log_service.create_call_log(company_id=2, direction="inbound", phone_number="999")

    client = client_and_db
    items = client.get("/api/calls").json()["items"]
    assert all(item["phone_number"] != "999" for item in items)
