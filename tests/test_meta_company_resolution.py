"""Real tests for multi-tenant company resolution on inbound Meta webhooks.

Before this change, channels/meta/processor.py always attributed every
incoming Messenger/Instagram message to
conversation_control_service.resolve_default_company_id() — a single
hardcoded company — regardless of which Facebook Page or Instagram
account actually received the message. This meant the OAuth
connect-your-own-page flow (which populates channel_accounts with
company_id, channel, page_id, instagram_business_id, ...) had no effect
on where inbound messages ended up for any company other than the
default one.

This file proves two things about the fix in
backend/services/channel_account_service.py + channels/meta/processor.py:

  (a) regression guard — when no channel_accounts row matches the
      incoming page/IG id, company resolution still falls back to
      conversation_control_service.resolve_default_company_id() exactly
      as before.
  (b) when a channel_accounts row DOES match the incoming recipient_id,
      process_meta_payload resolves to that row's company_id instead of
      the default.

Run with: python3 -m pytest tests/test_meta_company_resolution.py -v
"""
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test.

    Mirrors tests/test_conversation_ownership.py's fresh_db fixture: table
    creation order matters here (database.database.db.create_tables()
    must run before backend.services.conversation_control_service's own
    schema-init, or channel_accounts ends up with a stale legacy shape),
    so every service import + schema call happens only after db.db_path
    is pointed at the temp file.
    """
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service
    from backend.services.customer_service import customer_service
    from backend.services.notification_service import notification_service
    from backend.services.diagnostics_service import diagnostics_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()
    # customer_service/notification_service are process-wide singletons
    # (instantiated once, on first import, elsewhere in the suite) whose
    # __init__ already ran ensure_schema() against whatever db.db_path was
    # at that time — not necessarily this test's temp file. Re-run their
    # schema setup explicitly now that db.db_path points at the temp file.
    customer_service.ensure_schema()
    notification_service.ensure_schema()
    diagnostics_service.ensure_schema()

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


def _insert_extra_company(db, name: str) -> int:
    """Insert one more active company into the platform's seeded workspace.

    db.create_tables() always seeds workspace id=1 ("T-ZONE Workspace") and
    company id=1 ("T-ZONE", status active) via _seed_platform_defaults —
    that seeded company is exactly what
    conversation_control_service.resolve_default_company_id() resolves to
    on a fresh database (lowest-id active company). Tests that need a
    *second*, non-default company use this helper rather than creating
    their own workspace, so the seeded default company (id=1) stays the
    single-tenant fallback throughout.
    """
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO companies (workspace_id, name, slug, status)
            VALUES (1, ?, ?, 'active')
            """,
            (name, name.lower().replace(" ", "-")),
        )
        company_id = cursor.lastrowid
        conn.commit()

    return company_id


def _disable_ai(company_id: int) -> None:
    """Keep schedule_smart_reply from starting a background Timer thread.

    Company resolution happens synchronously before schedule_smart_reply
    is ever called, so this has no bearing on what's under test — it just
    keeps the test from leaving a live timer thread pointed at a db file
    the fixture is about to delete.
    """
    from backend.services.company_settings_service import company_settings_service

    company_settings_service.update_section(
        company_id=company_id,
        section="ai_behavior",
        values={"enabled": False},
        actor_user_id=None,
    )


def _messenger_payload(sender_id: str, recipient_id: str, text: str = "hello") -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": sender_id},
                        "recipient": {"id": recipient_id},
                        "message": {"text": text},
                    }
                ]
            }
        ],
    }


def _conversation_company_id(db, channel: str, external_user_id: str):
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT company_id FROM conversations
            WHERE channel = ? AND external_user_id = ?
            """,
            (channel, external_user_id),
        ).fetchone()

    assert row is not None, "expected a conversation row to have been created"
    return int(row["company_id"])


def test_no_matching_channel_account_falls_back_to_default_company(fresh_db):
    """Regression guard: unmatched page id -> unchanged single-tenant behavior."""
    db = fresh_db

    from backend.services.conversation_control_service import conversation_control_service

    # db.create_tables() seeds company id=1 (T-ZONE, status active) — this
    # is exactly the single-tenant default resolve_default_company_id()
    # already picks today. No channel_accounts row is inserted in this
    # test, so resolution must fall back to it unchanged.
    expected_default = conversation_control_service.resolve_default_company_id()
    assert expected_default == 1
    _disable_ai(expected_default)

    from channels.meta.processor import process_meta_payload

    payload = _messenger_payload(
        sender_id="regression_customer_1",
        recipient_id="no_channel_account_configured_for_this_page",
    )

    result = process_meta_payload(payload)
    assert result["status"] in ("received_ai_queued", "received_ai_disabled")

    resolved = _conversation_company_id(db, "messenger", "regression_customer_1")
    assert resolved == expected_default


def test_matching_channel_account_resolves_owning_company(fresh_db):
    """When a channel_accounts row matches recipient_id, use its company_id."""
    db = fresh_db

    default_company_id = 1  # seeded by db.create_tables()
    other_company_id = _insert_extra_company(db, "Other Company")
    _disable_ai(default_company_id)
    _disable_ai(other_company_id)

    page_id = "555666777888"

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO channel_accounts (company_id, channel, name, page_id, status)
            VALUES (?, 'messenger', 'Other Company Page', ?, 'active')
            """,
            (other_company_id, page_id),
        )
        conn.commit()

    from backend.services.conversation_control_service import conversation_control_service
    from channels.meta.processor import process_meta_payload

    # Sanity: the connected page's company must differ from the default,
    # or this test wouldn't actually prove anything.
    assert other_company_id != conversation_control_service.resolve_default_company_id()

    payload = _messenger_payload(
        sender_id="owning_company_customer_1",
        recipient_id=page_id,
    )

    result = process_meta_payload(payload)
    assert result["status"] in ("received_ai_queued", "received_ai_disabled")

    resolved = _conversation_company_id(db, "messenger", "owning_company_customer_1")
    assert resolved == other_company_id
    assert resolved != default_company_id


def test_channel_account_service_returns_none_when_unmatched():
    from backend.services.channel_account_service import channel_account_service

    assert channel_account_service.get_company_id_for_account(
        channel="messenger", external_account_id=None
    ) is None
    assert channel_account_service.get_company_id_for_account(
        channel="unknown_channel", external_account_id="123"
    ) is None
