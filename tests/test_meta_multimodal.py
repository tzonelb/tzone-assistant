"""
Real tests for Messenger/Instagram voice-note + image handling in
channels/meta/processor.py — the same STT/vision multimodal pipeline
already covered for WhatsApp (tests/test_whatsapp_incoming.py) and
Telegram (tests/test_multimodal_ai.py), extended to Meta channels.

Run with: python3 -m pytest tests/test_meta_multimodal.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1
CUSTOMER_ID = "1234567890"
RECIPIENT_ID = "999999999"


@pytest.fixture()
def fresh_env(tmp_path):
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service
    from backend.services.notification_service import notification_service
    from backend.services.customer_service import customer_service
    import core.conversation_store as conversation_store

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    original_base_dir = conversation_store.BASE_DIR
    conversation_store.BASE_DIR = tmp_path / "conversations"
    conversation_store.BASE_DIR.mkdir(parents=True, exist_ok=True)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()
    notification_service.ensure_schema()
    customer_service.ensure_schema()

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.commit()

    yield

    conversation_store.BASE_DIR = original_base_dir
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


def _audio_payload():
    return {
        "object": "page",
        "entry": [{"messaging": [{
            "sender": {"id": CUSTOMER_ID},
            "recipient": {"id": RECIPIENT_ID},
            "message": {"mid": "m1", "attachments": [{"type": "audio", "payload": {"url": "https://cdn.example/voice.ogg"}}]},
        }]}],
    }


def _image_payload():
    return {
        "object": "page",
        "entry": [{"messaging": [{
            "sender": {"id": CUSTOMER_ID},
            "recipient": {"id": RECIPIENT_ID},
            "message": {"mid": "m2", "attachments": [{"type": "image", "payload": {"url": "https://cdn.example/pic.jpg"}}]},
        }]}],
    }


def _common_patches():
    return [
        patch("channels.meta.processor.resolve_meta_profile", return_value={}),
        patch("channels.meta.processor.schedule_smart_reply", return_value={"queued": True}),
    ]


def test_meta_webhook_transcribes_voice_note_and_forwards_text(fresh_env):
    from channels.meta.processor import process_meta_payload

    with patch("channels.meta.processor.resolve_meta_profile", return_value={}), \
            patch("channels.meta.processor.schedule_smart_reply", return_value={"queued": True}), \
            patch("channels.meta.processor.download_meta_attachment", return_value=b"fake-audio"), \
            patch("channels.meta.processor.stt_service") as mock_stt:
        mock_stt.transcribe.return_value = "I need help with my order"
        result = process_meta_payload(_audio_payload())

    assert result["text"] == "I need help with my order"
    assert result["status"] == "received_ai_queued"

    import core.conversation_store as conversation_store
    saved_file = conversation_store.BASE_DIR / "messenger" / f"{CUSTOMER_ID}.jsonl"
    assert saved_file.exists()
    assert '"source_type": "audio"' in saved_file.read_text(encoding="utf-8")


def test_meta_webhook_describes_image_and_forwards_text(fresh_env):
    from channels.meta.processor import process_meta_payload

    with patch("channels.meta.processor.resolve_meta_profile", return_value={}), \
            patch("channels.meta.processor.schedule_smart_reply", return_value={"queued": True}), \
            patch("channels.meta.processor.download_meta_attachment", return_value=b"fake-image"), \
            patch("channels.meta.processor.vision_service") as mock_vision:
        mock_vision.describe_image.return_value = "A cracked phone screen"
        result = process_meta_payload(_image_payload())

    assert "A cracked phone screen" in result["text"]
    assert result["status"] == "received_ai_queued"

    import core.conversation_store as conversation_store
    saved_file = conversation_store.BASE_DIR / "messenger" / f"{CUSTOMER_ID}.jsonl"
    assert '"source_type": "image"' in saved_file.read_text(encoding="utf-8")


def test_meta_webhook_notifies_customer_when_attachment_unreadable(fresh_env):
    """A voice note/image we can't process must not vanish silently -
    the customer should hear back, and the failure must be visible for
    monitoring instead of just disappearing."""
    from channels.meta.processor import process_meta_payload

    with patch("channels.meta.processor.resolve_meta_profile", return_value={}), \
            patch("channels.meta.processor.download_meta_attachment", return_value=None), \
            patch("channels.meta.processor.send_meta_text", return_value={"ok": True}) as mock_send, \
            patch("channels.meta.processor.diagnostics_service") as mock_diagnostics:
        result = process_meta_payload(_audio_payload())

    assert result["status"] == "unsupported"
    mock_send.assert_called_once()
    assert mock_send.call_args.args[0] == CUSTOMER_ID
    mock_diagnostics.record.assert_called_once()
    assert mock_diagnostics.record.call_args.kwargs["event_type"] == "attachment_processing_failed"
    assert mock_diagnostics.record.call_args.kwargs["data"]["attachment_type"] == "audio"
