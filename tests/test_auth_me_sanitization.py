"""
Regression test: GET /api/auth/me must never leak password_hash or
totp_secret to the browser. Every other user-serializing endpoint routes
through auth_service.sanitize_user(); this endpoint (called on nearly every
authenticated page load) previously used a raw `dict(current_user)` copy
that skipped it.

Run with: python -m pytest tests/test_auth_me_sanitization.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()

    # totp_secret is populated (as it would be for a user who once set up 2FA)
    # but totp_enabled=0, so login doesn't need a 2FA step here — the point of
    # this test is that /me must never echo the secret back, login flow aside.
    password_hash = auth_service.hash_password("CorrectHorse123!")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (email, full_name, status, is_super_admin, password_hash, totp_secret, totp_enabled) "
            "VALUES ('metest@test.local', 'Me Test', 'active', 0, ?, 'SUPERSECRETTOTPSEED', 0)",
            (password_hash,),
        )
        user_id = conn.execute("SELECT id FROM users WHERE email = 'metest@test.local'").fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'tzone-lb', 1)")
        conn.execute("INSERT INTO company_users (company_id, user_id, status) VALUES (1, ?, 'active')", (user_id,))
        conn.commit()

    from main import app
    from fastapi.testclient import TestClient

    yield TestClient(app), user_id

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


def test_me_endpoint_never_leaks_password_hash_or_totp_secret(client_and_db):
    client, user_id = client_and_db

    login = client.post("/api/auth/login", json={
        "email": "metest@test.local", "password": "CorrectHorse123!", "company": "tzone-lb",
    })
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    user = body["user"]

    leaked = {k: v for k, v in user.items() if k in ("password_hash", "totp_secret", "token_hash")}
    assert leaked == {}, f"/api/auth/me leaked sensitive fields to the browser: {leaked!r}"
