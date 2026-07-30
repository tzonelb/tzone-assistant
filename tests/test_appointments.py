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
    from backend.services.appointment_service import appointment_service
    from backend.services.customer_service import customer_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    appointment_service.ensure_schema()
    customer_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent One', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')")
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


def test_create_appointment_defaults(client_and_db):
    client = client_and_db
    resp = client.post("/api/appointments", json={"title": "Consultation", "scheduled_at": "2026-08-01T10:00:00Z"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "scheduled"
    assert body["duration_minutes"] == 30


def test_create_requires_title(client_and_db):
    client = client_and_db
    resp = client.post("/api/appointments", json={"title": "  ", "scheduled_at": "2026-08-01T10:00:00Z"})
    assert resp.status_code == 400


def test_create_rejects_invalid_datetime(client_and_db):
    client = client_and_db
    resp = client.post("/api/appointments", json={"title": "X", "scheduled_at": "not-a-date"})
    assert resp.status_code == 400


def test_create_rejects_nonpositive_duration(client_and_db):
    client = client_and_db
    resp = client.post("/api/appointments", json={"title": "X", "scheduled_at": "2026-08-01T10:00:00Z", "duration_minutes": 0})
    assert resp.status_code == 400


def test_create_rejects_unknown_customer(client_and_db):
    client = client_and_db
    resp = client.post("/api/appointments", json={"title": "X", "scheduled_at": "2026-08-01T10:00:00Z", "customer_id": 999})
    assert resp.status_code == 404


def test_create_rejects_unknown_employee(client_and_db):
    client = client_and_db
    resp = client.post("/api/appointments", json={"title": "X", "scheduled_at": "2026-08-01T10:00:00Z", "employee_user_id": 999})
    assert resp.status_code == 400


def test_create_linked_to_customer_and_employee(client_and_db):
    from backend.services.customer_service import customer_service
    contact = customer_service.upsert_from_channel(company_id=COMPANY_ID, channel="telegram", external_user_id="u1", display_name="Rami")

    client = client_and_db
    resp = client.post("/api/appointments", json={
        "title": "Fitting", "scheduled_at": "2026-08-01T10:00:00Z",
        "customer_id": contact["id"], "employee_user_id": 1,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["customer_name"] == "Rami"
    assert body["employee_name"] == "Agent One"


def test_list_filters_by_status_and_date_range(client_and_db):
    client = client_and_db
    client.post("/api/appointments", json={"title": "A", "scheduled_at": "2026-08-01T10:00:00"})
    client.post("/api/appointments", json={"title": "B", "scheduled_at": "2026-08-05T10:00:00"})

    resp = client.get("/api/appointments", params={"from_date": "2026-08-03T00:00:00"})
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "B"


def test_update_status_and_reschedule(client_and_db):
    client = client_and_db
    created = client.post("/api/appointments", json={"title": "A", "scheduled_at": "2026-08-01T10:00:00Z"}).json()
    resp = client.put(f"/api/appointments/{created['id']}", json={"status": "completed", "scheduled_at": "2026-08-02T11:00:00Z"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["scheduled_at"] == "2026-08-02T11:00:00Z"


def test_update_rejects_invalid_status(client_and_db):
    client = client_and_db
    created = client.post("/api/appointments", json={"title": "A", "scheduled_at": "2026-08-01T10:00:00Z"}).json()
    resp = client.put(f"/api/appointments/{created['id']}", json={"status": "whenever"})
    assert resp.status_code == 400


def test_update_unknown_appointment_404s(client_and_db):
    client = client_and_db
    resp = client.put("/api/appointments/9999", json={"status": "completed"})
    assert resp.status_code == 404


def test_delete_appointment(client_and_db):
    client = client_and_db
    created = client.post("/api/appointments", json={"title": "A", "scheduled_at": "2026-08-01T10:00:00Z"}).json()
    resp = client.delete(f"/api/appointments/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/appointments").json()["items"] == []


def test_delete_unknown_appointment_404s(client_and_db):
    client = client_and_db
    resp = client.delete("/api/appointments/9999")
    assert resp.status_code == 404


def test_appointments_isolated_per_company(client_and_db):
    from backend.services.appointment_service import appointment_service
    appointment_service.create_appointment(company_id=2, title="Other co", scheduled_at="2026-08-01T10:00:00Z")

    client = client_and_db
    items = client.get("/api/appointments").json()["items"]
    assert all(item["title"] != "Other co" for item in items)


def test_options_endpoint(client_and_db):
    client = client_and_db
    resp = client.get("/api/appointments/options")
    assert resp.status_code == 200
    body = resp.json()
    assert "scheduled" in body["statuses"]
    assert body["employees"][0]["full_name"] == "Agent One"
