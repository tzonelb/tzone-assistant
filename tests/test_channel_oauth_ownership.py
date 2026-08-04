"""Tests for the cross-company ownership guard in the Facebook OAuth
callback (backend/api/routes/channel_oauth.py).

The bug being guarded against: the callback upserts channel_accounts via
INSERT ... ON CONFLICT(channel, external_account_id) DO UPDATE SET
company_id = excluded.company_id. Without a guard, if the SAME Facebook
Page ID (or Instagram Business Account ID) is connected by a SECOND,
different company, the row -- and therefore ALL of the original company's
conversation routing tied to that page -- would be silently reassigned to
the second company with no warning or audit trail.

These tests drive the real _run_callback_exchange() against a throwaway
SQLite DB, mocking only the outbound Facebook Graph HTTP calls.

Run with: python3 -m pytest tests/test_channel_oauth_ownership.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api.routes import channel_oauth  # noqa: E402
from backend.services.token_crypto import decrypt_token, encrypt_token  # noqa: E402


@pytest.fixture()
def fresh_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()

    # channel_accounts.company_id is a real FK to companies(id); seed a
    # workspace and the company ids these tests connect pages under.
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workspaces (id, name, slug) VALUES (1, 'ws', 'ws')"
        )
        for cid in (1, 2, 3, 7):
            conn.execute(
                "INSERT OR IGNORE INTO companies (id, workspace_id, name, slug) "
                "VALUES (?, 1, ?, ?)",
                (cid, f"company-{cid}", f"company-{cid}"),
            )
        conn.commit()

    yield db

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


class _FakeResp:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.content = b"{}"
        self.text = str(json_data)

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._json


def _install_fake_get(monkeypatch, pages, ig_id=None):
    def fake_get(url, params=None, timeout=None):
        if "oauth/access_token" in url:
            return _FakeResp({"access_token": "user-access-token"})
        if "me/accounts" in url:
            return _FakeResp({"data": pages})
        # Anything else is the per-page Instagram business account lookup.
        if ig_id:
            return _FakeResp({"instagram_business_account": {"id": ig_id}})
        return _FakeResp({})

    monkeypatch.setattr(channel_oauth.httpx, "get", fake_get)


def _seed_account(db, company_id, channel, external_account_id, token="orig"):
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO channel_accounts
                (company_id, channel, name, external_account_id,
                 access_token_encrypted, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (
                company_id,
                channel,
                f"seed-{external_account_id}",
                external_account_id,
                encrypt_token(token),
                ),
        )
        conn.commit()


def _row(db, channel, external_account_id):
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM channel_accounts WHERE channel = ? AND external_account_id = ?",
            (channel, external_account_id),
        ).fetchone()


def test_blocks_silent_takeover_by_different_company(fresh_db, monkeypatch):
    db = fresh_db
    _seed_account(db, company_id=1, channel="messenger", external_account_id="PAGE1", token="company1-token")

    _install_fake_get(
        monkeypatch,
        pages=[{"id": "PAGE1", "name": "Page One", "access_token": "company2-page-token"}],
    )

    resp = channel_oauth._run_callback_exchange(code="code", company_id=2)

    assert "already_connected_elsewhere" in resp.headers["location"]
    assert "status=error" in resp.headers["location"]

    # Ownership and token must be untouched -- no silent reassignment.
    row = _row(db, "messenger", "PAGE1")
    assert row["company_id"] == 1
    assert decrypt_token(row["access_token_encrypted"]) == "company1-token"


def test_instagram_conflict_blocks_entire_connect_atomically(fresh_db, monkeypatch):
    db = fresh_db
    # Company 1 owns an Instagram business account.
    _seed_account(db, company_id=1, channel="instagram", external_account_id="IG1")

    # Company 2 connects a brand-new page whose linked IG account collides.
    _install_fake_get(
        monkeypatch,
        pages=[{"id": "PAGE_NEW", "name": "New Page", "access_token": "pt"}],
        ig_id="IG1",
    )

    resp = channel_oauth._run_callback_exchange(code="code", company_id=2)

    assert "already_connected_elsewhere" in resp.headers["location"]
    # Atomic: the non-conflicting page must NOT have been created either.
    assert _row(db, "messenger", "PAGE_NEW") is None
    # The existing IG account still belongs to company 1.
    assert _row(db, "instagram", "IG1")["company_id"] == 1


def test_same_company_reconnect_updates_token(fresh_db, monkeypatch):
    db = fresh_db
    _seed_account(db, company_id=7, channel="messenger", external_account_id="PAGE7", token="stale")

    _install_fake_get(
        monkeypatch,
        pages=[{"id": "PAGE7", "name": "Page Seven", "access_token": "refreshed-token"}],
    )

    resp = channel_oauth._run_callback_exchange(code="code", company_id=7)

    assert "status=ok" in resp.headers["location"]
    row = _row(db, "messenger", "PAGE7")
    assert row["company_id"] == 7
    assert decrypt_token(row["access_token_encrypted"]) == "refreshed-token"


def test_new_page_connects_normally(fresh_db, monkeypatch):
    db = fresh_db

    _install_fake_get(
        monkeypatch,
        pages=[{"id": "PAGE_FRESH", "name": "Fresh", "access_token": "tok"}],
    )

    resp = channel_oauth._run_callback_exchange(code="code", company_id=3)

    assert "status=ok" in resp.headers["location"]
    row = _row(db, "messenger", "PAGE_FRESH")
    assert row is not None
    assert row["company_id"] == 3
