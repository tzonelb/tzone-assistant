"""Tests for connecting messaging accounts to a company.

This is what makes the platform genuinely multi-company: routing an inbound
message to the right company, and answering it from that company's own page
rather than a single token shared by everyone.
"""

from __future__ import annotations

import pytest

from backend.security import keyring
from backend.security.keyring import CorruptedKeyMaterial


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the channel service at the test platform's databases."""
    import sys

    import database.manager as manager_module

    # Imported before the sweep below: a module that has not been imported yet
    # holds no reference to rebind, and would later import the real singleton.
    import backend.services.channel_account_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    # Modules that did `from database.manager import database_manager` hold
    # their own reference and must be rebound too, or the test silently runs
    # against the process-wide singleton and proves nothing.
    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.channel_account_service" in rebound

    from backend.services.channel_account_service import channel_account_service

    return channel_account_service


def _connect(service, company, page_id="PAGE_1", token="page-token-1"):
    return service.create_account(
        company_id=company["id"],
        channel="messenger",
        name="Test Page",
        values={"page_id": page_id, "access_token": token},
    )


# ----------------------------------------------------------------------
# Secrets
# ----------------------------------------------------------------------


def test_access_token_is_not_stored_in_readable_form(service, platform, alpha):
    """The control database is shared across companies. A token stored in the
    clear there would be readable by anything that reached that file."""
    _connect(service, alpha, token="super-secret-page-token")

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT access_token_sealed FROM channel_accounts WHERE company_id = ?",
            (alpha["id"],),
        ).fetchone()

    assert row["access_token_sealed"]
    assert "super-secret-page-token" not in row["access_token_sealed"]


def test_token_never_leaves_the_server(service, alpha):
    """The screen only needs to know a credential exists. Returning it would put
    a page token into browser memory and every proxy log in between."""
    account = _connect(service, alpha, token="super-secret-page-token")

    assert account["has_access_token"] is True
    assert "access_token" not in account
    assert "access_token_sealed" not in account


def test_sealed_token_cannot_be_read_with_another_companys_key(
    service, platform, alpha, beta
):
    """Sealing is bound to the company, so lifting a row out of the control
    database and opening it as another company fails."""
    _connect(service, alpha, token="alpha-token")

    with platform["manager"].control() as conn:
        sealed = conn.execute(
            "SELECT access_token_sealed FROM channel_accounts WHERE company_id = ?",
            (alpha["id"],),
        ).fetchone()["access_token_sealed"]

    with pytest.raises(CorruptedKeyMaterial):
        keyring.unseal_secret(
            sealed,
            platform["manager"].company_key(beta["id"]),
            beta["id"],
            "access_token",
        )


def test_sealed_token_cannot_be_moved_to_another_field(service, platform, alpha):
    """Each secret is sealed under its own context, so an access token pasted
    into the verify token column will not open."""
    _connect(service, alpha, token="alpha-token")

    with platform["manager"].control() as conn:
        sealed = conn.execute(
            "SELECT access_token_sealed FROM channel_accounts WHERE company_id = ?",
            (alpha["id"],),
        ).fetchone()["access_token_sealed"]

    with pytest.raises(CorruptedKeyMaterial):
        keyring.unseal_secret(
            sealed,
            platform["manager"].company_key(alpha["id"]),
            alpha["id"],
            "verify_token",
        )


def test_credentials_round_trip_for_sending(service, alpha):
    """The sending path has to get the real token back, or nothing can be sent."""
    _connect(service, alpha, token="alpha-token")

    credentials = service.credentials_for(
        company_id=alpha["id"], channel="messenger"
    )

    assert credentials["access_token"] == "alpha-token"
    assert credentials["page_id"] == "PAGE_1"


# ----------------------------------------------------------------------
# Isolation and routing
# ----------------------------------------------------------------------


def test_each_company_gets_its_own_token(service, alpha, beta):
    """Two companies on one server must answer from their own pages. A single
    shared token would reply to one company's customer from another's page."""
    _connect(service, alpha, page_id="PAGE_ALPHA", token="alpha-token")
    _connect(service, beta, page_id="PAGE_BETA", token="beta-token")

    alpha_credentials = service.credentials_for(
        company_id=alpha["id"], channel="messenger"
    )
    beta_credentials = service.credentials_for(
        company_id=beta["id"], channel="messenger"
    )

    assert alpha_credentials["access_token"] == "alpha-token"
    assert beta_credentials["access_token"] == "beta-token"


def test_a_company_only_lists_its_own_accounts(service, alpha, beta):
    """The channels screen must never show another company's connections."""
    _connect(service, alpha, page_id="PAGE_ALPHA")
    _connect(service, beta, page_id="PAGE_BETA")

    alpha_pages = [item["page_id"] for item in service.list_accounts(alpha["id"])]
    beta_pages = [item["page_id"] for item in service.list_accounts(beta["id"])]

    assert alpha_pages == ["PAGE_ALPHA"]
    assert beta_pages == ["PAGE_BETA"]


def test_connecting_a_page_already_claimed_is_refused(service, alpha, beta):
    """Two rows for one page id would make inbound routing depend on row order,
    silently delivering one company's customers to another."""
    _connect(service, alpha, page_id="SHARED_PAGE")

    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError):
        _connect(service, beta, page_id="SHARED_PAGE")


def test_connected_page_routes_inbound_to_its_company(service, platform, alpha, beta):
    """The point of the whole record: a message on this page reaches this
    company's inbox and no other."""
    _connect(service, alpha, page_id="PAGE_ALPHA")
    _connect(service, beta, page_id="PAGE_BETA")

    manager = platform["manager"]

    assert (
        manager.resolve_company_for_channel(channel="messenger", page_id="PAGE_ALPHA")
        == alpha["id"]
    )
    assert (
        manager.resolve_company_for_channel(channel="messenger", page_id="PAGE_BETA")
        == beta["id"]
    )


def test_no_credentials_for_a_company_without_an_account(service, alpha):
    """Returning nothing forces the caller to refuse to send, rather than
    quietly falling back to somebody else's token."""
    assert service.credentials_for(company_id=alpha["id"], channel="whatsapp") is None


# ----------------------------------------------------------------------
# Validation and updates
# ----------------------------------------------------------------------


def test_account_without_a_routing_identifier_is_refused(service, alpha):
    """An account with no page id receives nothing, so accepting it would create
    a connection that silently never works."""
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError):
        service.create_account(
            company_id=alpha["id"],
            channel="messenger",
            name="Broken",
            values={"access_token": "token"},
        )


def test_rotating_a_token_replaces_it(service, alpha):
    """Tokens expire and get rotated; the new one must take effect immediately."""
    account = _connect(service, alpha, token="old-token")

    service.update_account(
        company_id=alpha["id"],
        account_id=account["id"],
        values={"access_token": "new-token"},
    )

    credentials = service.credentials_for(
        company_id=alpha["id"], channel="messenger"
    )
    assert credentials["access_token"] == "new-token"


def test_update_without_a_token_keeps_the_stored_one(service, alpha):
    """Renaming a connection must not silently wipe its credentials."""
    account = _connect(service, alpha, token="keep-me")

    service.update_account(
        company_id=alpha["id"],
        account_id=account["id"],
        values={"name": "Renamed Page"},
    )

    credentials = service.credentials_for(
        company_id=alpha["id"], channel="messenger"
    )
    assert credentials["access_token"] == "keep-me"


def test_a_company_cannot_update_another_companys_account(service, alpha, beta):
    """Account ids are guessable, so ownership is enforced on every write."""
    from backend.services.channel_account_service import ChannelAccountError

    account = _connect(service, alpha, page_id="PAGE_ALPHA")

    with pytest.raises(ChannelAccountError):
        service.update_account(
            company_id=beta["id"],
            account_id=account["id"],
            values={"name": "Hijacked"},
        )


def test_a_company_cannot_delete_another_companys_account(service, alpha, beta):
    """Same ownership rule on delete, which would otherwise cut off a
    competitor's messaging entirely."""
    account = _connect(service, alpha, page_id="PAGE_ALPHA")

    assert service.delete_account(beta["id"], account["id"]) is False
    assert service.get_account(alpha["id"], account["id"]) is not None


def test_disabled_account_is_not_used_for_sending(service, alpha):
    """Disabling a connection has to actually stop outbound traffic on it."""
    account = _connect(service, alpha, token="alpha-token")

    service.update_account(
        company_id=alpha["id"],
        account_id=account["id"],
        values={"status": "disabled"},
    )

    assert service.credentials_for(company_id=alpha["id"], channel="messenger") is None
