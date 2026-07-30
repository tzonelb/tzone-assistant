"""
Real tests for the CRM/Contacts increment on top of the existing
Customers foundation: tags, lifecycle stage, list filters, and saved
Segments.

Run with: python3 -m pytest tests/test_customers_crm.py -v
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
    from backend.services.customer_service import customer_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    customer_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
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


def _make_contact(client, *, channel="telegram", external_user_id="u1", display_name="Rami"):
    from backend.services.customer_service import customer_service
    return customer_service.upsert_from_channel(
        company_id=COMPANY_ID, channel=channel, external_user_id=external_user_id, display_name=display_name,
    )


def test_new_contact_defaults_to_lead_stage_and_no_tags(client_and_db):
    contact = _make_contact(client_and_db)
    assert contact["lifecycle_stage"] == "lead"
    assert contact["tags"] == []


def test_options_endpoint_returns_fixed_lifecycle_pipeline(client_and_db):
    client = client_and_db
    resp = client.get("/api/customers/options")
    assert resp.status_code == 200
    assert resp.json()["lifecycle_stages"] == ["lead", "active", "customer", "vip", "churned"]


def test_update_lifecycle_stage_and_tags(client_and_db):
    client = client_and_db
    contact = _make_contact(client)
    resp = client.put(
        f"/api/customers/{contact['id']}",
        json={"lifecycle_stage": "vip", "tags": ["wholesale", "tripoli", "wholesale"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lifecycle_stage"] == "vip"
    assert body["tags"] == ["wholesale", "tripoli"]  # de-duplicated, order preserved


def test_rejects_invalid_lifecycle_stage(client_and_db):
    client = client_and_db
    contact = _make_contact(client)
    resp = client.put(f"/api/customers/{contact['id']}", json={"lifecycle_stage": "not-a-real-stage"})
    assert resp.status_code == 400


def test_list_filters_by_lifecycle_stage(client_and_db):
    client = client_and_db
    a = _make_contact(client, external_user_id="a", display_name="A")
    _make_contact(client, external_user_id="b", display_name="B")
    client.put(f"/api/customers/{a['id']}", json={"lifecycle_stage": "customer"})

    resp = client.get("/api/customers", params={"lifecycle_stage": "customer"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == a["id"]


def test_list_filters_by_tag(client_and_db):
    client = client_and_db
    a = _make_contact(client, external_user_id="a", display_name="A")
    _make_contact(client, external_user_id="b", display_name="B")
    client.put(f"/api/customers/{a['id']}", json={"tags": ["vip-supplier"]})

    resp = client.get("/api/customers", params={"tag": "vip-supplier"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == a["id"]


def test_create_and_apply_segment(client_and_db):
    client = client_and_db
    a = _make_contact(client, external_user_id="a", display_name="A")
    _make_contact(client, external_user_id="b", display_name="B")
    client.put(f"/api/customers/{a['id']}", json={"lifecycle_stage": "customer", "tags": ["reseller"]})

    create_resp = client.post(
        "/api/customer-segments",
        json={"name": "Active Resellers", "filters": {"lifecycle_stage": "customer", "tag": "reseller"}},
    )
    assert create_resp.status_code == 200, create_resp.text
    segment = create_resp.json()
    assert segment["filters"] == {"lifecycle_stage": "customer", "tag": "reseller"}

    list_resp = client.get("/api/customer-segments")
    assert any(item["name"] == "Active Resellers" for item in list_resp.json()["items"])

    apply_resp = client.get("/api/customers", params={"segment_id": segment["id"]})
    assert apply_resp.status_code == 200
    items = apply_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == a["id"]


def test_cannot_create_duplicate_segment_name(client_and_db):
    client = client_and_db
    client.post("/api/customer-segments", json={"name": "Leads", "filters": {}})
    resp = client.post("/api/customer-segments", json={"name": "leads", "filters": {}})
    assert resp.status_code == 400


def test_segment_rejects_invalid_lifecycle_stage_filter(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/customer-segments", json={"name": "Broken", "filters": {"lifecycle_stage": "nope"}}
    )
    assert resp.status_code == 400


def test_delete_segment(client_and_db):
    client = client_and_db
    created = client.post("/api/customer-segments", json={"name": "Temp", "filters": {}}).json()
    resp = client.delete(f"/api/customer-segments/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/customer-segments").json()["items"] == []


def test_segments_are_isolated_per_company(client_and_db):
    from database.database import db
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    from backend.services.customer_service import customer_service
    customer_service.create_segment(company_id=2, name="Other Co Segment", filters={}, actor_user_id=None)

    client = client_and_db
    resp = client.get("/api/customer-segments")
    assert all(item["name"] != "Other Co Segment" for item in resp.json()["items"])


def test_contacts_are_isolated_per_company(client_and_db):
    from database.database import db
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    from backend.services.customer_service import customer_service
    customer_service.upsert_from_channel(
        company_id=2, channel="telegram", external_user_id="other-co-user", display_name="Other Co Contact",
    )

    client = client_and_db
    resp = client.get("/api/customers")
    assert all(item["display_name"] != "Other Co Contact" for item in resp.json()["items"])
