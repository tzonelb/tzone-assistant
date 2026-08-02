"""
Real tests for the Theme Studio backend (platform_ui_service +
/api/platform-ui routes) — CLAUDE_CODE_THEME_SPEC.md.

Run with: python3 -m pytest tests/test_platform_ui.py -v
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
OTHER_COMPANY_ID = 2


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.platform_ui_service import platform_ui_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    platform_ui_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'super@test.local', 'Super Admin', 'active', 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (2, 'owner@test.local', 'Owner', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Owner', 'owner', 'Full access', 1)"
        )
        owner_role_id = conn.execute("SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'").fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, 2, ?, 'active')",
            (owner_role_id,),
        )
        conn.commit()

    from main import app

    yield TestClient(app), app

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


def _as(app, user):
    from backend.services.auth_service import get_current_user

    async def _override():
        return user
    app.dependency_overrides[get_current_user] = _override


SUPER_ADMIN = {"id": 1, "email": "super@test.local", "is_super_admin": True, "active_company_id": None}
OWNER = {"id": 2, "email": "owner@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}


def test_resolve_config_returns_bundled_defaults_with_no_published_theme(client_and_db):
    client, app = client_and_db
    _as(app, OWNER)
    resp = client.get("/api/platform-ui/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 0
    # Must match T-ZONE's actual current look, not the spec doc's sample
    # values — resolving with nothing published is a visual no-op.
    assert body["tokens"]["color"]["accent"] == "#4F63F0"
    assert body["modules"]["conversations"]["visible"] is True
    assert body["brand"]["name"] == "T-ZONE"


def test_create_draft_rejects_bad_font_and_out_of_range_values(client_and_db):
    client, app = client_and_db
    _as(app, SUPER_ADMIN)

    bad_font = client.post("/api/platform-ui/themes", json={
        "scope_type": "platform", "tokens": {"type": {"headingFont": "Comic Sans"}},
    })
    assert bad_font.status_code == 422

    bad_range = client.post("/api/platform-ui/themes", json={
        "scope_type": "platform", "tokens": {"shape": {"radius": 999}},
    })
    assert bad_range.status_code == 422

    unknown_key = client.post("/api/platform-ui/themes", json={
        "scope_type": "platform", "tokens": {"color": {"nonexistent": "x"}},
    })
    assert unknown_key.status_code == 422


def test_publish_bumps_version_and_archives_previous(client_and_db):
    client, app = client_and_db
    _as(app, SUPER_ADMIN)

    draft = client.post("/api/platform-ui/themes", json={
        "scope_type": "platform", "tokens": {"color": {"accent": "#ff0000"}},
    }).json()
    published = client.post(f"/api/platform-ui/themes/{draft['id']}/publish", json={"reason": "first ship"})
    assert published.status_code == 200, published.text
    assert published.json()["version"] == 1

    config = client.get("/api/platform-ui/config").json()
    assert config["tokens"]["color"]["accent"] == "#ff0000"
    assert config["version"] == 1

    draft2 = client.post("/api/platform-ui/themes", json={
        "scope_type": "platform", "tokens": {"color": {"accent": "#00ff00"}},
    }).json()
    published2 = client.post(f"/api/platform-ui/themes/{draft2['id']}/publish", json={"reason": "tweak accent"}).json()
    assert published2["version"] == 2

    themes = client.get("/api/platform-ui/themes?scope_type=platform").json()["themes"]
    statuses = {t["id"]: t["status"] for t in themes}
    assert statuses[draft["id"]] == "archived"
    assert statuses[draft2["id"]] == "published"


def test_publish_writes_an_audit_log_row(client_and_db):
    client, app = client_and_db
    _as(app, SUPER_ADMIN)
    from database.database import db

    draft = client.post("/api/platform-ui/themes", json={"scope_type": "platform", "tokens": {}}).json()
    client.post(f"/api/platform-ui/themes/{draft['id']}/publish", json={"reason": "audit check"})

    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE action = 'ui_theme_published' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["user_id"] == 1
    assert "audit check" in row["new_values_json"]


def test_restore_clones_archived_version_into_new_draft(client_and_db):
    client, app = client_and_db
    _as(app, SUPER_ADMIN)

    draft = client.post("/api/platform-ui/themes", json={
        "scope_type": "platform", "tokens": {"color": {"accent": "#123456"}},
    }).json()
    client.post(f"/api/platform-ui/themes/{draft['id']}/publish", json={"reason": "v1"})

    draft2 = client.post("/api/platform-ui/themes", json={"scope_type": "platform", "tokens": {"color": {"accent": "#abcdef"}}}).json()
    client.post(f"/api/platform-ui/themes/{draft2['id']}/publish", json={"reason": "v2"})

    restored = client.post(f"/api/platform-ui/themes/{draft['id']}/restore")
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["status"] == "draft"
    assert body["tokens"]["color"]["accent"] == "#123456"


def test_owner_can_manage_own_company_scope_but_not_platform_or_other_company(client_and_db):
    client, app = client_and_db
    _as(app, OWNER)

    own_scope = client.post("/api/platform-ui/themes", json={
        "scope_type": "company", "scope_id": str(COMPANY_ID), "tokens": {},
    })
    assert own_scope.status_code == 200, own_scope.text

    other_scope = client.post("/api/platform-ui/themes", json={
        "scope_type": "company", "scope_id": str(OTHER_COMPANY_ID), "tokens": {},
    })
    assert other_scope.status_code == 403

    platform_scope = client.post("/api/platform-ui/themes", json={"scope_type": "platform", "tokens": {}})
    assert platform_scope.status_code == 403


def test_company_override_wins_over_platform_default_per_section_key(client_and_db):
    client, app = client_and_db
    _as(app, SUPER_ADMIN)
    platform_draft = client.post("/api/platform-ui/themes", json={
        "scope_type": "platform", "tokens": {"color": {"accent": "#111111"}, "type": {"baseSize": 16}},
    }).json()
    client.post(f"/api/platform-ui/themes/{platform_draft['id']}/publish", json={"reason": "platform base"})

    _as(app, OWNER)
    company_draft = client.post("/api/platform-ui/themes", json={
        "scope_type": "company", "scope_id": str(COMPANY_ID), "tokens": {"color": {"accent": "#222222"}},
    }).json()
    client.post(f"/api/platform-ui/themes/{company_draft['id']}/publish", json={"reason": "tenant accent"})

    config = client.get("/api/platform-ui/config").json()
    assert config["tokens"]["color"]["accent"] == "#222222"
    # Untouched key from the platform layer still applies underneath.
    assert config["tokens"]["type"]["baseSize"] == 16


def test_only_a_draft_can_be_published_or_edited(client_and_db):
    client, app = client_and_db
    _as(app, SUPER_ADMIN)
    draft = client.post("/api/platform-ui/themes", json={"scope_type": "platform", "tokens": {}}).json()
    client.post(f"/api/platform-ui/themes/{draft['id']}/publish", json={"reason": "ship"})

    republish = client.post(f"/api/platform-ui/themes/{draft['id']}/publish", json={"reason": "again"})
    assert republish.status_code == 422

    edit_published = client.patch(f"/api/platform-ui/themes/{draft['id']}", json={"tokens": {"color": {"accent": "#000000"}}})
    assert edit_published.status_code == 422
