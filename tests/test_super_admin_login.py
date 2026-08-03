"""
Tests for the dedicated, company-free Super Admin login path
(auth_service.authenticate_super_admin + POST /api/auth/super-admin-login).
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()

    password_hash = auth_service.hash_password("CorrectHorse123!")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (email, full_name, status, is_super_admin, password_hash) "
            "VALUES ('super@test.local', 'Super Admin', 'active', 1, ?)",
            (password_hash,),
        )
        conn.execute(
            "INSERT INTO users (email, full_name, status, is_super_admin, password_hash) "
            "VALUES ('regular@test.local', 'Regular User', 'active', 0, ?)",
            (password_hash,),
        )
        conn.commit()

    from main import app
    yield TestClient(app), auth_service

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


def test_super_admin_can_log_in_with_no_company_field(client_and_db):
    _client, auth_service = client_and_db
    user = auth_service.authenticate_super_admin(email="super@test.local", password="CorrectHorse123!")
    assert user is not None
    assert user["is_super_admin"] is True
    assert user["active_company_id"] is not None


def test_regular_user_with_correct_password_is_rejected(client_and_db):
    """The whole point of this dedicated path: a correct password for a
    normal (non-super-admin) account must never grant entry here."""
    _client, auth_service = client_and_db
    user = auth_service.authenticate_super_admin(email="regular@test.local", password="CorrectHorse123!")
    assert user is None


def test_super_admin_with_wrong_password_is_rejected(client_and_db):
    _client, auth_service = client_and_db
    user = auth_service.authenticate_super_admin(email="super@test.local", password="wrong")
    assert user is None


def test_super_admin_login_route_issues_a_real_session(client_and_db):
    client, _auth_service = client_and_db
    resp = client.post(
        "/api/auth/super-admin-login",
        json={"email": "super@test.local", "password": "CorrectHorse123!"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["is_super_admin"] is True

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text


def test_super_admin_login_route_rejects_regular_user(client_and_db):
    client, _auth_service = client_and_db
    resp = client.post(
        "/api/auth/super-admin-login",
        json={"email": "regular@test.local", "password": "CorrectHorse123!"},
    )
    assert resp.status_code == 401


def test_super_admin_login_route_rejects_unknown_email(client_and_db):
    client, _auth_service = client_and_db
    resp = client.post(
        "/api/auth/super-admin-login",
        json={"email": "nobody@test.local", "password": "CorrectHorse123!"},
    )
    assert resp.status_code == 401
