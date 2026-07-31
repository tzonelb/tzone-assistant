"""
Real tests for public self-service Sign-up — a visitor registers their
company + owner account + plan in one flow and lands logged-in.

Run with: .venv/Scripts/python.exe -m pytest tests/test_signup.py -q
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
def client():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.platform_admin_service import platform_admin_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    platform_admin_service.ensure_schema()

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


def _signup_payload(**overrides):
    payload = {
        "company_name": "Acme Widgets",
        "owner_full_name": "Ada Owner",
        "owner_email": "ada@acme.test",
        "password": "supersecret",
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


def test_signup_creates_company_owner_role_user_membership_and_subscription(client):
    from database.database import db

    resp = client.post("/api/signup", json=_signup_payload())
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
    resp = client.post("/api/signup", json=_signup_payload())
    token = resp.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user"]["email"] == "ada@acme.test"
    # The owner is an active member of exactly the new company.
    assert any(c["role_code"] == "owner" for c in body["companies"])


def test_duplicate_email_returns_400(client):
    first = client.post("/api/signup", json=_signup_payload())
    assert first.status_code == 200

    second = client.post(
        "/api/signup",
        json=_signup_payload(company_name="Different Co"),
    )
    assert second.status_code == 400
    assert "email" in second.json()["detail"].lower()


def test_short_password_returns_422_or_400(client):
    resp = client.post("/api/signup", json=_signup_payload(password="short"))
    # Pydantic min_length rejects with 422; if it ever reaches the service
    # it raises ValueError -> 400. Either is an accepted rejection.
    assert resp.status_code in (400, 422)


def test_slug_collision_auto_resolves(client):
    first = client.post("/api/signup", json=_signup_payload())
    assert first.json()["user"]["active_company_slug"] == "acme-widgets"

    second = client.post(
        "/api/signup",
        json=_signup_payload(owner_email="second@acme.test"),
    )
    assert second.status_code == 200, second.text
    assert second.json()["user"]["active_company_slug"] == "acme-widgets-2"


def test_explicit_plan_selection_is_honored(client):
    plans = client.get("/api/signup/plans").json()["plans"]
    chosen = plans[-1]
    resp = client.post("/api/signup", json=_signup_payload(plan_id=chosen["id"]))
    assert resp.status_code == 200, resp.text

    from database.database import db
    company_id = resp.json()["user"]["active_company_id"]
    with db.connect() as conn:
        sub = conn.execute(
            "SELECT plan_id FROM subscriptions WHERE company_id = ?", (company_id,)
        ).fetchone()
    assert sub["plan_id"] == chosen["id"]
