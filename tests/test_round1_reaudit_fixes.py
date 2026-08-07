"""Regression tests for the four confirmed findings from re-auditing the
parallel branch's Round-1 security audit against this codebase:

1. Cross-tenant channel takeover: connect_whatsapp/messenger/instagram/
   telegram only checked "already connected to THIS company" -- another
   company could connect an identity that belongs to a different tenant,
   silently reclaiming/splitting that tenant's inbound webhook routing
   (resolve_meta_account routes by exactly these identifiers).
2. Last-admin lockout: update_user_assignment could demote/deactivate
   the only admin, and PATCH /roles/{id} could strip users.manage from
   the only admin-carrying role -- permanently locking the company out
   of its own user management.
3. Ops kill switch: automation_policy.is_bot_enabled() was defined but
   never consulted -- a channel disabled at the ops level kept
   auto-replying via the scripted flow. Engine.handle() now returns
   None for a disabled channel.
4. POST /test/whatsapp/ was unauthenticated -- a public way to drive
   the real message engine with attacker-controlled input.

Run with: python3 -m pytest tests/test_round1_reaudit_fixes.py -v
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_env():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    company_settings_service.ensure_schema()

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


# ---------------------------------------------------------------------
# 1. Cross-tenant channel takeover
# ---------------------------------------------------------------------


def test_connecting_another_companys_page_is_rejected(fresh_env):
    db, _auth = fresh_env
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) "
            "VALUES (2, 'Other Co', 'other-co', 1)"
        )
        # Company 1 already owns this Facebook page.
        conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, channel, name, page_id,
                access_token_encrypted, status, ai_enabled,
                created_at, updated_at
            ) VALUES (1, 'messenger', 'Their Page', 'PAGE_A', 'x', 'active', 1,
                      '2026-01-01', '2026-01-01')
            """
        )
        conn.commit()

    # Company 2 tries to connect the same page (Meta API mocked to
    # succeed -- the guard must fire regardless).
    with patch(
        "backend.services.channel_account_service.requests.get"
    ) as mock_get:
        mock_get.return_value.json.return_value = {"id": "PAGE_A", "name": "Their Page"}
        with pytest.raises(ChannelAccountError) as exc:
            channel_account_service.connect_messenger(
                company_id=2, page_id="PAGE_A", access_token="stolen-token"
            )
    assert "another company" in str(exc.value)

    # The rightful owner's row is untouched and no second row exists.
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT company_id FROM channel_accounts WHERE page_id = 'PAGE_A'"
        ).fetchall()
    assert [row["company_id"] for row in rows] == [1]


def test_reconnecting_your_own_page_is_still_allowed_error(fresh_env):
    """The guard must not break the legitimate 'already connected to this
    company' duplicate message for the SAME company."""
    db, _auth = fresh_env
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, channel, name, page_id,
                access_token_encrypted, status, ai_enabled,
                created_at, updated_at
            ) VALUES (1, 'messenger', 'My Page', 'PAGE_B', 'x', 'active', 1,
                      '2026-01-01', '2026-01-01')
            """
        )
        conn.commit()

    with patch(
        "backend.services.channel_account_service.requests.get"
    ) as mock_get:
        mock_get.return_value.json.return_value = {"id": "PAGE_B", "name": "My Page"}
        with pytest.raises(ChannelAccountError) as exc:
            channel_account_service.connect_messenger(
                company_id=1, page_id="PAGE_B", access_token="token"
            )
    assert "this company" in str(exc.value)


# ---------------------------------------------------------------------
# 2. Last-admin lockout guards
# ---------------------------------------------------------------------


def _seed_admin_and_member(db, auth_service):
    """Company 1 with exactly one admin (the seeded owner role) and one
    ordinary member on a no-permission role. Returns (admin_id,
    member_id, member_role_id, owner_role_id, admin_token)."""
    admin_id = auth_service.create_user(
        email="admin@test.local", password="a-strong-password", full_name="Admin"
    )
    auth_service.assign_user_to_company(admin_id, 1, role_code="owner")

    with db.connect() as conn:
        member_role_id = conn.execute(
            "INSERT INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Agent', 'agent', '', 0)"
        ).lastrowid
        conn.commit()

    member_id = auth_service.create_user(
        email="member@test.local", password="a-strong-password", full_name="Member"
    )
    auth_service.assign_user_to_company(member_id, 1, role_code="agent")

    with db.connect() as conn:
        owner_role_id = conn.execute(
            "SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'"
        ).fetchone()["id"]

    session = auth_service.create_session(admin_id, company_id=1)
    return admin_id, member_id, member_role_id, owner_role_id, session["access_token"]


def test_demoting_the_last_admin_is_rejected(fresh_env):
    db, auth_service = fresh_env
    from main import app

    admin_id, _member_id, member_role_id, _owner_role_id, token = (
        _seed_admin_and_member(db, auth_service)
    )

    with TestClient(app) as client:
        resp = client.patch(
            f"/api/admin/access/users/{admin_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "role_id": member_role_id,
                "branch_id": None,
                "status": "active",
                "departments": [],
            },
        )
    assert resp.status_code == 400
    assert "no administrator" in resp.json()["detail"].lower()


def test_demoting_an_admin_is_fine_when_another_admin_remains(fresh_env):
    db, auth_service = fresh_env
    from main import app

    admin_id, member_id, member_role_id, owner_role_id, token = (
        _seed_admin_and_member(db, auth_service)
    )
    # Promote the member to owner first -> two admins.
    with db.connect() as conn:
        conn.execute(
            "UPDATE company_users SET role_id = ? WHERE company_id = 1 AND user_id = ?",
            (owner_role_id, member_id),
        )
        conn.commit()

    with TestClient(app) as client:
        resp = client.patch(
            f"/api/admin/access/users/{admin_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "role_id": member_role_id,
                "branch_id": None,
                "status": "active",
                "departments": [],
            },
        )
    assert resp.status_code == 200


def test_stripping_users_manage_from_last_admin_role_is_rejected(fresh_env):
    db, auth_service = fresh_env
    from main import app

    # One admin whose power comes from a users.manage role (not owner).
    admin_id = auth_service.create_user(
        email="mgr@test.local", password="a-strong-password", full_name="Mgr"
    )
    with db.connect() as conn:
        role_id = conn.execute(
            "INSERT INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Manager', 'manager', '', 0)"
        ).lastrowid
        perm = conn.execute(
            "SELECT id FROM permissions WHERE code = 'users.manage'"
        ).fetchone()
        conn.execute(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
            (role_id, perm["id"]),
        )
        conn.commit()
    auth_service.assign_user_to_company(admin_id, 1, role_code="manager")
    # Remove the seeded owner membership if any (company 1 starts empty
    # of members in this fixture, so 'manager' is the only admin path).
    session = auth_service.create_session(admin_id, company_id=1)

    with TestClient(app) as client:
        resp = client.patch(
            f"/api/admin/access/roles/{role_id}",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            json={"permission_codes": ["dashboard.view"]},
        )
    assert resp.status_code == 400
    assert "no administrator" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------
# 3. Ops kill switch stops the whole engine
# ---------------------------------------------------------------------


def test_disabled_channel_makes_engine_return_none(fresh_env, monkeypatch, tmp_path):
    from core.automation_policy import automation_policy
    from core.engine import engine
    from core.request import Request

    policy_file = tmp_path / "automation_policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "default": {"bot_enabled": True, "ai_enabled": True, "ai_mode": "auto_reply"},
                "channels": {"whatsapp": {"bot_enabled": False}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(automation_policy, "POLICY_FILE", policy_file)

    response = engine.handle(
        Request(channel="whatsapp", user_id="kill_switch_user", message="hi")
    )
    assert response is None


# ---------------------------------------------------------------------
# 4. test_whatsapp debug endpoint requires auth
# ---------------------------------------------------------------------


def test_whatsapp_debug_endpoint_requires_authentication(fresh_env):
    from main import app

    with TestClient(app) as client:
        resp = client.post(
            "/test/whatsapp/", json={"user_id": "x", "message": "hi"}
        )
    assert resp.status_code == 401
