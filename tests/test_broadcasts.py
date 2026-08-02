"""
Real tests for the Broadcast feature: sending one message to every
contact matching a filter (a saved Segment, or a lifecycle_stage/tag),
on one channel, reusing the exact same per-channel send functions used
for manual employee replies.

Every sender function is mocked at the point of use inside
backend.services.broadcast_service — no real network calls are made.

Run with: python3 -m pytest tests/test_broadcasts.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.customer_service import customer_service
    from backend.services.broadcast_service import broadcast_service
    from backend.services.message_status_service import message_status_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    customer_service.ensure_schema()
    broadcast_service.ensure_schema()
    message_status_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Owner', 'owner', 'Full access', 1)"
        )
        owner_role_id = conn.execute("SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'").fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, 1, ?, 'active')",
            (owner_role_id,),
        )
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

    yield TestClient(app)

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


def _make_contact(*, company_id=COMPANY_ID, channel="telegram", external_user_id="u1", display_name="Rami"):
    from backend.services.customer_service import customer_service
    return customer_service.upsert_from_channel(
        company_id=company_id, channel=channel, external_user_id=external_user_id, display_name=display_name,
    )


def test_create_computes_recipient_count_for_channel_and_lifecycle_stage(client_and_db):
    client = client_and_db
    a = _make_contact(external_user_id="a", display_name="A")
    _make_contact(external_user_id="b", display_name="B")
    client.put(f"/api/customers/{a['id']}", json={"lifecycle_stage": "customer"})

    resp = client.post(
        "/api/broadcasts",
        json={
            "name": "Customer Blast",
            "message_text": "Hello customers!",
            "channel": "telegram",
            "lifecycle_stage": "customer",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recipient_count"] == 1
    assert body["status"] == "draft"


def test_create_rejects_empty_message_text(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/broadcasts",
        json={"name": "Empty", "message_text": "   ", "channel": "telegram"},
    )
    assert resp.status_code == 400


def test_create_rejects_invalid_channel(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/broadcasts",
        json={"name": "Bad Channel", "message_text": "hi", "channel": "carrier-pigeon"},
    )
    assert resp.status_code == 400


def test_send_dispatches_to_every_matching_recipient_and_updates_status(client_and_db):
    client = client_and_db
    _make_contact(external_user_id="a", display_name="A")
    _make_contact(external_user_id="b", display_name="B")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "All Telegram", "message_text": "Hi there!", "channel": "telegram"},
    )
    assert create_resp.status_code == 200, create_resp.text
    broadcast = create_resp.json()
    assert broadcast["recipient_count"] == 2

    with patch(
        "backend.services.broadcast_service.send_telegram_text",
        return_value={"ok": True},
    ) as mock_send:
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")

    assert send_resp.status_code == 200, send_resp.text
    body = send_resp.json()
    assert body["status"] == "sent"
    assert body["sent_count"] == 2
    assert body["failed_count"] == 0
    assert mock_send.call_count == 2


def test_send_counts_a_failed_send_without_crashing_the_whole_send(client_and_db):
    client = client_and_db
    _make_contact(external_user_id="a", display_name="A")
    _make_contact(external_user_id="b", display_name="B")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Mixed Results", "message_text": "Hi there!", "channel": "telegram"},
    )
    broadcast = create_resp.json()

    with patch(
        "backend.services.broadcast_service.send_telegram_text",
        side_effect=[{"ok": True}, Exception("network exploded")],
    ):
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")

    assert send_resp.status_code == 200, send_resp.text
    body = send_resp.json()
    assert body["status"] == "sent"
    assert body["sent_count"] == 1
    assert body["failed_count"] == 1


def test_stuck_sending_broadcast_can_be_resumed_without_double_sending(client_and_db):
    """Before this fix, a broadcast interrupted mid-send (e.g. a request
    timeout on a large recipient list) was stuck in 'sending' forever -
    only 'draft' could start a send, and nothing ever finalized it.
    Calling send again on a 'sending' broadcast must now resume: skip
    whoever's already confirmed sent, and only dispatch to the rest."""
    from database.database import db

    client = client_and_db
    _make_contact(external_user_id="a", display_name="A")
    _make_contact(external_user_id="b", display_name="B")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Interrupted Broadcast", "message_text": "Hi there!", "channel": "telegram"},
    )
    broadcast_id = create_resp.json()["id"]

    # Simulate an interrupted first attempt: status stuck at 'sending',
    # with only recipient "a" recorded as actually sent.
    with db.connect() as conn:
        conn.execute("UPDATE broadcasts SET status = 'sending' WHERE id = ?", (broadcast_id,))
        conn.execute(
            "INSERT INTO broadcast_recipients (broadcast_id, channel, external_user_id, send_status, created_at) "
            "VALUES (?, 'telegram', 'a', 'sent', datetime('now'))",
            (broadcast_id,),
        )
        conn.commit()

    with patch(
        "backend.services.broadcast_service.send_telegram_text",
        return_value={"ok": True},
    ) as mock_send:
        resume_resp = client.post(f"/api/broadcasts/{broadcast_id}/send")

    assert resume_resp.status_code == 200, resume_resp.text
    body = resume_resp.json()
    assert body["status"] == "sent"
    assert body["sent_count"] == 2  # the earlier "a" plus this call's "b"
    assert body["failed_count"] == 0
    # Only "b" (the not-yet-sent recipient) was actually dispatched.
    assert mock_send.call_count == 1
    assert mock_send.call_args.kwargs["recipient_id"] == "b"


def test_send_returns_400_on_already_sent_broadcast(client_and_db):
    client = client_and_db
    _make_contact(external_user_id="a", display_name="A")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Once Only", "message_text": "Hi there!", "channel": "telegram"},
    )
    broadcast = create_resp.json()

    with patch("backend.services.broadcast_service.send_telegram_text", return_value={"ok": True}):
        first = client.post(f"/api/broadcasts/{broadcast['id']}/send")
    assert first.status_code == 200, first.text

    with patch("backend.services.broadcast_service.send_telegram_text", return_value={"ok": True}) as mock_send:
        second = client.post(f"/api/broadcasts/{broadcast['id']}/send")
    assert second.status_code == 400
    mock_send.assert_not_called()


def test_send_uses_correct_sender_and_success_flag_per_channel(client_and_db):
    """whatsapp's sender returns {'sent': bool} not {'ok': bool} — make
    sure the dispatch checks the right key for that channel."""
    client = client_and_db
    _make_contact(channel="whatsapp", external_user_id="wa-1", display_name="WA Contact")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "WA Blast", "message_text": "Hi via WhatsApp!", "channel": "whatsapp"},
    )
    broadcast = create_resp.json()
    assert broadcast["recipient_count"] == 1

    with patch(
        "backend.services.broadcast_service.send_whatsapp_text",
        return_value={"sent": True},
    ) as mock_send:
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")

    assert send_resp.status_code == 200, send_resp.text
    body = send_resp.json()
    assert body["sent_count"] == 1
    assert body["failed_count"] == 0
    mock_send.assert_called_once()


def test_segment_based_targeting_resolves_the_right_recipients(client_and_db):
    client = client_and_db
    a = _make_contact(external_user_id="a", display_name="A")
    _make_contact(external_user_id="b", display_name="B")
    client.put(f"/api/customers/{a['id']}", json={"lifecycle_stage": "customer", "tags": ["reseller"]})

    segment_resp = client.post(
        "/api/customer-segments",
        json={"name": "Active Resellers", "filters": {"lifecycle_stage": "customer", "tag": "reseller"}},
    )
    assert segment_resp.status_code == 200, segment_resp.text
    segment = segment_resp.json()

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Reseller Blast", "message_text": "Hi resellers!", "channel": "telegram", "segment_id": segment["id"]},
    )
    assert create_resp.status_code == 200, create_resp.text
    body = create_resp.json()
    assert body["recipient_count"] == 1

    with patch(
        "backend.services.broadcast_service.send_telegram_text",
        return_value={"ok": True},
    ) as mock_send:
        send_resp = client.post(f"/api/broadcasts/{body['id']}/send")
    assert send_resp.status_code == 200, send_resp.text
    assert send_resp.json()["sent_count"] == 1
    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["recipient_id"] == "a"


def test_create_with_unknown_segment_id_returns_404(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/broadcasts",
        json={"name": "Ghost Segment", "message_text": "hi", "channel": "telegram", "segment_id": 99999},
    )
    assert resp.status_code == 404


def test_company_isolation_for_broadcasts_and_segments(client_and_db):
    from database.database import db
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    from backend.services.customer_service import customer_service
    from backend.services.broadcast_service import broadcast_service

    customer_service.upsert_from_channel(
        company_id=2, channel="telegram", external_user_id="other-co-user", display_name="Other Co Contact",
    )
    other_segment = customer_service.create_segment(company_id=2, name="Other Co Segment", filters={}, actor_user_id=None)
    other_broadcast = broadcast_service.create_broadcast(
        company_id=2, name="Other Co Broadcast", message_text="hi", channel="telegram", actor_user_id=None,
    )

    client = client_and_db
    list_resp = client.get("/api/broadcasts")
    assert list_resp.status_code == 200
    assert all(item["name"] != "Other Co Broadcast" for item in list_resp.json()["items"])

    get_resp = client.get(f"/api/broadcasts/{other_broadcast['id']}")
    assert get_resp.status_code == 404

    # A segment from another company must not be usable to target company 1's broadcast.
    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Cross-Company Attempt", "message_text": "hi", "channel": "telegram", "segment_id": other_segment["id"]},
    )
    assert create_resp.status_code == 404


def test_send_records_provider_message_id_and_sent_delivery_status_for_telegram(client_and_db):
    client = client_and_db
    _make_contact(external_user_id="a", display_name="A")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Telegram Tracking", "message_text": "Hi there!", "channel": "telegram"},
    )
    broadcast = create_resp.json()

    with patch(
        "backend.services.broadcast_service.send_telegram_text",
        return_value={"ok": True, "response": {"result": {"message_id": 555}}},
    ):
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")
    assert send_resp.status_code == 200, send_resp.text

    from database.database import db
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM broadcast_recipients WHERE broadcast_id = ?",
            (broadcast["id"],),
        ).fetchone()
    assert row is not None
    assert row["provider_message_id"] == "555"
    assert row["send_status"] == "sent"
    assert row["external_user_id"] == "a"

    from backend.services.message_status_service import message_status_service
    statuses = message_status_service.get_statuses(channel="telegram", provider_message_ids=["555"])
    assert statuses["555"] == "sent"


def test_get_broadcast_report_totals_match_after_send(client_and_db):
    client = client_and_db
    _make_contact(external_user_id="a", display_name="A")
    _make_contact(external_user_id="b", display_name="B")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Report Totals", "message_text": "Hi there!", "channel": "telegram"},
    )
    broadcast = create_resp.json()

    with patch(
        "backend.services.broadcast_service.send_telegram_text",
        side_effect=[
            {"ok": True, "response": {"result": {"message_id": 111}}},
            Exception("network exploded"),
        ],
    ):
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")
    assert send_resp.status_code == 200, send_resp.text

    report_resp = client.get(f"/api/broadcasts/{broadcast['id']}/report")
    assert report_resp.status_code == 200, report_resp.text
    report = report_resp.json()

    assert report["channel_tracking_supported"] is True
    assert report["totals"]["recipients"] == 2
    assert report["totals"]["sent"] == 1
    assert report["totals"]["failed"] == 1
    assert report["totals"]["pending"] == 1
    assert report["totals"]["delivered"] == 0
    assert report["totals"]["read"] == 0
    assert len(report["recipients"]) == 2

    failed_row = next(r for r in report["recipients"] if r["send_status"] == "failed")
    assert failed_row["error"] == "network exploded"
    assert failed_row["delivery_status"] is None

    sent_row = next(r for r in report["recipients"] if r["send_status"] == "sent")
    assert sent_row["delivery_status"] == "sent"
    assert sent_row["error"] is None


def test_get_broadcast_report_reflects_read_status_after_webhook_update(client_and_db):
    client = client_and_db
    _make_contact(channel="messenger", external_user_id="a", display_name="A")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Read Tracking", "message_text": "Hi there!", "channel": "messenger"},
    )
    broadcast = create_resp.json()

    with patch(
        "backend.services.broadcast_service.send_meta_text",
        return_value={"ok": True, "response": {"message_id": "mid.abc123"}},
    ):
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")
    assert send_resp.status_code == 200, send_resp.text

    # Simulate a delivery/read webhook arriving later for this provider_message_id.
    from backend.services.message_status_service import message_status_service
    message_status_service.update_status(channel="messenger", provider_message_id="mid.abc123", status="read")

    report = client.get(f"/api/broadcasts/{broadcast['id']}/report").json()
    assert report["totals"]["read"] == 1
    assert report["totals"]["delivered"] == 1
    assert report["totals"]["pending"] == 0

    recipient = report["recipients"][0]
    assert recipient["delivery_status"] == "read"


def test_whatsapp_broadcast_report_shows_tracking_unsupported(client_and_db):
    client = client_and_db
    _make_contact(channel="whatsapp", external_user_id="wa-1", display_name="WA Contact")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "WA Report", "message_text": "Hi via WhatsApp!", "channel": "whatsapp"},
    )
    broadcast = create_resp.json()

    with patch(
        "backend.services.broadcast_service.send_whatsapp_text",
        return_value={"sent": True},
    ):
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")
    assert send_resp.status_code == 200, send_resp.text

    report = client.get(f"/api/broadcasts/{broadcast['id']}/report").json()
    assert report["channel_tracking_supported"] is False
    assert report["totals"]["sent"] == 1
    assert report["totals"]["pending"] == 0
    recipient = report["recipients"][0]
    assert recipient["send_status"] == "sent"
    assert recipient["delivery_status"] is None


def test_failed_send_creates_broadcast_recipient_row_without_crashing(client_and_db):
    client = client_and_db
    _make_contact(external_user_id="a", display_name="A")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Failure Row", "message_text": "hi", "channel": "telegram"},
    )
    broadcast = create_resp.json()

    with patch(
        "backend.services.broadcast_service.send_telegram_text",
        return_value={"ok": False, "error": "Telegram rejected the message."},
    ):
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")
    assert send_resp.status_code == 200, send_resp.text
    assert send_resp.json()["failed_count"] == 1

    from database.database import db
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM broadcast_recipients WHERE broadcast_id = ?",
            (broadcast["id"],),
        ).fetchone()
    assert row["send_status"] == "failed"
    assert row["provider_message_id"] is None
    assert row["error"] == "Telegram rejected the message."


def test_get_broadcast_report_returns_404_for_unknown_broadcast(client_and_db):
    client = client_and_db
    resp = client.get("/api/broadcasts/99999/report")
    assert resp.status_code == 404


def test_create_with_numbers_creates_new_customers_with_lead_stage_and_whatsapp_identity(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/broadcasts",
        json={
            "name": "Cold Outreach",
            "message_text": "Hi there!",
            "channel": "whatsapp",
            "numbers": ["+15550100", "+15550101"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recipient_count"] == 2
    assert body["status"] == "draft"

    from backend.services.customer_service import customer_service

    listing = customer_service.list_customers(company_id=COMPANY_ID)
    matches = [
        item for item in listing["items"]
        if "whatsapp" in item["channels"]
    ]
    assert len(matches) == 2
    for item in matches:
        assert item["lifecycle_stage"] == "lead"
        full = customer_service.get_customer(company_id=COMPANY_ID, customer_id=item["id"])
        whatsapp_identities = [i for i in full["identities"] if i["channel"] == "whatsapp"]
        assert len(whatsapp_identities) == 1
        assert whatsapp_identities[0]["external_user_id"] in ("+15550100", "+15550101")


def test_create_with_numbers_reuses_existing_customer_for_a_known_number(client_and_db):
    client = client_and_db
    existing = _make_contact(channel="whatsapp", external_user_id="+15550199", display_name="Existing WA Contact")

    resp = client.post(
        "/api/broadcasts",
        json={
            "name": "Reuse Existing",
            "message_text": "Hi again!",
            "channel": "whatsapp",
            "numbers": ["+15550199", "+15550200"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recipient_count"] == 2

    from backend.services.customer_service import customer_service

    listing = customer_service.list_customers(company_id=COMPANY_ID)
    matches = [item for item in listing["items"] if "whatsapp" in item["channels"]]
    # Exactly two whatsapp-linked customers total: the pre-existing one
    # (reused, not duplicated) plus the one new number.
    assert len(matches) == 2
    assert any(item["id"] == existing["id"] for item in matches)


def test_create_rejects_numbers_and_segment_id_together(client_and_db):
    client = client_and_db
    segment_resp = client.post(
        "/api/customer-segments",
        json={"name": "Everyone", "filters": {}},
    )
    assert segment_resp.status_code == 200, segment_resp.text
    segment = segment_resp.json()

    resp = client.post(
        "/api/broadcasts",
        json={
            "name": "Conflicting Targeting",
            "message_text": "hi",
            "channel": "whatsapp",
            "segment_id": segment["id"],
            "numbers": ["+15550100"],
        },
    )
    assert resp.status_code == 400


def test_create_rejects_numbers_on_non_whatsapp_channel(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/broadcasts",
        json={
            "name": "Numbers On Telegram",
            "message_text": "hi",
            "channel": "telegram",
            "numbers": ["+15550100"],
        },
    )
    assert resp.status_code == 400


def test_send_on_numbers_targeted_broadcast_dispatches_to_reresolved_recipients(client_and_db):
    client = client_and_db
    create_resp = client.post(
        "/api/broadcasts",
        json={
            "name": "WA Numbers Send",
            "message_text": "Hi via numbers!",
            "channel": "whatsapp",
            "numbers": ["+15550111", "+15550112"],
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    broadcast = create_resp.json()
    assert broadcast["recipient_count"] == 2

    with patch(
        "backend.services.broadcast_service.send_whatsapp_text",
        return_value={"sent": True},
    ) as mock_send:
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")

    assert send_resp.status_code == 200, send_resp.text
    body = send_resp.json()
    assert body["status"] == "sent"
    assert body["sent_count"] == 2
    assert body["failed_count"] == 0
    assert mock_send.call_count == 2
    dispatched_ids = {call.args[0] for call in mock_send.call_args_list}
    assert dispatched_ids == {"+15550111", "+15550112"}


def test_numbers_list_normalization_dedupes_and_skips_malformed_entries(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/broadcasts",
        json={
            "name": "Messy List",
            "message_text": "hi",
            "channel": "whatsapp",
            "numbers": ["+1 555 0100", "15550100", "", "  "],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Normalization (re.sub(r"[^\d+]", "", ...)) strips whitespace/formatting
    # but does NOT treat a leading "+" as equivalent to no "+" — so
    # "+1 555 0100" -> "+15550100" and "15550100" -> "15550100" remain two
    # distinct recipients. The two blank/whitespace-only entries normalize
    # to "" and are skipped. Net result: 2 recipients, not 4.
    assert body["recipient_count"] == 2

    import json as jsonlib
    from database.database import db as _db
    with _db.connect() as conn:
        row = conn.execute(
            "SELECT raw_numbers_json FROM broadcasts WHERE id = ?", (body["id"],),
        ).fetchone()
    stored_numbers = jsonlib.loads(row["raw_numbers_json"])
    assert sorted(stored_numbers) == sorted(["+15550100", "15550100"])


def test_delete_only_allowed_while_draft(client_and_db):
    client = client_and_db
    _make_contact(external_user_id="a", display_name="A")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Draft To Delete", "message_text": "hi", "channel": "telegram"},
    )
    broadcast = create_resp.json()

    delete_resp = client.delete(f"/api/broadcasts/{broadcast['id']}")
    assert delete_resp.status_code == 200, delete_resp.text
    assert delete_resp.json() == {"deleted": True}
    assert client.get(f"/api/broadcasts/{broadcast['id']}").status_code == 404

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Sent Broadcast", "message_text": "hi", "channel": "telegram"},
    )
    broadcast2 = create_resp.json()
    with patch("backend.services.broadcast_service.send_telegram_text", return_value={"ok": True}):
        client.post(f"/api/broadcasts/{broadcast2['id']}/send")

    delete_after_send_resp = client.delete(f"/api/broadcasts/{broadcast2['id']}")
    assert delete_after_send_resp.status_code == 400


def test_create_with_media_stores_url_and_type(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/broadcasts",
        json={
            "name": "With Image", "message_text": "check this out", "channel": "telegram",
            "media_url": "https://example.com/promo.jpg", "media_type": "image",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["media_url"] == "https://example.com/promo.jpg"
    assert body["media_type"] == "image"


def test_create_with_media_rejects_unsupported_type(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/broadcasts",
        json={
            "name": "Bad Media", "message_text": "x", "channel": "telegram",
            "media_url": "https://example.com/file.exe", "media_type": "executable",
        },
    )
    assert resp.status_code == 400


def test_send_with_media_dispatches_via_media_sender(client_and_db):
    client = client_and_db
    _make_contact(external_user_id="a", display_name="A")

    create_resp = client.post(
        "/api/broadcasts",
        json={
            "name": "Media Blast", "message_text": "look at this", "channel": "telegram",
            "media_url": "https://example.com/promo.jpg", "media_type": "image",
        },
    )
    broadcast = create_resp.json()

    with patch(
        "backend.services.broadcast_service.send_telegram_media",
        return_value={"ok": True},
    ) as mock_media_send, patch(
        "backend.services.broadcast_service.send_telegram_text",
    ) as mock_text_send:
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")

    assert send_resp.status_code == 200, send_resp.text
    assert send_resp.json()["sent_count"] == 1
    mock_media_send.assert_called_once()
    assert mock_media_send.call_args.kwargs["media_url"] == "https://example.com/promo.jpg"
    assert mock_media_send.call_args.kwargs["media_type"] == "image"
    assert mock_media_send.call_args.kwargs["caption"] == "look at this"
    mock_text_send.assert_not_called()


def test_send_without_media_still_uses_text_sender(client_and_db):
    client = client_and_db
    _make_contact(external_user_id="a", display_name="A")

    create_resp = client.post(
        "/api/broadcasts",
        json={"name": "Plain Text", "message_text": "hello", "channel": "telegram"},
    )
    broadcast = create_resp.json()

    with patch(
        "backend.services.broadcast_service.send_telegram_media",
    ) as mock_media_send, patch(
        "backend.services.broadcast_service.send_telegram_text", return_value={"ok": True},
    ) as mock_text_send:
        send_resp = client.post(f"/api/broadcasts/{broadcast['id']}/send")

    assert send_resp.status_code == 200, send_resp.text
    mock_text_send.assert_called_once()
    mock_media_send.assert_not_called()
