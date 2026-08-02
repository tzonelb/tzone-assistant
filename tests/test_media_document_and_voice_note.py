"""
Covers the two new pieces added for the Conversations composer's
Attachment/Voice note buttons (previously disabled "coming soon"
no-ops):
  1. "document" media type support in send_meta_media (Messenger's
     generic "file" attachment type) and media_upload_service's
     extension allowlist.
  2. POST /api/media/upload-voice-note, which transcodes whatever a
     browser's MediaRecorder produces into mp3 before storing it,
     since WhatsApp Cloud API rejects raw webm/ogg audio outright.

Run with: python3 -m pytest tests/test_media_document_and_voice_note.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def test_send_meta_media_document_uses_file_attachment_type():
    from channels.meta.sender import send_meta_media

    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.status_code = 200
    fake_response.content = b'{"message_id": "mid.1"}'
    fake_response.json.return_value = {"message_id": "mid.1"}

    with patch("channels.meta.sender._resolve_token", return_value="fake-token"), \
         patch("channels.meta.sender.requests.post", return_value=fake_response) as mock_post:
        result = send_meta_media(
            recipient_id="1234567890",
            media_url="https://cdn.example/brochure.pdf",
            media_type="document",
            channel="messenger",
        )

    assert result["ok"] is True
    payload = mock_post.call_args.kwargs["json"]
    assert payload["message"]["attachment"]["type"] == "file"
    assert payload["message"]["attachment"]["payload"]["url"] == "https://cdn.example/brochure.pdf"


def test_send_meta_media_rejects_truly_unsupported_type():
    from channels.meta.sender import send_meta_media

    result = send_meta_media(recipient_id="1234567890", media_url="https://cdn.example/x.exe", media_type="executable")
    assert result["ok"] is False


def test_media_upload_service_accepts_document_extensions(tmp_path, monkeypatch):
    import backend.services.media_upload_service as media_upload_module

    monkeypatch.setattr(media_upload_module, "UPLOAD_ROOT", tmp_path / "uploads")
    result = media_upload_module.media_upload_service.save_upload(
        filename="brochure.pdf", content=b"%PDF-1.4 fake pdf bytes",
    )
    assert result["media_type"] == "document"
    assert result["url"].endswith(".pdf")
    assert result["filename"] == "brochure.pdf"


@pytest.fixture()
def client_and_db(tmp_path, monkeypatch):
    from database.database import db
    from backend.services.auth_service import auth_service
    import backend.services.media_upload_service as media_upload_module

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()

    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(media_upload_module, "UPLOAD_ROOT", upload_root)

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')")
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": 1}
    app.dependency_overrides[get_current_user] = _override

    from fastapi.testclient import TestClient
    yield TestClient(app)

    app.dependency_overrides.clear()
    db.db_path = original_db_path
    if upload_root.exists():
        import shutil
        shutil.rmtree(upload_root, ignore_errors=True)
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_db_path):
                os.remove(tmp_db_path)
            break
        except PermissionError:
            time.sleep(0.1)


def test_upload_voice_note_transcodes_then_stores_as_mp3(client_and_db):
    client = client_and_db

    with patch("backend.api.routes.media_uploads.transcode_to_mp3", return_value=b"fake mp3 bytes") as mock_transcode:
        resp = client.post(
            "/api/media/upload-voice-note",
            files={"file": ("voice-note.webm", b"fake webm audio bytes", "audio/webm")},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["media_type"] == "audio"
    assert body["url"].endswith(".mp3")
    mock_transcode.assert_called_once_with(b"fake webm audio bytes")


def test_upload_voice_note_returns_400_when_transcode_fails(client_and_db):
    client = client_and_db
    from core.audio_transcode import AudioTranscodeError

    with patch(
        "backend.api.routes.media_uploads.transcode_to_mp3",
        side_effect=AudioTranscodeError("Voice note could not be converted to a playable format."),
    ):
        resp = client.post(
            "/api/media/upload-voice-note",
            files={"file": ("voice-note.webm", b"not really audio", "audio/webm")},
        )

    assert resp.status_code == 400
    assert "converted" in resp.json()["detail"]
