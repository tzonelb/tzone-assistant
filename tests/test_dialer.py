"""Regression tests for the Dialer module: RBAC, company-scoping, the
not-configured behavior, webhook signature enforcement, and the
Twilio-signature algorithm itself.

The Dialer (backend/services/telephony_service.py + routes/dialer.py)
places real calls through a provider abstraction. In test/dev there are
no TWILIO_* credentials, so the provider is NullProvider -- these tests
prove the module is safe and correct in that state, and that the
webhook/signature and provider-selection machinery behaves:

  1. RBAC: status/history need "calls.view"; placing/transferring/
     hanging up need "dialer.use".
  2. Unconfigured: placing a call returns 503 with a clear message --
     never a 500, never a half-created call row.
  3. Webhooks reject unsigned/badly-signed requests outright (403) --
     with no auth token configured NOTHING can be verified, so
     everything is rejected.
  4. verify_twilio_signature implements the documented scheme correctly
     (positive + negative vectors).
  5. Call listing is company-scoped.

Run with: python3 -m pytest tests/test_dialer_company_scoping.py -v
"""
import base64
import hashlib
import hmac
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

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    company_settings_service.ensure_schema()
    from backend.services.telephony_service import telephony_service
    telephony_service.ensure_schema()
    from backend.services.call_log_service import call_log_service
    call_log_service.ensure_schema()

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
        plan = conn.execute("SELECT id FROM plans LIMIT 1").fetchone()
        if plan:
            conn.execute(
                """
                INSERT INTO subscriptions (
                    company_id, plan_id, status, starts_at,
                    expires_at, grace_period_until, auto_renew
                ) VALUES (?, ?, 'active', '2026-01-01T00:00:00+00:00',
                          '2030-01-01T00:00:00+00:00',
                          '2030-02-01T00:00:00+00:00', 0)
                """,
                (company_id, plan["id"]),
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


def test_unauthenticated_requests_are_rejected(fresh_env):
    db, _auth = fresh_env
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/dialer/status").status_code == 401
        assert client.get("/api/dialer/calls").status_code == 401
        assert (
            client.post(
                "/api/dialer/calls", json={"to_number": "+96170000000"}
            ).status_code
            == 401
        )


def test_rbac_view_vs_dial(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _make_role(db, company, "viewer", "Viewer", ["calls.view"])
    _, viewer_token = _make_user(db, auth_service, company, "viewer@test.local", role_code="viewer")

    with TestClient(app) as client:
        # Viewer can see status and history...
        assert (
            client.get("/api/dialer/status", headers=_headers(viewer_token)).status_code
            == 200
        )
        assert (
            client.get("/api/dialer/calls", headers=_headers(viewer_token)).status_code
            == 200
        )
        # ...but cannot place calls without dialer.use.
        assert (
            client.post(
                "/api/dialer/calls",
                headers=_headers(viewer_token),
                json={"to_number": "+96170000000"},
            ).status_code
            == 403
        )


def test_unconfigured_placing_call_returns_503(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner@test.local")

    with TestClient(app) as client:
        status = client.get("/api/dialer/status", headers=_headers(token)).json()
        assert status["configured"] is False
        assert "TWILIO_ACCOUNT_SID" in status["missing"]

        placed = client.post(
            "/api/dialer/calls",
            headers=_headers(token),
            json={"to_number": "+96170000000"},
        )
        assert placed.status_code == 503
        assert "not configured" in placed.json()["detail"].lower()

    # No half-created call row.
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM telephony_calls"
        ).fetchone()["c"]
    assert count == 0


def test_webhooks_reject_unsigned_requests(fresh_env):
    db, _auth = fresh_env
    from main import app

    with TestClient(app) as client:
        for path in (
            "/api/dialer/webhooks/voice",
            "/api/dialer/webhooks/inbound",
            "/api/dialer/webhooks/status",
            "/api/dialer/webhooks/recording",
        ):
            unsigned = client.post(path, data={"CallSid": "CA123"})
            assert unsigned.status_code == 403, path

            badly_signed = client.post(
                path,
                data={"CallSid": "CA123"},
                headers={"X-Twilio-Signature": "bogus"},
            )
            assert badly_signed.status_code == 403, path


def test_twilio_signature_algorithm():
    from backend.services.telephony_service import verify_twilio_signature

    auth_token = "test_auth_token_123"
    url = "https://example.com/api/dialer/webhooks/status"
    params = {"CallSid": "CA999", "CallStatus": "completed"}

    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    good_signature = base64.b64encode(
        hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()

    assert verify_twilio_signature(
        url=url, params=params, signature=good_signature, auth_token=auth_token
    )
    assert not verify_twilio_signature(
        url=url, params=params, signature="wrong", auth_token=auth_token
    )
    assert not verify_twilio_signature(
        url=url, params=params, signature=good_signature, auth_token="other_token"
    )
    assert not verify_twilio_signature(
        url=url, params=params, signature=None, auth_token=auth_token
    )
    # Empty auth token (unconfigured) verifies nothing.
    assert not verify_twilio_signature(
        url=url, params=params, signature=good_signature, auth_token=""
    )


def test_call_listing_is_company_scoped(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_b = _make_user(db, auth_service, company_b, "ownerb@test.local")

    # Seed a company-A telephony call directly (as a completed provider
    # call would have).
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO telephony_calls (
                company_id, provider, provider_call_id, direction,
                to_number, status
            ) VALUES (?, 'twilio', 'CA_A_1', 'outbound', '+96170000001', 'completed')
            """,
            (company_a,),
        )
        conn.commit()

    with TestClient(app) as client:
        listed_b = client.get("/api/dialer/calls", headers=_headers(token_b))
        assert listed_b.status_code == 200
        body = listed_b.json()
        assert body["total"] == 0
        assert body["items"] == []


def test_status_callback_updates_call_and_mirrors_to_log(fresh_env):
    """The provider-status pipeline: a completed status callback moves
    the telephony call to its final state and mirrors it into
    call_logs (the Calls page's history)."""
    db, _auth = fresh_env
    from backend.services.telephony_service import telephony_service

    company = _make_company(db, "Company A", "company-a")

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO telephony_calls (
                company_id, provider, provider_call_id, direction,
                to_number, status, started_at
            ) VALUES (?, 'twilio', 'CA_TEST_7', 'outbound', '+96170000002',
                      'in_progress', '2026-08-04T10:00:00+00:00')
            """,
            (company,),
        )
        conn.commit()

    telephony_service.handle_status_callback(
        {"CallSid": "CA_TEST_7", "CallStatus": "completed", "CallDuration": "95"}
    )

    with db.connect() as conn:
        call = conn.execute(
            "SELECT * FROM telephony_calls WHERE provider_call_id = 'CA_TEST_7'"
        ).fetchone()
        log = conn.execute(
            "SELECT * FROM call_logs WHERE company_id = ?", (company,)
        ).fetchone()

    assert call["status"] == "completed"
    assert call["duration_seconds"] == 95
    assert call["ended_at"]

    assert log is not None
    assert log["phone_number"] == "+96170000002"
    assert log["direction"] == "outbound"
    assert log["status"] == "completed"
    assert log["duration_seconds"] == 95
