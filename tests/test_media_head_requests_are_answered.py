"""HEAD on the public media URL must answer the same as GET.

Reproduced live, not imagined: a plain `@router.get(...)` on this route does
not get HEAD for free in this FastAPI/Starlette version -- a HEAD request to
a path this app only declared for GET does not even come back as a clean 405.
It falls through every registered router to the SPA catch-all mount (see
main.py's `_SinglePageApp`), which -- correctly, for an `/api/` path -- refuses
to serve the app shell and answers a bare 404 instead. The net effect is a
HEAD request to a file that demonstrably exists coming back "not found".

This app's own docstring on the route says why it matters here specifically:
"the channel -- Meta, WhatsApp, Telegram -- fetches the file from this URL to
deliver it", and a HEAD preflight is common behaviour for exactly that kind of
fetch. A 404 there is indistinguishable, to whatever asked, from the
attachment never having existed.

Uses the real app (`main.app`), not an isolated router, because the defect
only appears once the SPA catch-all mount is in the routing table too --
testing the media router alone would never have found it.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def app_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from config.settings import config

    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads", raising=False)

    import main as main_module

    return TestClient(main_module.app)


def _store_a_file():
    from backend.services.media_upload_service import media_upload_service

    return media_upload_service.save(
        company_id=1, filename="photo.jpg", content=b"\xff\xd8\xff" + b"0" * 32
    )


def test_head_and_get_agree_on_a_real_file(app_client):
    stored = _store_a_file()
    url = f"/api/media/1/{stored['stored_name']}"

    get_response = app_client.get(url)
    head_response = app_client.head(url)

    assert get_response.status_code == 200
    assert head_response.status_code == 200, (
        "HEAD returned "
        f"{head_response.status_code} for a file GET can see at the same URL"
    )
    assert head_response.headers.get("content-type") == get_response.headers.get(
        "content-type"
    )
    assert head_response.headers.get("content-length") == str(
        len(get_response.content)
    )


def test_head_on_a_missing_file_is_a_clean_404(app_client):
    response = app_client.head("/api/media/1/" + "0" * 32 + ".jpg")

    assert response.status_code == 404
