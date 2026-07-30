import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1


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
        return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

    yield TestClient(app)

    app.dependency_overrides.clear()
    db.db_path = original_db_path
    if upload_root.exists():
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


def test_upload_image_returns_url_and_media_type(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/media/upload",
        files={"file": ("promo.jpg", b"\xff\xd8\xff\xe0fakejpegbytes", "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["media_type"] == "image"
    assert body["url"].endswith(".jpg")


def test_upload_video_and_audio_types(client_and_db):
    client = client_and_db
    video_resp = client.post("/api/media/upload", files={"file": ("clip.mp4", b"fakevideo", "video/mp4")})
    assert video_resp.json()["media_type"] == "video"

    audio_resp = client.post("/api/media/upload", files={"file": ("voice.mp3", b"fakeaudio", "audio/mpeg")})
    assert audio_resp.json()["media_type"] == "audio"


def test_upload_rejects_unsupported_extension(client_and_db):
    client = client_and_db
    resp = client.post("/api/media/upload", files={"file": ("script.exe", b"MZ", "application/octet-stream")})
    assert resp.status_code == 400


def test_upload_rejects_empty_file(client_and_db):
    client = client_and_db
    resp = client.post("/api/media/upload", files={"file": ("empty.jpg", b"", "image/jpeg")})
    assert resp.status_code == 400


def test_upload_rejects_oversized_file(client_and_db):
    import backend.services.media_upload_service as media_upload_module

    client = client_and_db
    media_upload_module.MAX_UPLOAD_BYTES = 10
    try:
        resp = client.post("/api/media/upload", files={"file": ("big.jpg", b"x" * 100, "image/jpeg")})
        assert resp.status_code == 400
    finally:
        media_upload_module.MAX_UPLOAD_BYTES = 16 * 1024 * 1024
