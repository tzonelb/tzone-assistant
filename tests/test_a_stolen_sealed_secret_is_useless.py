"""A sealed credential must be worthless anywhere but where it was sealed.

Channel accounts live in the **shared** control database, because an inbound
webhook has to be routed to a company before anybody knows which company it is.
That means one company's page token sits in the same table as everybody
else's — and the whole point of sealing them is that the table is not where the
value is.

Three attacks, in order of how much the attacker already has:

1. **A dump of the control database.** Every sealed column, no keys. This is
   the backup that walks out on a laptop, the replica somebody left readable,
   the disk that goes back to the hosting company.
2. **A company that copies another company's sealed blob into its own row.**
   The strongest realistic attack: the attacker is a paying customer with a
   legitimate workspace and API access, and the blob is a valid string from a
   table the platform itself reads. Nothing about it is malformed.
3. **A company that moves a blob between fields on its own row** — the verify
   token into the access token, say — hoping one field is checked more loosely
   than another.

All three must fail, and the second is the one that would be worst: unsealing
another company's page token means sending messages to that company's customers
as that company.
"""

from __future__ import annotations

import pytest


CONTEXTS = ("access_token", "app_secret", "verify_token", "bot_token")


@pytest.fixture()
def sealed(platform, alpha):
    """A real secret, sealed for Alpha the way the platform seals it."""
    from backend.security import keyring

    manager = platform["manager"]

    return {
        context: keyring.seal_secret(
            f"SECRET-VALUE-{context}",
            manager.company_key(alpha["id"]),
            alpha["id"],
            context,
        )
        for context in CONTEXTS
    }


def _unseal(platform, company_id, blob, context):
    from backend.security import keyring

    return keyring.unseal_secret(
        blob, platform["manager"].company_key(company_id), company_id, context
    )


# ------------------------------------------------------- the honest path first


@pytest.mark.parametrize("context", CONTEXTS)
def test_the_owner_can_read_its_own_secret(platform, alpha, sealed, context):
    """The control. Every refusal below is meaningless if sealing simply never
    works."""
    assert (
        _unseal(platform, alpha["id"], sealed[context], context)
        == f"SECRET-VALUE-{context}"
    )


# ----------------------------------------------------------- attack 1: a dump


def test_the_sealed_blob_does_not_contain_the_secret(platform, alpha, sealed):
    """A dump of the control database yields no usable credential.

    Checked as a substring rather than by trying to decrypt, because the
    failure this catches is the sloppy one — a "sealed" column that is base64,
    or a prefix, or the value with a wrapper around it.
    """
    for context, blob in sealed.items():
        assert f"SECRET-VALUE-{context}" not in blob, (
            f"the {context} blob contains the secret in the clear: {blob!r}"
        )
        assert "SECRET-VALUE" not in blob


def test_a_dump_without_the_key_cannot_be_opened(platform, alpha, beta, sealed):
    """The same blob, opened with a key that really exists but is the wrong one.

    Beta's key rather than an invented one, on purpose: a made-up key of the
    right length would also fail, and would prove only that random bytes do not
    decrypt. Using a key the platform itself derives and uses proves the thing
    that matters — that holding *a* company key is not holding *the* company
    key.
    """
    from backend.security import keyring

    with pytest.raises(Exception):
        keyring.unseal_secret(
            sealed["access_token"],
            platform["manager"].company_key(beta["id"]),
            alpha["id"],
            "access_token",
        )


# ------------------------------------------- attack 2: another company's blob


@pytest.mark.parametrize("context", CONTEXTS)
def test_another_company_cannot_open_a_stolen_blob(
    platform, alpha, beta, sealed, context
):
    """The attack worth caring about.

    Beta is a paying customer with a real workspace. It copies Alpha's sealed
    page token — a perfectly valid string, out of a table the platform reads —
    into its own channel account row, and asks the platform to use it. If that
    worked, Beta would be messaging Alpha's customers as Alpha.
    """
    with pytest.raises(Exception) as refused:
        _unseal(platform, beta["id"], sealed[context], context)

    assert f"SECRET-VALUE-{context}" not in str(refused.value), (
        "the refusal message leaks the secret it refused to unseal"
    )


def test_the_blob_is_bound_to_the_company_and_not_only_to_the_key(
    platform, alpha, beta, sealed
):
    """Belt and braces, and the case a key-only design would miss.

    Unsealing with Alpha's *key* but Beta's *id* must still fail. If it did
    not, the binding would be the key alone — and every place that looks up a
    key by company id would be one wrong variable away from opening the wrong
    company's secret.
    """
    from backend.security import keyring

    with pytest.raises(Exception):
        keyring.unseal_secret(
            sealed["access_token"],
            platform["manager"].company_key(alpha["id"]),
            beta["id"],
            "access_token",
        )


# ------------------------------------------- attack 3: moving between fields


def test_a_secret_cannot_be_moved_between_fields_on_the_same_row(
    platform, alpha, sealed
):
    """The verify token pasted into the access token column.

    Same company, same key, same row — only the field is different. Without a
    per-field context this would open, and a company could promote its own
    low-value verify token into the field the sender reads.
    """
    for context in CONTEXTS:
        for other in CONTEXTS:
            if other == context:
                continue

            with pytest.raises(Exception):
                _unseal(platform, alpha["id"], sealed[context], other)


def test_a_tampered_blob_is_refused_rather_than_partly_read(
    platform, alpha, sealed
):
    """Flipping a character must fail the whole thing, not return a corrupted
    prefix — which is the difference between authenticated encryption and
    encryption."""
    blob = sealed["access_token"]
    tampered = blob[:-4] + ("AAAA" if blob[-4:] != "AAAA" else "BBBB")

    with pytest.raises(Exception):
        _unseal(platform, alpha["id"], tampered, "access_token")


def test_an_empty_or_junk_blob_is_refused_cleanly(platform, alpha):
    """What a caller gets for a column that was never written, or was written
    by something else. It must raise a nameable error rather than return
    something."""
    for junk in ("", "not-sealed-at-all", "AAAA", "x" * 200):
        with pytest.raises(Exception):
            _unseal(platform, alpha["id"], junk, "access_token")
