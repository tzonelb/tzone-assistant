"""Files an employee attaches to a reply.

The storage is not encrypted the way the databases are, and it cannot be: the
channel fetches the file over plain HTTPS to deliver it, so a key only this
server holds would make it unreadable to the recipient. What has to hold instead
is everything around it, and that is what this file pins.

The name is generated, never the uploader's. A filename is attacker-controlled
text that would otherwise become a path, and two people attaching "invoice.pdf"
must not overwrite each other. Reading back accepts only the shape this service
writes, so a traversal attempt is refused before it touches the filesystem.

The type is decided at upload. Accepting anything and discovering at send time
that the channel will not take it leaves the employee having already told the
customer the file is coming.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def media(monkeypatch, tmp_path):
    from config.settings import config

    import backend.services.media_upload_service as module

    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads", raising=False)

    return module.media_upload_service


def _png() -> bytes:
    # Content is never parsed; only the extension decides the kind.
    return b"\x89PNG\r\n\x1a\n" + b"0" * 64


# ------------------------------------------------------------------- storing


def test_a_file_is_stored_under_a_generated_name(media):
    stored = media.save(company_id=1, filename="invoice.png", content=_png())

    assert stored["media_type"] == "image"
    assert stored["filename"] == "invoice.png"
    # The name on disk is generated; the uploader's is kept only as a label.
    assert stored["stored_name"] != "invoice.png"
    assert stored["stored_name"].endswith(".png")
    assert stored["url"] == f"/api/media/1/{stored['stored_name']}"

    assert media.path_for(
        company_id=1, stored_name=stored["stored_name"]
    ).read_bytes() == _png()


def test_two_files_with_the_same_name_do_not_overwrite_each_other(media):
    first = media.save(company_id=1, filename="invoice.pdf", content=b"one")
    second = media.save(company_id=1, filename="invoice.pdf", content=b"two")

    assert first["stored_name"] != second["stored_name"]
    assert media.path_for(company_id=1, stored_name=first["stored_name"]).read_bytes() == b"one"


def test_a_filename_cannot_escape_the_company_directory(media):
    """The name is attacker-controlled. Only its suffix is used, and only after
    it matches the allow-list, so the path components contribute nothing."""
    stored = media.save(
        company_id=1, filename="../../../../etc/passwd.png", content=_png()
    )

    path = media.path_for(company_id=1, stored_name=stored["stored_name"])
    assert path.parent.name == "company_1"
    assert "etc" not in str(path)


# ------------------------------------------------------------------ refusing


def test_an_unsupported_type_is_refused_at_upload(media):
    from backend.services.media_upload_service import MediaUploadError

    with pytest.raises(MediaUploadError):
        media.save(company_id=1, filename="payload.exe", content=b"MZ")


def test_an_empty_file_is_refused(media):
    from backend.services.media_upload_service import MediaUploadError

    with pytest.raises(MediaUploadError):
        media.save(company_id=1, filename="empty.png", content=b"")


def test_a_file_over_the_ceiling_is_refused(media):
    from backend.services.media_upload_service import (
        MAX_UPLOAD_BYTES,
        MediaUploadError,
    )

    with pytest.raises(MediaUploadError):
        media.save(
            company_id=1, filename="huge.png", content=b"0" * (MAX_UPLOAD_BYTES + 1)
        )


# ------------------------------------------------------------------- reading


@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "subdir/file.png",
        "notahexname.png",
        "",
    ],
)
def test_only_a_name_this_service_wrote_can_be_read_back(media, name):
    from backend.services.media_upload_service import MediaUploadError

    with pytest.raises(MediaUploadError):
        media.path_for(company_id=1, stored_name=name)


def test_one_companys_upload_is_not_reachable_through_another_id(media):
    from backend.services.media_upload_service import MediaUploadError

    stored = media.save(company_id=1, filename="private.png", content=_png())

    with pytest.raises(MediaUploadError):
        media.path_for(company_id=2, stored_name=stored["stored_name"])
