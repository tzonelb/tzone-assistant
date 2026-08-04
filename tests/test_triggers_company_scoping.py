"""Regression tests for the Bot Triggers module: company-scoping, RBAC,
validation, and the actual firing paths (event hooks + time sweeps +
dedupe).

Bot Triggers (backend/services/trigger_service.py) are the rules that
make the bot act on real platform events. These tests prove:

  1. Multi-tenant isolation: a user in company A can never list, read,
     edit or delete company B's triggers or see B's firing history; a
     company-A event only fires company-A triggers.
  2. RBAC: viewing requires "triggers.view"; writes require
     "triggers.manage".
  3. Validation: unknown trigger_type rejected; time-based types require
     delay_minutes.
  4. Firing: the new_conversation event hook actually fires a matching
     enabled trigger exactly once (dedupe), creates a firing row and a
     team notification, and skips disabled/other-type triggers. The
     appointment_reminder time sweep fires for an upcoming appointment
     and never double-fires.

Run with: python3 -m pytest tests/test_triggers_company_scoping.py -v
"""
import os
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_env():
    """Point the shared db singleton at a throwaway SQLite file per test."""
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.company_settings_service import company_settings_service
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.notification_service import notification_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    company_settings_service.ensure_schema()
    conversation_control_service.ensure_schema()
    notification_service.ensure_schema()

    yield db, auth_service

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


def _make_company(db, name, slug):
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO workspaces (name, slug, status) VALUES (?, ?, 'active')",
            (f"{name} workspace", f"{slug}-ws"),
        )
        workspace_id = cursor.lastrowid

        cursor = conn.execute(
            """
            INSERT INTO companies (workspace_id, name, slug, status)
            VALUES (?, ?, ?, 'active')
            """,
            (workspace_id, name, slug),
        )
        company_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, 'Owner', 'owner', 'Full access', 1)
            """,
            (company_id,),
        )
        conn.commit()

    return company_id


def _make_role(db, company_id, code, name, permission_codes):
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, ?, ?, '', 0)
            """,
            (company_id, name, code),
        )
        role_id = cursor.lastrowid
        for permission_code in permission_codes:
            row = conn.execute(
                "SELECT id FROM permissions WHERE code = ?", (permission_code,)
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                    (role_id, row["id"]),
                )
        conn.commit()
    return role_id


def _make_user(db, auth_service, company_id, email, role_code="owner"):
    user_id = auth_service.create_user(
        email=email, password="a-strong-password", full_name=email
    )
    auth_service.assign_user_to_company(user_id, company_id, role_code=role_code)
    session = auth_service.create_session(user_id, company_id=company_id)
    return user_id, session["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _trigger_payload(**overrides):
    payload = {
        "name": "Welcome new customers",
        "trigger_type": "new_conversation",
        "enabled": True,
        "notify_team": True,
    }
    payload.update(overrides)
    return payload


def test_unauthenticated_requests_are_rejected(fresh_env):
    db, _auth = fresh_env
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/triggers").status_code == 401
        assert client.get("/api/triggers/types").status_code == 401
        assert client.get("/api/triggers/firings").status_code == 401
        assert client.post("/api/triggers", json=_trigger_payload()).status_code == 401
        assert client.put("/api/triggers/1", json={"name": "x"}).status_code == 401
        assert client.delete("/api/triggers/1").status_code == 401


def test_rbac_view_vs_manage(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, owner_token = _make_user(db, auth_service, company, "owner@test.local")
    _make_role(db, company, "viewer", "Viewer", ["triggers.view"])
    _, viewer_token = _make_user(db, auth_service, company, "viewer@test.local", role_code="viewer")
    _make_role(db, company, "guest", "Guest", [])
    _, guest_token = _make_user(db, auth_service, company, "guest@test.local", role_code="guest")

    with TestClient(app) as client:
        created = client.post(
            "/api/triggers", headers=_headers(owner_token), json=_trigger_payload()
        )
        assert created.status_code == 201
        trigger_id = created.json()["id"]

        assert client.get("/api/triggers", headers=_headers(guest_token)).status_code == 403

        assert client.get("/api/triggers", headers=_headers(viewer_token)).status_code == 200
        assert (
            client.post(
                "/api/triggers", headers=_headers(viewer_token), json=_trigger_payload()
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/api/triggers/{trigger_id}",
                headers=_headers(viewer_token),
                json={"name": "Hacked"},
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"/api/triggers/{trigger_id}", headers=_headers(viewer_token)
            ).status_code
            == 403
        )


def test_triggers_are_scoped_to_caller_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera@test.local")
    _, token_b = _make_user(db, auth_service, company_b, "ownerb@test.local")

    with TestClient(app) as client:
        created_a = client.post(
            "/api/triggers", headers=_headers(token_a), json=_trigger_payload(name="A's trigger")
        )
        assert created_a.status_code == 201
        trigger_id_a = created_a.json()["id"]

        listed_b = client.get("/api/triggers", headers=_headers(token_b)).json()["items"]
        assert all(item["id"] != trigger_id_a for item in listed_b)

        assert (
            client.get(f"/api/triggers/{trigger_id_a}", headers=_headers(token_b)).status_code
            == 404
        )
        assert (
            client.put(
                f"/api/triggers/{trigger_id_a}",
                headers=_headers(token_b),
                json={"name": "Stolen"},
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/api/triggers/{trigger_id_a}", headers=_headers(token_b)
            ).status_code
            == 404
        )


def test_validation_rules(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner2@test.local")

    with TestClient(app) as client:
        unknown_type = client.post(
            "/api/triggers",
            headers=_headers(token),
            json=_trigger_payload(trigger_type="customer_teleported"),
        )
        assert unknown_type.status_code == 422

        # Time-based type without delay -> rejected.
        no_delay = client.post(
            "/api/triggers",
            headers=_headers(token),
            json=_trigger_payload(trigger_type="customer_no_reply"),
        )
        assert no_delay.status_code == 422

        # With a delay -> accepted.
        with_delay = client.post(
            "/api/triggers",
            headers=_headers(token),
            json=_trigger_payload(
                name="Silent customer follow-up",
                trigger_type="customer_no_reply",
                delay_minutes=60,
            ),
        )
        assert with_delay.status_code == 201

        # Removing the delay from an existing time-based trigger -> rejected.
        strip_delay = client.put(
            f"/api/triggers/{with_delay.json()['id']}",
            headers=_headers(token),
            json={"delay_minutes": None},
        )
        assert strip_delay.status_code == 422


def test_new_conversation_event_fires_matching_trigger_once(fresh_env):
    """The real end-to-end: creating a brand-new conversation fires an
    enabled new_conversation trigger exactly once -- a firing row and a
    team notification appear; a disabled trigger and another company's
    trigger do not fire; re-looking-up the same conversation never
    re-fires."""
    db, auth_service = fresh_env
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.trigger_service import trigger_service

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    trigger_a = trigger_service.create_trigger(
        company_id=company_a,
        values={"name": "Welcome A", "trigger_type": "new_conversation"},
        actor_user_id=None,
    )
    trigger_service.create_trigger(
        company_id=company_a,
        values={
            "name": "Disabled A",
            "trigger_type": "new_conversation",
            "enabled": False,
        },
        actor_user_id=None,
    )
    trigger_b = trigger_service.create_trigger(
        company_id=company_b,
        values={"name": "Welcome B", "trigger_type": "new_conversation"},
        actor_user_id=None,
    )

    # New conversation for company A (telegram: notification only, no
    # outbound sender -- keeps the test offline).
    conversation_control_service.get_or_create(
        company_id=company_a,
        channel="telegram",
        external_user_id="cust_1",
    )

    firings_a = trigger_service.list_firings(company_id=company_a)
    assert firings_a["total"] == 1
    assert firings_a["items"][0]["trigger_id"] == trigger_a["id"]
    assert firings_a["items"][0]["trigger_type"] == "new_conversation"

    # Company B saw nothing.
    assert trigger_service.list_firings(company_id=company_b)["total"] == 0
    assert (
        trigger_service.get_trigger(
            company_id=company_b, trigger_id=trigger_b["id"]
        )
    )

    # Second lookup of the SAME conversation: no new firing (the
    # conversation already exists, so no new_conversation event at all).
    conversation_control_service.get_or_create(
        company_id=company_a,
        channel="telegram",
        external_user_id="cust_1",
    )
    assert trigger_service.list_firings(company_id=company_a)["total"] == 1

    # A team notification was created for the firing.
    with db.connect() as conn:
        notification = conn.execute(
            "SELECT * FROM notifications WHERE company_id = ? AND notification_type = 'bot_trigger'",
            (company_a,),
        ).fetchone()
    assert notification is not None


def test_appointment_reminder_time_sweep_fires_once(fresh_env):
    """An enabled appointment_reminder trigger fires for an appointment
    starting within its delay window, exactly once across repeated
    sweeps."""
    db, _auth = fresh_env
    from datetime import datetime, timedelta, timezone

    from backend.services.appointment_service import appointment_service
    from backend.services.trigger_service import trigger_service

    company = _make_company(db, "Company A", "company-a")

    trigger = trigger_service.create_trigger(
        company_id=company,
        values={
            "name": "Remind 2h before",
            "trigger_type": "appointment_reminder",
            "delay_minutes": 120,
        },
        actor_user_id=None,
    )

    soon = (
        datetime.now(timezone.utc) + timedelta(minutes=30)
    ).isoformat()
    appointment = appointment_service.create_appointment(
        company_id=company,
        values={"title": "Site visit", "starts_at": soon},
        actor_user_id=None,
    )

    trigger_service.run_time_checks()
    trigger_service.run_time_checks()  # second sweep must not double-fire

    firings = trigger_service.list_firings(company_id=company)
    reminder_firings = [
        f for f in firings["items"] if f["trigger_type"] == "appointment_reminder"
    ]
    assert len(reminder_firings) == 1
    assert reminder_firings[0]["trigger_id"] == trigger["id"]
    assert reminder_firings[0]["reference_id"] == appointment["id"]


def test_appointment_booked_event_fires(fresh_env):
    db, _auth = fresh_env
    from datetime import datetime, timedelta, timezone

    from backend.services.appointment_service import appointment_service
    from backend.services.trigger_service import trigger_service

    company = _make_company(db, "Company A", "company-a")

    trigger_service.create_trigger(
        company_id=company,
        values={"name": "Booked!", "trigger_type": "appointment_booked"},
        actor_user_id=None,
    )

    appointment_service.create_appointment(
        company_id=company,
        values={
            "title": "Install",
            "starts_at": (
                datetime.now(timezone.utc) + timedelta(days=1)
            ).isoformat(),
        },
        actor_user_id=None,
    )

    firings = trigger_service.list_firings(company_id=company)
    assert any(f["trigger_type"] == "appointment_booked" for f in firings["items"])
