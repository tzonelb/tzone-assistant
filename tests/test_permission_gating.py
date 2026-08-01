"""
Real end-to-end tests proving the new permission-code gates
(auth_service.require_permission) actually work over HTTP, for the
~15 route files that were wired up to real per-permission-code
enforcement instead of being open to any logged-in company member.

For each gated action: a plain employee (company_users.role_id = NULL,
so has_permission() finds no role and returns False) must get 403, and
either the seeded 'owner' role (which bypasses all permission checks)
or a custom role with the specific permission_code granted via
role_permissions must succeed. A few explicitly-NOT-gated endpoints
are also checked to confirm they were not accidentally over-gated.

Outbound HTTP calls (Telegram/Meta/OpenAI) are mocked - no real network
calls are made.

Run with: .venv/Scripts/python.exe -m pytest tests/test_permission_gating.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1
OWNER_ID = 1
EMPLOYEE_ID = 2


@pytest.fixture()
def env():
    """Spins up a temp sqlite db, ensures every schema this test file
    touches exists, seeds a company with an 'owner' user (bypasses all
    permission checks) and a plain employee (role_id = NULL, i.e. zero
    permissions), and returns everything a test needs to build clients
    for either identity."""
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.company_settings_service import company_settings_service
    from backend.services.security_verification_service import security_verification_service
    from backend.services.broadcast_service import broadcast_service
    from backend.services.catalogue_service import catalogue_service
    from backend.services.scheduled_post_service import scheduled_post_service
    from backend.services.department_service import department_service
    from backend.services.team_chat_service import team_chat_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.customer_service import customer_service
    from backend.services.call_log_service import call_log_service
    from backend.services.platform_admin_service import platform_admin_service
    from backend.services.facebook_oauth_service import facebook_oauth_service
    from backend.services.message_status_service import message_status_service
    from core.instruction_service import instruction_service
    from core.knowledge_manager import knowledge_manager

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    company_settings_service.ensure_schema()
    security_verification_service.ensure_schema()
    broadcast_service.ensure_schema()
    catalogue_service.ensure_schema()
    scheduled_post_service.ensure_schema()
    department_service.ensure_schema()
    team_chat_service.ensure_schema()
    conversation_control_service.ensure_schema()
    customer_service.ensure_schema()
    call_log_service.ensure_schema()
    platform_admin_service.ensure_schema()
    facebook_oauth_service.ensure_schema()
    message_status_service.ensure_schema()
    instruction_service.ensure_schema()
    knowledge_manager.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (?, 'owner@test.local', 'Owner', 'active', 0)",
            (OWNER_ID,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (?, 'employee@test.local', 'Employee', 'active', 0)",
            (EMPLOYEE_ID,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Owner', 'owner', 'Full access', 1)"
        )
        owner_role_id = conn.execute(
            "SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, ?, ?, 'active')",
            (OWNER_ID, owner_role_id),
        )
        # Plain employee: a company_users row with NO role_id at all -
        # has_permission()'s join to roles finds nothing -> False for
        # every permission code.
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, ?, NULL, 'active')",
            (EMPLOYEE_ID,),
        )
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    def make_client(user_id: int) -> TestClient:
        async def _override():
            return {
                "id": user_id,
                "email": "owner@test.local" if user_id == OWNER_ID else "employee@test.local",
                "is_super_admin": False,
                "active_company_id": COMPANY_ID,
            }
        app.dependency_overrides[get_current_user] = _override
        return TestClient(app)

    yield make_client

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


def _grant_permission(role_code: str, role_name: str, permission_code: str) -> int:
    """Creates (or reuses) a custom company role holding exactly one
    permission_code, granted via role_permissions - the "specific
    permission via a custom role" path, as distinct from the
    owner-bypasses-everything path used for most of the success checks
    below."""
    from database.database import db

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, ?, ?, '', 0)",
            (role_name, role_code),
        )
        role_id = conn.execute(
            "SELECT id FROM roles WHERE company_id = 1 AND code = ?", (role_code,)
        ).fetchone()["id"]
        permission_id = conn.execute(
            "SELECT id FROM permissions WHERE code = ?", (permission_code,)
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
            (role_id, permission_id),
        )
        conn.commit()
    return role_id


def _assign_role(user_id: int, role_id: int) -> None:
    from database.database import db

    with db.connect() as conn:
        conn.execute(
            "UPDATE company_users SET role_id = ? WHERE company_id = 1 AND user_id = ?",
            (role_id, user_id),
        )
        conn.commit()


# ===========================================================================
# settings.manage - PUT /api/company-settings/{section}
# ===========================================================================

def test_employee_cannot_update_company_settings(env):
    client = env(EMPLOYEE_ID)
    resp = client.put("/api/company-settings/branding", json={"values": {"display_name": "Hacked"}})
    assert resp.status_code == 403


def test_owner_can_update_company_settings(env):
    client = env(OWNER_ID)
    resp = client.put("/api/company-settings/branding", json={"values": {"display_name": "New Name"}})
    assert resp.status_code == 200, resp.text


def test_custom_role_with_settings_manage_can_update_company_settings(env):
    """Exercises the role_permissions grant path (not just owner bypass)."""
    role_id = _grant_permission("settings_admin", "Settings Admin", "settings.manage")
    _assign_role(EMPLOYEE_ID, role_id)
    client = env(EMPLOYEE_ID)
    resp = client.put("/api/company-settings/branding", json={"values": {"display_name": "Granted"}})
    assert resp.status_code == 200, resp.text


# ===========================================================================
# GET /api/auth/me exposes real permission_codes per company (not just
# role_code) - the frontend nav/UI gates on this instead of a
# role_code == "owner" heuristic that misses custom roles entirely.
# ===========================================================================

def test_me_exposes_granted_permission_codes_for_custom_role(env):
    role_id = _grant_permission("analytics_viewer", "Analytics Viewer", "analytics.view")
    _assign_role(EMPLOYEE_ID, role_id)
    client = env(EMPLOYEE_ID)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200, resp.text
    company = next(c for c in resp.json()["companies"] if c["id"] == COMPANY_ID)
    assert company["permission_codes"] == ["analytics.view"]


def test_me_permission_codes_empty_for_zero_permission_employee(env):
    client = env(EMPLOYEE_ID)
    resp = client.get("/api/auth/me")
    company = next(c for c in resp.json()["companies"] if c["id"] == COMPANY_ID)
    assert company["permission_codes"] == []


def test_me_permission_codes_empty_for_owner_since_owner_bypasses_by_role_code(env):
    client = env(OWNER_ID)
    resp = client.get("/api/auth/me")
    company = next(c for c in resp.json()["companies"] if c["id"] == COMPANY_ID)
    assert company["role_code"] == "owner"
    assert company["permission_codes"] == []


# ===========================================================================
# channels.manage - POST /api/channels/telegram/connect (elevated-token
# gate is bypassed via mock so we isolate the NEW permission check)
# ===========================================================================

def _fake_getme():
    resp = MagicMock()
    resp.json.return_value = {"ok": True, "result": {"id": 999, "username": "bot", "is_bot": True}}
    return resp


def test_employee_cannot_connect_telegram(env):
    client = env(EMPLOYEE_ID)
    with patch(
        "backend.services.security_verification_service.security_verification_service.check_elevated",
        return_value=True,
    ):
        resp = client.post(
            "/api/channels/telegram/connect",
            json={"bot_token": "123:ABC"},
            headers={"X-Elevated-Token": "fake"},
        )
    assert resp.status_code == 403


def test_owner_can_connect_telegram(env):
    client = env(OWNER_ID)
    with patch(
        "backend.services.security_verification_service.security_verification_service.check_elevated",
        return_value=True,
    ), patch("backend.services.channel_account_service.requests.get", return_value=_fake_getme()):
        resp = client.post(
            "/api/channels/telegram/connect",
            json={"bot_token": "123:ABC"},
            headers={"X-Elevated-Token": "fake"},
        )
    assert resp.status_code == 200, resp.text


# ===========================================================================
# channels.manage - GET /api/channels/facebook/oauth/start
# ===========================================================================

def test_employee_cannot_start_facebook_oauth(env):
    client = env(EMPLOYEE_ID)
    resp = client.get("/api/channels/facebook/oauth/start")
    assert resp.status_code == 403


def test_owner_can_start_facebook_oauth(env):
    client = env(OWNER_ID)
    with patch("backend.services.facebook_oauth_service.config.META_APP_ID", "fake-app-id"), \
         patch("backend.services.facebook_oauth_service.config.META_OAUTH_REDIRECT_URI", "https://example.test/cb"):
        resp = client.get("/api/channels/facebook/oauth/start")
    assert resp.status_code == 200, resp.text
    assert "authorize_url" in resp.json()


# ===========================================================================
# subscriptions.manage - POST /api/platform/subscription-requests
# ===========================================================================

def _seed_plan() -> int:
    from database.database import db

    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO plans (name, code, price_monthly, status) VALUES ('Pro', 'pro', 49, 'active')"
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_employee_cannot_request_subscription_change(env):
    plan_id = _seed_plan()
    client = env(EMPLOYEE_ID)
    resp = client.post("/api/platform/subscription-requests", json={"plan_id": plan_id})
    assert resp.status_code == 403


def test_owner_can_request_subscription_change(env):
    plan_id = _seed_plan()
    client = env(OWNER_ID)
    resp = client.post("/api/platform/subscription-requests", json={"plan_id": plan_id})
    assert resp.status_code == 200, resp.text


# ===========================================================================
# broadcasts - channels.manage (create/send), channels.view (list)
# ===========================================================================

def test_employee_cannot_create_broadcast(env):
    client = env(EMPLOYEE_ID)
    resp = client.post(
        "/api/broadcasts",
        json={"name": "Promo", "message_text": "Hello!", "channel": "whatsapp"},
    )
    assert resp.status_code == 403


def test_owner_can_create_broadcast(env):
    client = env(OWNER_ID)
    resp = client.post(
        "/api/broadcasts",
        json={"name": "Promo", "message_text": "Hello!", "channel": "whatsapp"},
    )
    assert resp.status_code == 200, resp.text


def test_employee_cannot_list_broadcasts(env):
    client = env(EMPLOYEE_ID)
    resp = client.get("/api/broadcasts")
    assert resp.status_code == 403


def test_owner_can_list_broadcasts(env):
    client = env(OWNER_ID)
    resp = client.get("/api/broadcasts")
    assert resp.status_code == 200, resp.text


# ===========================================================================
# modules.catalogue - POST/GET /api/catalogue
# ===========================================================================

def test_employee_cannot_create_catalogue_product(env):
    client = env(EMPLOYEE_ID)
    resp = client.post("/api/catalogue", json={"name": "Widget", "price_cents": 1000})
    assert resp.status_code == 403


def test_owner_can_create_catalogue_product(env):
    client = env(OWNER_ID)
    resp = client.post("/api/catalogue", json={"name": "Widget", "price_cents": 1000})
    assert resp.status_code == 200, resp.text


def test_employee_cannot_list_catalogue(env):
    client = env(EMPLOYEE_ID)
    resp = client.get("/api/catalogue")
    assert resp.status_code == 403


def test_owner_can_list_catalogue(env):
    client = env(OWNER_ID)
    resp = client.get("/api/catalogue")
    assert resp.status_code == 200, resp.text


# ===========================================================================
# channels.manage - POST /api/scheduled-posts
# ===========================================================================

def _seed_postable_channel_account() -> int:
    from database.database import db

    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO channel_accounts (company_id, channel, name, status) "
            "VALUES (1, 'messenger', 'Test Page', 'active')"
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_employee_cannot_create_scheduled_post(env):
    account_id = _seed_postable_channel_account()
    client = env(EMPLOYEE_ID)
    resp = client.post(
        "/api/scheduled-posts",
        json={"text": "Hello world", "channel_account_ids": [account_id]},
    )
    assert resp.status_code == 403


def test_owner_can_create_scheduled_post(env):
    account_id = _seed_postable_channel_account()
    client = env(OWNER_ID)
    resp = client.post(
        "/api/scheduled-posts",
        json={"text": "Hello world", "channel_account_ids": [account_id]},
    )
    assert resp.status_code == 200, resp.text


# ===========================================================================
# instructions - knowledge.manage (create), knowledge.view (list)
# ===========================================================================

def test_employee_cannot_create_instruction(env):
    client = env(EMPLOYEE_ID)
    resp = client.post("/api/instructions", json={"text": "Always greet in Arabic first."})
    assert resp.status_code == 403


def test_owner_can_create_instruction(env):
    client = env(OWNER_ID)
    resp = client.post("/api/instructions", json={"text": "Always greet in Arabic first."})
    assert resp.status_code == 200, resp.text


def test_employee_cannot_list_instructions(env):
    client = env(EMPLOYEE_ID)
    resp = client.get("/api/instructions")
    assert resp.status_code == 403


def test_owner_can_list_instructions(env):
    client = env(OWNER_ID)
    resp = client.get("/api/instructions")
    assert resp.status_code == 200, resp.text


# ===========================================================================
# knowledge.manage - POST /api/knowledge
# ===========================================================================

def test_employee_cannot_create_knowledge_entry(env):
    client = env(EMPLOYEE_ID)
    resp = client.post(
        "/api/knowledge",
        json={"title": "Refund policy", "content": "We refund within 14 days."},
    )
    assert resp.status_code == 403


def test_owner_can_create_knowledge_entry(env):
    client = env(OWNER_ID)
    resp = client.post(
        "/api/knowledge",
        json={"title": "Refund policy", "content": "We refund within 14 days."},
    )
    assert resp.status_code == 200, resp.text


# ===========================================================================
# departments - users.manage (create/delete); GET must stay OPEN
# ===========================================================================

def test_employee_cannot_create_department(env):
    client = env(EMPLOYEE_ID)
    resp = client.post("/api/departments", json={"name": "Sales"})
    assert resp.status_code == 403


def test_owner_can_create_department(env):
    client = env(OWNER_ID)
    resp = client.post("/api/departments", json={"name": "Sales"})
    assert resp.status_code == 200, resp.text


def test_employee_cannot_delete_department(env):
    env(OWNER_ID).post("/api/departments", json={"name": "Support"})
    client = env(EMPLOYEE_ID)
    resp = client.delete("/api/departments/Support")
    assert resp.status_code == 403


def test_owner_can_delete_department(env):
    env(OWNER_ID).post("/api/departments", json={"name": "Support"})
    client = env(OWNER_ID)
    resp = client.delete("/api/departments/Support")
    assert resp.status_code == 200, resp.text


def test_employee_with_zero_permissions_can_still_list_departments(env):
    """GET /api/departments was NOT gated - confirm it stayed open."""
    client = env(EMPLOYEE_ID)
    resp = client.get("/api/departments")
    assert resp.status_code == 200, resp.text


# ===========================================================================
# team-chat - modules.team_chat
# ===========================================================================

def test_employee_cannot_send_team_chat_message(env):
    client = env(EMPLOYEE_ID)
    resp = client.post("/api/team-chat", json={"text": "hey team"})
    assert resp.status_code == 403


def test_owner_can_send_team_chat_message(env):
    client = env(OWNER_ID)
    resp = client.post("/api/team-chat", json={"text": "hey team"})
    assert resp.status_code == 200, resp.text


def test_employee_cannot_list_team_chat(env):
    client = env(EMPLOYEE_ID)
    resp = client.get("/api/team-chat")
    assert resp.status_code == 403


def test_owner_can_list_team_chat(env):
    client = env(OWNER_ID)
    resp = client.get("/api/team-chat")
    assert resp.status_code == 200, resp.text


# ===========================================================================
# conversations.reply - POST /conversations/{channel}/{user_id}/reply
# ===========================================================================

def test_employee_cannot_send_manual_reply(env):
    """Permission check happens before conversation ownership lookup,
    so no conversation setup is needed to prove the 403."""
    client = env(EMPLOYEE_ID)
    resp = client.post(
        "/conversations/telegram/999888777/reply",
        json={"text": "Hello from support"},
    )
    assert resp.status_code == 403


def test_owner_can_send_manual_reply(env):
    from backend.services.conversation_control_service import conversation_control_service

    # Manual reply requires the sender to already own/be handling the
    # conversation (handled_by_ai must be False) - same setup as
    # tests/test_telegram_manual_reply.py.
    conversation_control_service.set_ai_mode(
        company_id=COMPANY_ID, channel="telegram", external_user_id="999888777",
        handled_by_ai=False, actor_user_id=OWNER_ID,
    )
    client = env(OWNER_ID)

    fake_response = MagicMock()
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 1}}
    with patch("channels.telegram.sender.config.TELEGRAM_BOT_TOKEN", "fake-token"), \
         patch("channels.telegram.sender.requests.post", return_value=fake_response):
        resp = client.post(
            "/conversations/telegram/999888777/reply",
            json={"text": "Hello from support"},
        )
    assert resp.status_code == 200, resp.text


# ===========================================================================
# calls - conversations.reply (create), conversations.view (list),
# settings.manage (delete)
# ===========================================================================

def test_employee_cannot_create_call_log(env):
    client = env(EMPLOYEE_ID)
    resp = client.post("/api/calls", json={"direction": "outbound", "phone_number": "+15551234"})
    assert resp.status_code == 403


def test_owner_can_create_call_log(env):
    client = env(OWNER_ID)
    resp = client.post("/api/calls", json={"direction": "outbound", "phone_number": "+15551234"})
    assert resp.status_code == 200, resp.text


def test_employee_cannot_list_call_logs(env):
    client = env(EMPLOYEE_ID)
    resp = client.get("/api/calls")
    assert resp.status_code == 403


def test_owner_can_list_call_logs(env):
    client = env(OWNER_ID)
    resp = client.get("/api/calls")
    assert resp.status_code == 200, resp.text


def test_employee_cannot_delete_call_log(env):
    created = env(OWNER_ID).post(
        "/api/calls", json={"direction": "outbound", "phone_number": "+15551234"}
    ).json()
    client = env(EMPLOYEE_ID)
    resp = client.delete(f"/api/calls/{created['id']}")
    assert resp.status_code == 403


def test_owner_can_delete_call_log(env):
    created = env(OWNER_ID).post(
        "/api/calls", json={"direction": "outbound", "phone_number": "+15551234"}
    ).json()
    client = env(OWNER_ID)
    resp = client.delete(f"/api/calls/{created['id']}")
    assert resp.status_code == 200, resp.text


# ===========================================================================
# conversation-tags - settings.manage (create); GET must stay OPEN
# ===========================================================================

def test_employee_cannot_create_conversation_tag(env):
    client = env(EMPLOYEE_ID)
    resp = client.post("/api/conversation-tags", json={"name": "VIP"})
    assert resp.status_code == 403


def test_owner_can_create_conversation_tag(env):
    client = env(OWNER_ID)
    resp = client.post("/api/conversation-tags", json={"name": "VIP"})
    assert resp.status_code == 200, resp.text


def test_employee_with_zero_permissions_can_still_list_conversation_tags(env):
    client = env(EMPLOYEE_ID)
    resp = client.get("/api/conversation-tags")
    assert resp.status_code == 200, resp.text


# ===========================================================================
# customers - settings.manage on bulk-update/segment-delete;
# basic GET/POST/PUT /api/customers must stay OPEN for day-to-day CRM work
# ===========================================================================

def test_employee_cannot_bulk_update_customers(env):
    created = env(OWNER_ID).post(
        "/api/customers", json={"display_name": "Jane Doe", "phone": "+15559999"}
    ).json()
    client = env(EMPLOYEE_ID)
    resp = client.post(
        "/api/customers/bulk-update",
        json={"customer_ids": [created["id"]], "lifecycle_stage": "customer"},
    )
    assert resp.status_code == 403


def test_owner_can_bulk_update_customers(env):
    created = env(OWNER_ID).post(
        "/api/customers", json={"display_name": "Jane Doe", "phone": "+15559999"}
    ).json()
    client = env(OWNER_ID)
    resp = client.post(
        "/api/customers/bulk-update",
        json={"customer_ids": [created["id"]], "lifecycle_stage": "customer"},
    )
    assert resp.status_code == 200, resp.text


def test_employee_cannot_delete_customer_segment(env):
    created = env(OWNER_ID).post(
        "/api/customer-segments", json={"name": "VIPs", "filters": {}}
    ).json()
    client = env(EMPLOYEE_ID)
    resp = client.delete(f"/api/customer-segments/{created['id']}")
    assert resp.status_code == 403


def test_owner_can_delete_customer_segment(env):
    created = env(OWNER_ID).post(
        "/api/customer-segments", json={"name": "VIPs", "filters": {}}
    ).json()
    client = env(OWNER_ID)
    resp = client.delete(f"/api/customer-segments/{created['id']}")
    assert resp.status_code == 200, resp.text


def test_employee_with_zero_permissions_can_still_do_basic_customer_crud(env):
    """Confirms day-to-day CRM work (list/create/update contacts) was NOT
    accidentally gated behind settings.manage."""
    client = env(EMPLOYEE_ID)
    create_resp = client.post("/api/customers", json={"display_name": "Basic CRM Contact"})
    assert create_resp.status_code == 200, create_resp.text
    customer_id = create_resp.json()["id"]

    list_resp = client.get("/api/customers")
    assert list_resp.status_code == 200, list_resp.text

    update_resp = client.put(f"/api/customers/{customer_id}", json={"display_name": "Updated Name"})
    assert update_resp.status_code == 200, update_resp.text


# ===========================================================================
# tickets - just needs auth now (no permission code); company isolation
# ===========================================================================

def test_unauthenticated_request_is_rejected(env):
    """Before this fix, GET /tickets/ had no auth dependency at all and
    would have returned 200 to anyone. Build a client with NO
    get_current_user override to hit the real auth dependency."""
    from main import app
    from backend.services.auth_service import get_current_user

    env(OWNER_ID)  # ensures db/company are set up
    del app.dependency_overrides[get_current_user]
    try:
        client = TestClient(app)
        resp = client.get("/tickets/")
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides.clear()


def test_ticket_from_company_a_not_visible_to_company_b(env):
    from database.database import db
    from backend.services.auth_service import get_current_user
    from main import app

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (3, 'other_owner@test.local', 'Other Owner', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (2, 3, NULL, 'active')"
        )
        conn.commit()

    client_a = env(OWNER_ID)
    created = client_a.post(
        "/tickets/",
        json={"platform": "whatsapp", "user_id": "cust_a", "problem": "Buffering issue"},
    )
    assert created.status_code == 200, created.text
    ticket_id = created.json()["ticket_id"]

    async def _override_company_b():
        return {"id": 3, "email": "other_owner@test.local", "is_super_admin": False, "active_company_id": 2}
    app.dependency_overrides[get_current_user] = _override_company_b
    client_b = TestClient(app)

    resp = client_b.get(f"/tickets/{ticket_id}")
    assert resp.status_code == 404
