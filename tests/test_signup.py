"""
Real tests for public self-service Sign-up — a visitor registers their
company + owner account + plan in one flow and lands logged-in.

Run with: .venv/Scripts/python.exe -m pytest tests/test_signup.py -q
"""
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.platform_admin_service import platform_admin_service
    from backend.services.signup_service import signup_service
    from backend.services.license_key_service import license_key_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    platform_admin_service.ensure_schema()
    signup_service.ensure_schema()
    license_key_service.ensure_schema()

    from main import app

    yield TestClient(app)

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


def _get_email_code(client, email):
    """Requests a code via the real send-code endpoint, mocking only the
    actual SMTP send — everything else (generation, hashing, storage) is
    the real code path. Returns the plaintext code the "email" carried."""
    with patch("backend.services.signup_service.send_email") as mock_send:
        mock_send.return_value = (True, "")
        resp = client.post("/api/signup/send-code", json={"email": email})
        assert resp.status_code == 200, resp.text
        body = mock_send.call_args.kwargs.get("body")
        match = re.search(r"code is: (\d{6})", body)
        assert match, f"Could not find code in email body: {body}"
        return match.group(1)


def _signup_payload(client, **overrides):
    email = overrides.get("owner_email", "ada@acme.test")
    payload = {
        "company_name": "Acme Widgets",
        "owner_full_name": "Ada Owner",
        "owner_email": email,
        "password": "supersecret",
        "confirm_password": "supersecret",
        "email_code": _get_email_code(client, email),
    }
    payload.update(overrides)
    return payload


def test_plans_list_is_public(client):
    resp = client.get("/api/signup/plans")
    assert resp.status_code == 200, resp.text
    plans = resp.json()["plans"]
    # Seeded plans exist and expose the display fields (no auth needed).
    assert len(plans) >= 1
    assert {"id", "name", "price_monthly", "max_users"} <= set(plans[0].keys())


def test_send_code_rejects_already_registered_email(client):
    client.post("/api/signup", json=_signup_payload(client))
    resp = client.post("/api/signup/send-code", json={"email": "ada@acme.test"})
    assert resp.status_code == 400


def test_signup_requires_correct_email_code(client):
    payload = _signup_payload(client, email_code="000000")
    resp = client.post("/api/signup", json=payload)
    assert resp.status_code == 400
    assert "code" in resp.json()["detail"].lower()


def test_signup_requires_matching_passwords(client):
    payload = _signup_payload(client, confirm_password="different12345")
    resp = client.post("/api/signup", json=payload)
    assert resp.status_code == 400
    assert "match" in resp.json()["detail"].lower()


def test_signup_creates_company_owner_role_user_membership_and_subscription(client):
    from database.database import db

    resp = client.post("/api/signup", json=_signup_payload(client))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["access_token"]
    assert data["user"]["active_company_slug"] == "acme-widgets"
    company_id = data["user"]["active_company_id"]

    with db.connect() as conn:
        company = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        assert company is not None

        owner_role = conn.execute(
            "SELECT * FROM roles WHERE company_id = ? AND code = 'owner'", (company_id,)
        ).fetchone()
        assert owner_role is not None

        user = conn.execute("SELECT * FROM users WHERE LOWER(email) = 'ada@acme.test'").fetchone()
        assert user is not None

        membership = conn.execute(
            "SELECT * FROM company_users WHERE company_id = ? AND user_id = ?",
            (company_id, user["id"]),
        ).fetchone()
        assert membership is not None
        assert membership["status"] == "active"
        assert membership["role_id"] == owner_role["id"]

        subscription = conn.execute(
            "SELECT * FROM subscriptions WHERE company_id = ?", (company_id,)
        ).fetchone()
        assert subscription is not None
        assert subscription["status"] == "trialing"


def test_returned_token_authenticates_me(client):
    resp = client.post("/api/signup", json=_signup_payload(client))
    token = resp.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user"]["email"] == "ada@acme.test"
    # The owner is an active member of exactly the new company.
    assert any(c["role_code"] == "owner" for c in body["companies"])


def test_duplicate_email_returns_400(client):
    first = client.post("/api/signup", json=_signup_payload(client))
    assert first.status_code == 200

    # The duplicate-email check runs before code verification (so a doomed
    # request never burns a real code) — a dummy code is enough to prove it.
    second_payload = _signup_payload(client, company_name="Different Co", owner_email="second-attempt@acme.test")
    second_payload["owner_email"] = "ada@acme.test"
    second_payload["email_code"] = "000000"
    second = client.post("/api/signup", json=second_payload)
    assert second.status_code == 400
    assert "email" in second.json()["detail"].lower()


def test_short_password_returns_422_or_400(client):
    resp = client.post("/api/signup", json=_signup_payload(client, password="short", confirm_password="short"))
    # Pydantic min_length rejects with 422; if it ever reaches the service
    # it raises ValueError -> 400. Either is an accepted rejection.
    assert resp.status_code in (400, 422)


def test_slug_collision_auto_resolves(client):
    first = client.post("/api/signup", json=_signup_payload(client))
    assert first.json()["user"]["active_company_slug"] == "acme-widgets"

    second = client.post(
        "/api/signup",
        json=_signup_payload(client, owner_email="second@acme.test"),
    )
    assert second.status_code == 200, second.text
    assert second.json()["user"]["active_company_slug"] == "acme-widgets-2"


def test_explicit_plan_selection_is_honored(client):
    plans = client.get("/api/signup/plans").json()["plans"]
    chosen = plans[-1]
    resp = client.post("/api/signup", json=_signup_payload(client, plan_id=chosen["id"]))
    assert resp.status_code == 200, resp.text

    from database.database import db
    company_id = resp.json()["user"]["active_company_id"]
    with db.connect() as conn:
        sub = conn.execute(
            "SELECT plan_id FROM subscriptions WHERE company_id = ?", (company_id,)
        ).fetchone()
    assert sub["plan_id"] == chosen["id"]


def test_license_key_grants_its_plan_and_gets_redeemed(client):
    from backend.services.license_key_service import license_key_service

    plans = client.get("/api/signup/plans").json()["plans"]
    target_plan = plans[-1]
    key = license_key_service.issue(plan_id=target_plan["id"], note="test key")

    resp = client.post("/api/signup", json=_signup_payload(client, license_key=key["code"]))
    assert resp.status_code == 200, resp.text

    from database.database import db
    company_id = resp.json()["user"]["active_company_id"]
    with db.connect() as conn:
        sub = conn.execute(
            "SELECT plan_id FROM subscriptions WHERE company_id = ?", (company_id,)
        ).fetchone()
    assert sub["plan_id"] == target_plan["id"]

    redeemed = license_key_service.get(key["code"])
    assert redeemed["status"] == "redeemed"
    assert redeemed["redeemed_by_company_id"] == company_id


def test_already_redeemed_license_key_is_rejected(client):
    from backend.services.license_key_service import license_key_service

    plans = client.get("/api/signup/plans").json()["plans"]
    key = license_key_service.issue(plan_id=plans[0]["id"])
    license_key_service.redeem(code=key["code"], company_id=999)

    resp = client.post("/api/signup", json=_signup_payload(client, license_key=key["code"]))
    assert resp.status_code == 400
