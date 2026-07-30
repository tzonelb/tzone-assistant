"""
Real tests for the Analytics/Reports summary endpoint — basic
operational KPIs (channel mix, AI vs human, lifecycle stages, top
tags) aggregated read-only over customers/customer_identities/
conversations. Follows the same fixture pattern as
tests/test_customers_crm.py.

Run with: python -m pytest tests/test_analytics.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.customer_service import customer_service
    from backend.services.analytics_service import analytics_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    customer_service.ensure_schema()
    analytics_service.ensure_schema()

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

    from fastapi.testclient import TestClient
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


def _make_contact(company_id=COMPANY_ID, *, channel="telegram", external_user_id="u1", display_name="Rami"):
    from backend.services.customer_service import customer_service
    return customer_service.upsert_from_channel(
        company_id=company_id, channel=channel, external_user_id=external_user_id, display_name=display_name,
    )


def _make_conversation(*, company_id=COMPANY_ID, channel="telegram", external_user_id="u1", customer_id=None, ai_enabled=1):
    from database.database import db
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO conversations (company_id, channel, external_user_id, customer_id, status, ai_enabled, created_at) "
            "VALUES (?, ?, ?, ?, 'open', ?, datetime('now'))",
            (company_id, channel, external_user_id, customer_id, ai_enabled),
        )
        conn.commit()


def test_summary_returns_200_with_expected_top_level_keys(client_and_db):
    client = client_and_db
    resp = client.get("/api/analytics")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) >= {
        "total_contacts",
        "total_conversations",
        "new_contacts_last_30_days",
        "conversations_by_channel",
        "ai_vs_human",
        "contacts_by_lifecycle_stage",
        "top_tags",
    }


def test_summary_is_empty_shaped_with_no_data(client_and_db):
    client = client_and_db
    resp = client.get("/api/analytics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_contacts"] == 0
    assert body["total_conversations"] == 0
    assert body["new_contacts_last_30_days"] == 0
    assert body["conversations_by_channel"] == []
    assert body["ai_vs_human"] == {"ai_enabled": 0, "human": 0}
    assert body["contacts_by_lifecycle_stage"] == []
    assert body["top_tags"] == []


def test_counts_are_correct_after_creating_known_contacts_and_conversations(client_and_db):
    client = client_and_db

    a = _make_contact(external_user_id="a", display_name="A")
    b = _make_contact(external_user_id="b", channel="whatsapp", display_name="B")
    client.put(f"/api/customers/{a['id']}", json={"lifecycle_stage": "vip", "tags": ["wholesale", "tripoli"]})
    client.put(f"/api/customers/{b['id']}", json={"lifecycle_stage": "vip", "tags": ["wholesale"]})

    _make_conversation(channel="telegram", external_user_id="a", customer_id=a["id"], ai_enabled=1)
    _make_conversation(channel="telegram", external_user_id="a2", customer_id=a["id"], ai_enabled=0)
    _make_conversation(channel="whatsapp", external_user_id="b", customer_id=b["id"], ai_enabled=1)

    resp = client.get("/api/analytics")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_contacts"] == 2
    assert body["total_conversations"] == 3
    assert body["new_contacts_last_30_days"] == 2

    channel_counts = {item["channel"]: item["count"] for item in body["conversations_by_channel"]}
    assert channel_counts == {"telegram": 2, "whatsapp": 1}

    assert body["ai_vs_human"] == {"ai_enabled": 2, "human": 1}

    stage_counts = {item["stage"]: item["count"] for item in body["contacts_by_lifecycle_stage"]}
    assert stage_counts == {"vip": 2}

    top_tags = {item["tag"]: item["count"] for item in body["top_tags"]}
    assert top_tags == {"wholesale": 2, "tripoli": 1}


def test_new_contacts_last_30_days_excludes_older_contacts(client_and_db):
    from database.database import db

    client = client_and_db
    recent = _make_contact(external_user_id="recent", display_name="Recent")
    old = _make_contact(external_user_id="old", display_name="Old")

    with db.connect() as conn:
        conn.execute(
            "UPDATE customers SET first_seen_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", old["id"]),
        )
        conn.commit()

    resp = client.get("/api/analytics")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_contacts"] == 2
    assert body["new_contacts_last_30_days"] == 1
    _ = recent


def test_company_isolation_data_does_not_leak(client_and_db):
    from database.database import db

    client = client_and_db
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    a = _make_contact(company_id=COMPANY_ID, external_user_id="a", display_name="A")
    client.put(f"/api/customers/{a['id']}", json={"tags": ["own-co-tag"]})
    _make_conversation(company_id=COMPANY_ID, channel="telegram", external_user_id="a", customer_id=a["id"], ai_enabled=1)

    other = _make_contact(company_id=2, external_user_id="other-a", display_name="Other A")
    from backend.services.customer_service import customer_service
    customer_service.update_customer(
        company_id=2, customer_id=other["id"], values={"tags": ["other-co-tag"]}, actor_user_id=None,
    )
    _make_conversation(company_id=2, channel="whatsapp", external_user_id="other-a", customer_id=other["id"], ai_enabled=0)

    resp = client.get("/api/analytics")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_contacts"] == 1
    assert body["total_conversations"] == 1
    channel_counts = {item["channel"]: item["count"] for item in body["conversations_by_channel"]}
    assert channel_counts == {"telegram": 1}
    top_tags = {item["tag"] for item in body["top_tags"]}
    assert top_tags == {"own-co-tag"}
