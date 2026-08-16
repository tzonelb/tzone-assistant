"""Tests for the guarantees the platform is sold on.

Each of these protects a defect that was live and reproducible before the
per-company database work: one company could read another's conversations, the
workspace code was decorative, and anyone on the internet could post a forged
customer message into the inbox.
"""

from __future__ import annotations

import pytest

from backend.security import keyring
from backend.security.keyring import (
    CorruptedKeyMaterial,
    InvalidWorkspaceCode,
)
from database.manager import DatabaseError, utc_now_iso


def _add_message(manager, company_id: int, body: str) -> None:
    now = utc_now_iso()

    with manager.tenant(company_id) as conn:
        conn.execute(
            """
            INSERT INTO conversations (
                company_id, channel, external_user_id, created_at, updated_at
            )
            VALUES (?, 'messenger', 'customer-1', ?, ?)
            """,
            (company_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO messages (
                company_id, conversation_id, channel, external_user_id,
                direction, body, created_at
            )
            VALUES (?, 1, 'messenger', 'customer-1', 'in', ?, ?)
            """,
            (company_id, body, now),
        )
        conn.commit()


# ----------------------------------------------------------------------
# Isolation
# ----------------------------------------------------------------------


def test_one_company_cannot_see_another_companys_messages(platform, alpha, beta):
    """A employee of one company could previously list and read every other
    company's conversations, because messages lived in one shared folder with no
    company dimension at all."""
    manager = platform["manager"]

    _add_message(manager, alpha["id"], "alpha private customer message")

    with manager.tenant(beta["id"]) as conn:
        visible = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]

    assert visible == 0

    with manager.tenant(alpha["id"]) as conn:
        own = conn.execute("SELECT body FROM messages").fetchall()

    assert [row["body"] for row in own] == ["alpha private customer message"]


def test_each_company_has_its_own_database_file(platform, alpha, beta):
    """Isolation has to be physical. Sharing one file would leave separation
    depending on every query remembering a WHERE clause."""
    assert alpha["path"] != beta["path"]
    assert alpha["path"].exists()
    assert beta["path"].exists()


def test_a_companys_key_cannot_open_another_companys_database(platform, alpha, beta):
    """Stops a leaked or misassigned key from widening into a full breach."""
    manager = platform["manager"]
    alpha_key = manager.company_key(alpha["id"])

    with pytest.raises(DatabaseError):
        manager._open(beta["path"], alpha_key)


def test_wrapped_keys_are_bound_to_their_company(platform, alpha, beta):
    """The wrapped key is bound through the AEAD's associated data, so moving a
    company_databases row between companies fails instead of granting access."""
    manager = platform["manager"]

    with manager.control() as conn:
        sealed = conn.execute(
            "SELECT key_sealed_master FROM company_databases WHERE company_id = ?",
            (alpha["id"],),
        ).fetchone()["key_sealed_master"]

    with pytest.raises(CorruptedKeyMaterial):
        keyring.unwrap_with_master(sealed, beta["id"], manager.master_key())


# ----------------------------------------------------------------------
# Encryption at rest
# ----------------------------------------------------------------------


def test_message_text_is_not_readable_on_disk(platform, alpha):
    """A stolen VPS disk or backup copy must not yield customer data."""
    secret = "card number 4111 1111 1111 1111"
    _add_message(platform["manager"], alpha["id"], secret)

    raw = alpha["path"].read_bytes()

    assert secret.encode() not in raw
    assert not raw.startswith(b"SQLite format 3")


def test_database_cannot_be_opened_without_the_key(platform, alpha):
    """Without the master key the files are inert, which is the whole point of
    holding the key outside the data directory."""
    import sqlite3

    with pytest.raises(sqlite3.DatabaseError):
        connection = sqlite3.connect(str(alpha["path"]))
        try:
            connection.execute("SELECT COUNT(*) FROM conversations").fetchone()
        finally:
            connection.close()


# ----------------------------------------------------------------------
# Workspace code
# ----------------------------------------------------------------------


def test_correct_workspace_code_unlocks_the_company(platform, alpha):
    """The code is verified by actually unsealing the key, not by comparison
    against a stored string."""
    assert platform["manager"].verify_workspace_code(
        alpha["id"], alpha["workspace_code"]
    )


def test_wrong_workspace_code_is_rejected(platform, alpha):
    """Before this, the workspace field on the login form was only a company
    lookup and any value that matched a company name was accepted."""
    assert not platform["manager"].verify_workspace_code(
        alpha["id"], "TZ-WRON-GCOD-EXXX"
    )


def test_another_companys_code_does_not_unlock_this_company(platform, alpha, beta):
    """Codes must not be transferable between tenants."""
    assert not platform["manager"].verify_workspace_code(
        alpha["id"], beta["workspace_code"]
    )


def test_workspace_code_ignores_case_and_dashes(platform, alpha):
    """Operators read these codes aloud and retype them; formatting must not be
    the difference between working and locked out."""
    messy = alpha["workspace_code"].lower().replace("-", " ")

    assert platform["manager"].verify_workspace_code(alpha["id"], messy)


def test_rotating_the_code_keeps_the_data_readable(platform, alpha):
    """Rotation re-wraps the key rather than re-encrypting the database, so a
    compromised code never means data loss or downtime."""
    manager = platform["manager"]
    _add_message(manager, alpha["id"], "survives rotation")

    new_code = keyring.generate_workspace_code()
    manager.rotate_workspace_code(alpha["id"], new_code)

    assert manager.verify_workspace_code(alpha["id"], new_code)
    assert not manager.verify_workspace_code(alpha["id"], alpha["workspace_code"])

    with manager.tenant(alpha["id"]) as conn:
        body = conn.execute("SELECT body FROM messages LIMIT 1").fetchone()["body"]

    assert body == "survives rotation"


def test_unwrapping_with_a_wrong_code_raises(platform, alpha):
    """A wrong code must fail closed at the crypto layer, never return a
    partially-derived key."""
    manager = platform["manager"]

    with manager.control() as conn:
        row = conn.execute(
            "SELECT key_sealed_code, code_salt FROM company_databases WHERE company_id = ?",
            (alpha["id"],),
        ).fetchone()

    with pytest.raises(InvalidWorkspaceCode):
        keyring.unwrap_with_code(
            row["key_sealed_code"],
            "TZ-XXXX-YYYY-ZZZZ",
            bytes.fromhex(row["code_salt"]),
            alpha["id"],
        )


# ----------------------------------------------------------------------
# Webhook routing and provisioning
# ----------------------------------------------------------------------


def test_unrouted_webhook_does_not_fall_back_to_a_company(platform):
    """Returning a default company here is what funnelled every tenant's
    customers into company 1."""
    resolved = platform["manager"].resolve_company_for_channel(
        channel="messenger", page_id="not-a-connected-page"
    )

    assert resolved is None


def test_webhook_routes_to_the_company_owning_the_page(platform, alpha, beta):
    """Each company's messages must land in that company's own inbox."""
    manager = platform["manager"]
    now = utc_now_iso()

    with manager.control() as conn:
        conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, channel, name, page_id, status, created_at, updated_at
            )
            VALUES (?, 'messenger', 'Alpha Page', 'PAGE_ALPHA', 'active', ?, ?)
            """,
            (alpha["id"], now, now),
        )
        conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, channel, name, page_id, status, created_at, updated_at
            )
            VALUES (?, 'messenger', 'Beta Page', 'PAGE_BETA', 'active', ?, ?)
            """,
            (beta["id"], now, now),
        )
        conn.commit()

    assert (
        manager.resolve_company_for_channel(channel="messenger", page_id="PAGE_ALPHA")
        == alpha["id"]
    )
    assert (
        manager.resolve_company_for_channel(channel="messenger", page_id="PAGE_BETA")
        == beta["id"]
    )


def test_default_company_is_none_when_several_exist(platform):
    """With more than one tenant there is no safe default, so the platform must
    refuse to guess rather than pick the lowest id."""
    assert platform["manager"].default_company_id() is None


def test_provisioning_twice_is_refused(platform, alpha):
    """Re-provisioning would generate a new key and leave the existing database
    permanently unreadable."""
    with pytest.raises(DatabaseError):
        platform["manager"].provision_company(
            company_id=alpha["id"], workspace_code="TZ-AAAA-BBBB-CCCC"
        )


# ----------------------------------------------------------------------
# Inbound routing picks the right company
# ----------------------------------------------------------------------


def test_an_inbound_event_never_routes_to_another_company_by_accident(platform, alpha, beta):
    """`resolve_company_for_channel` took a `channel` and did not use it.

    That mattered because of the last candidate it tries: a Messenger `page_id`
    is also matched against `external_account_id`, a free-form column, and
    `_assert_routing_id_is_free` only enforces uniqueness *per channel*. So two
    companies could legitimately hold the same string on different channels —
    and a customer's Messenger message would land in whichever row came back
    first.

    This is the isolation failure the whole encrypted-per-company design exists
    to prevent, arriving through the routing table rather than through a query.
    """
    from database.manager import utc_now_iso

    manager = platform["manager"]
    shared_identifier = "1234567890"
    now = utc_now_iso()

    with manager.control() as conn:
        # Alpha owns the Messenger page.
        conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, channel, name, page_id, external_account_id,
                status, created_at, updated_at
            )
            VALUES (?, 'messenger', 'Alpha Page', ?, ?, 'active', ?, ?)
            """,
            (alpha["id"], shared_identifier, shared_identifier, now, now),
        )
        # Beta owns a WhatsApp number that happens to carry the same string in
        # the free-form column. Permitted today: uniqueness is per channel.
        conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, channel, name, phone_number_id, external_account_id,
                status, created_at, updated_at
            )
            VALUES (?, 'whatsapp', 'Beta Number', '999', ?, 'active', ?, ?)
            """,
            (beta["id"], shared_identifier, now, now),
        )
        conn.commit()

    routed = manager.resolve_company_for_channel(
        channel="messenger", page_id=shared_identifier
    )

    assert routed == alpha["id"], "a Messenger event reached the wrong company"


def test_an_event_for_a_channel_nobody_connected_is_refused(platform, alpha):
    """Returning a company for an unconnected channel would deliver traffic to
    somebody who never asked for it."""
    from database.manager import utc_now_iso

    manager = platform["manager"]
    now = utc_now_iso()

    with manager.control() as conn:
        conn.execute(
            """
            INSERT INTO channel_accounts (
                company_id, channel, name, page_id, external_account_id,
                status, created_at, updated_at
            )
            VALUES (?, 'messenger', 'Alpha Page', '555', '555', 'active', ?, ?)
            """,
            (alpha["id"], now, now),
        )
        conn.commit()

    assert (
        manager.resolve_company_for_channel(channel="whatsapp", phone_number_id="555")
        is None
    )
    assert manager.resolve_company_for_channel(channel="", page_id="555") is None
