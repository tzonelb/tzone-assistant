import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from backend.services import totp_utils


@pytest.fixture()
def client_and_user():
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
            "VALUES ('twofa@test.local', '2FA Test', 'active', 0, ?)",
            (password_hash,),
        )
        user_id = conn.execute("SELECT id FROM users WHERE email = 'twofa@test.local'").fetchone()["id"]
        conn.execute("INSERT INTO company_users (company_id, user_id, status) VALUES (1, ?, 'active')", (user_id,))
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": user_id, "email": "twofa@test.local", "is_super_admin": False, "active_company_id": 1}
    app.dependency_overrides[get_current_user] = _override

    yield TestClient(app), user_id, auth_service

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


def test_totp_verify_accepts_fresh_code_rejects_wrong_code():
    secret = totp_utils.generate_secret()
    code = totp_utils.generate(secret)
    assert totp_utils.verify(secret, code) is True
    wrong = "000000" if code != "000000" else "111111"
    assert totp_utils.verify(secret, wrong) is False


def test_login_without_2fa_returns_token_directly(client_and_user):
    client, _user_id, _auth = client_and_user
    resp = client.post("/api/auth/login", json={"company": "tzone-lb", "email": "twofa@test.local", "password": "CorrectHorse123!"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("access_token")
    assert not body.get("twofa_required")


def test_enroll_confirm_flips_enabled_and_changes_login_flow(client_and_user):
    client, user_id, auth_service = client_and_user

    status_resp = client.get("/api/auth/2fa/status")
    assert status_resp.json()["enabled"] is False

    start = client.post("/api/auth/2fa/enroll/start")
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]
    assert start.json()["otpauth_uri"].startswith("otpauth://totp/")

    bad_confirm = client.post("/api/auth/2fa/enroll/confirm", json={"code": "000000"})
    assert bad_confirm.status_code == 400

    code = totp_utils.generate(secret)
    confirm = client.post("/api/auth/2fa/enroll/confirm", json={"code": code})
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["enabled"] is True

    assert client.get("/api/auth/2fa/status").json()["enabled"] is True
    assert auth_service.user_has_2fa(user_id) is True

    # Login now stops short of a token and returns a pending_token instead.
    login_resp = client.post("/api/auth/login", json={"company": "tzone-lb", "email": "twofa@test.local", "password": "CorrectHorse123!"})
    assert login_resp.status_code == 200
    login_body = login_resp.json()
    assert login_body["twofa_required"] is True
    assert not login_body.get("access_token")
    assert login_body.get("pending_token")


def test_2fa_verify_completes_login_with_valid_code(client_and_user):
    client, user_id, auth_service = client_and_user
    start = client.post("/api/auth/2fa/enroll/start")
    secret = start.json()["secret"]
    client.post("/api/auth/2fa/enroll/confirm", json={"code": totp_utils.generate(secret)})

    login_resp = client.post("/api/auth/login", json={"company": "tzone-lb", "email": "twofa@test.local", "password": "CorrectHorse123!"})
    pending_token = login_resp.json()["pending_token"]

    bad = client.post("/api/auth/2fa/verify", json={"pending_token": pending_token, "code": "000000"})
    assert bad.status_code == 401

    good_code = totp_utils.generate(secret)
    verify_resp = client.post("/api/auth/2fa/verify", json={"pending_token": pending_token, "code": good_code})
    assert verify_resp.status_code == 200, verify_resp.text
    token = verify_resp.json()["access_token"]
    assert token

    # The issued token actually authenticates a follow-up call.
    me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["user"]["email"] == "twofa@test.local"


def test_2fa_verify_rejects_bad_pending_token(client_and_user):
    client, _user_id, _auth = client_and_user
    resp = client.post("/api/auth/2fa/verify", json={"pending_token": "not-a-real-token", "code": "123456"})
    assert resp.status_code == 401


def test_disable_requires_correct_password_and_code(client_and_user):
    client, user_id, auth_service = client_and_user
    start = client.post("/api/auth/2fa/enroll/start")
    secret = start.json()["secret"]
    client.post("/api/auth/2fa/enroll/confirm", json={"code": totp_utils.generate(secret)})
    assert auth_service.user_has_2fa(user_id) is True

    wrong_password = client.post("/api/auth/2fa/disable", json={"password": "WrongPassword1!", "code": totp_utils.generate(secret)})
    assert wrong_password.status_code == 400
    assert auth_service.user_has_2fa(user_id) is True

    wrong_code = client.post("/api/auth/2fa/disable", json={"password": "CorrectHorse123!", "code": "000000"})
    assert wrong_code.status_code == 400
    assert auth_service.user_has_2fa(user_id) is True

    ok = client.post("/api/auth/2fa/disable", json={"password": "CorrectHorse123!", "code": totp_utils.generate(secret)})
    assert ok.status_code == 200, ok.text
    assert auth_service.user_has_2fa(user_id) is False

    # Login is back to the direct (no-2FA) path.
    login_resp = client.post("/api/auth/login", json={"company": "tzone-lb", "email": "twofa@test.local", "password": "CorrectHorse123!"})
    assert login_resp.json().get("access_token")
