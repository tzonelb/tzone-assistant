"""A stored attachment must not outlive the thing it belonged to.

`GET /api/media/{company_id}/{stored_name}` is deliberately unauthenticated:
Meta, WhatsApp and Telegram fetch an attachment from their own servers, with no
session of ours, so a dependency there would stop every attachment from being
delivered. The unguessable name is the credential.

That design is defensible only while the *file* decides whether the URL answers,
because the file is the only revocation there is. Two paths broke that:

* A voice note of the wrong kind was written to disk and only then refused. The
  employee was told the upload failed; the bytes stayed, unreferenced by any
  conversation, unreachable through the product, and still served.
* Removing a company deleted its encrypted database and every control-plane row
  but left its upload directory untouched -- so a deleted company's customer
  attachments remained fetchable by anyone holding a link, with no company, no
  session and no record left to revoke them through.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "OwnerPass123!"


@pytest.fixture()
def wired(platform, monkeypatch, tmp_path):
    """The services, pointed at this test's data directory."""
    from database.manager import DatabaseManager
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from backend.services.media_upload_service import media_upload_service
    from config.settings import config

    # The service reads config.UPLOAD_DIR on every call, so pointing that at the
    # test's own directory is enough and nothing is written outside it.
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "uploads"))

    return media_upload_service


def _stored_files(service, company_id):
    directory = service._company_dir(company_id)

    if not directory.is_dir():
        return []

    return sorted(child.name for child in directory.iterdir() if child.is_file())


def test_a_stored_file_can_be_removed_by_name(wired, alpha):
    stored = wired.save(
        company_id=alpha["id"],
        content=b"\x89PNG\r\n\x1a\n" + b"0" * 32,
        filename="shot.png",
    )

    assert _stored_files(wired, alpha["id"]) == [stored["stored_name"]]
    assert wired.remove(company_id=alpha["id"], stored_name=stored["stored_name"])
    assert _stored_files(wired, alpha["id"]) == []


def test_remove_refuses_a_name_that_is_not_one_it_wrote(wired, alpha):
    """A delete must never take a path from its caller."""
    wired.save(
        company_id=alpha["id"],
        content=b"data",
        filename="keep.png",
    )

    assert not wired.remove(company_id=alpha["id"], stored_name="../../etc/passwd")
    assert not wired.remove(company_id=alpha["id"], stored_name="keep.png")
    assert len(_stored_files(wired, alpha["id"])) == 1


def test_removing_a_company_clears_its_uploads(wired, platform, alpha):
    """The gap that let a deleted company's attachments stay public."""
    from backend.services.platform_service import platform_service

    wired.save(
        company_id=alpha["id"],
        content=b"customer receipt",
        filename="receipt.pdf",
    )
    assert _stored_files(wired, alpha["id"])

    platform_service._rollback_company(alpha["id"])

    assert _stored_files(wired, alpha["id"]) == []


def test_one_companys_removal_leaves_another_companys_uploads(wired, alpha, beta):
    """Deletion is scoped, like everything else keyed by company."""
    from backend.services.platform_service import platform_service

    wired.save(
        company_id=beta["id"],
        content=b"beta's file",
        filename="beta.png",
    )
    wired.save(
        company_id=alpha["id"],
        content=b"alpha's file",
        filename="alpha.png",
    )

    platform_service._rollback_company(alpha["id"])

    assert _stored_files(wired, alpha["id"]) == []
    assert len(_stored_files(wired, beta["id"])) == 1
