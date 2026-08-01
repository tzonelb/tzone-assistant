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


def _make_conversation(
    *,
    company_id=COMPANY_ID,
    channel="telegram",
    external_user_id="u1",
    customer_id=None,
    ai_enabled=1,
    needs_human=0,
    created_at=None,
):
    from database.database import db
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO conversations "
            "(company_id, channel, external_user_id, customer_id, status, ai_enabled, needs_human, created_at) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?, COALESCE(?, datetime('now')))",
            (company_id, channel, external_user_id, customer_id, ai_enabled, needs_human, created_at),
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
        "conversation_volume_trend",
        "ai_vs_human_trend",
    }
    assert set(body["conversation_volume_trend"].keys()) >= {"days", "series"}
    assert set(body["ai_vs_human_trend"].keys()) >= {"days", "series", "note"}


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
    assert body["conversation_volume_trend"] == {"days": 30, "series": []}
    assert body["ai_vs_human_trend"]["days"] == 30
    assert body["ai_vs_human_trend"]["series"] == []


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


def _days_ago_iso(days):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_conversation_volume_trend_buckets_by_real_creation_day(client_and_db):
    client = client_and_db
    a = _make_contact(external_user_id="a", display_name="A")

    today = _days_ago_iso(0)
    yesterday = _days_ago_iso(1)
    _make_conversation(external_user_id="a1", customer_id=a["id"], created_at=today)
    _make_conversation(external_user_id="a2", customer_id=a["id"], created_at=today)
    _make_conversation(external_user_id="a3", customer_id=a["id"], created_at=yesterday)

    resp = client.get("/api/analytics")
    assert resp.status_code == 200, resp.text
    trend = resp.json()["conversation_volume_trend"]
    assert trend["days"] == 30

    counts_by_day = {point["date"]: point["count"] for point in trend["series"]}
    assert counts_by_day[today[:10]] == 2
    assert counts_by_day[yesterday[:10]] == 1
    # No interpolated/fabricated days — only real buckets are present.
    assert sum(counts_by_day.values()) == 3


def test_conversation_volume_trend_excludes_conversations_older_than_window(client_and_db):
    client = client_and_db
    a = _make_contact(external_user_id="a", display_name="A")

    _make_conversation(external_user_id="recent", customer_id=a["id"], created_at=_days_ago_iso(1))
    _make_conversation(external_user_id="old", customer_id=a["id"], created_at=_days_ago_iso(90))

    resp = client.get("/api/analytics?days=30")
    assert resp.status_code == 200, resp.text
    trend = resp.json()["conversation_volume_trend"]
    assert sum(point["count"] for point in trend["series"]) == 1


def test_ai_vs_human_trend_splits_by_creation_time_snapshot_fields(client_and_db):
    client = client_and_db
    a = _make_contact(external_user_id="a", display_name="A")

    today = _days_ago_iso(0)
    _make_conversation(external_user_id="a1", customer_id=a["id"], ai_enabled=1, needs_human=0, created_at=today)
    _make_conversation(external_user_id="a2", customer_id=a["id"], ai_enabled=0, needs_human=1, created_at=today)
    _make_conversation(external_user_id="a3", customer_id=a["id"], ai_enabled=1, needs_human=1, created_at=today)

    resp = client.get("/api/analytics")
    assert resp.status_code == 200, resp.text
    trend = resp.json()["ai_vs_human_trend"]
    assert trend["note"]  # non-empty honesty disclaimer about snapshot vs resolution

    day = next(point for point in trend["series"] if point["date"] == today[:10])
    assert day["ai_enabled_count"] == 2
    assert day["human_count"] == 1
    assert day["needs_human_count"] == 2


def test_trend_endpoints_respect_company_isolation(client_and_db):
    client = client_and_db
    with __import__("database.database", fromlist=["db"]).db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    a = _make_contact(company_id=COMPANY_ID, external_user_id="a", display_name="A")
    _make_conversation(company_id=COMPANY_ID, external_user_id="a", customer_id=a["id"], created_at=_days_ago_iso(0))

    other = _make_contact(company_id=2, external_user_id="other-a", display_name="Other A")
    _make_conversation(company_id=2, external_user_id="other-a", customer_id=other["id"], created_at=_days_ago_iso(0))

    resp = client.get("/api/analytics")
    assert resp.status_code == 200, resp.text
    trend = resp.json()["conversation_volume_trend"]
    assert sum(point["count"] for point in trend["series"]) == 1
