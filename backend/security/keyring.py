"""Envelope encryption for per-company database keys.

Every company owns a random 256-bit Data Encryption Key (DEK). That key is the
SQLCipher key for the company's own database file, so a company can never read
another company's data even if it reaches the file.

The DEK itself is never stored in the clear. It is stored wrapped twice:

    DEK
     |-- sealed with MASTER_KEY (from the environment)
     |     -> lets the server open the database with no human present, which is
     |        what keeps the bot answering customers at 3am.
     |
     `-- sealed with KDF(workspace_code)
           -> the workspace code an employee types at login. A wrong code fails
              to unseal, so the code is a real credential and not a label.

Both wraps are AES-256-GCM and are bound to the company id through the AAD, so a
wrapped key stolen from one company cannot be replayed against another.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MASTER_KEY_ENV = "TZONE_MASTER_KEY"

KEY_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 16
KDF_ITERATIONS = 600_000

# Excludes 0/O/1/I/L so a code can be read off a screen and typed without doubt.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_GROUPS = 3
_CODE_GROUP_SIZE = 4


class KeyringError(RuntimeError):
    """Base class for every key handling failure."""


class MasterKeyMissing(KeyringError):
    """The server master key is absent or malformed."""


class InvalidWorkspaceCode(KeyringError):
    """The supplied workspace code does not unseal the company key."""


class CorruptedKeyMaterial(KeyringError):
    """Stored key material failed authentication against the master key."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


def normalize_workspace_code(workspace_code: str) -> str:
    """Fold a typed code to its canonical form.

    Dashes, spaces and casing are presentation only, so "tz-a1b2 c3d4" and
    "TZA1B2C3D4" unlock the same company.
    """
    cleaned = str(workspace_code or "")
    for noise in (" ", "-", "_", "\t"):
        cleaned = cleaned.replace(noise, "")
    return cleaned.strip().upper()


def generate_master_key() -> str:
    """Produce a new server master key, base64 encoded for the environment."""
    return _b64encode(secrets.token_bytes(KEY_BYTES))


def generate_company_key() -> bytes:
    """Produce a new per-company database encryption key."""
    return secrets.token_bytes(KEY_BYTES)


def generate_salt() -> bytes:
    """Produce a per-company salt for the workspace code KDF."""
    return secrets.token_bytes(SALT_BYTES)


def generate_workspace_code() -> str:
    """Produce a human-typable workspace code such as ``TZ-A1B2-C3D4-E5F6``."""
    groups = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUP_SIZE))
        for _ in range(_CODE_GROUPS)
    ]
    return "TZ-" + "-".join(groups)


def load_master_key() -> bytes:
    """Read and validate the server master key from the environment."""
    raw_value = os.getenv(MASTER_KEY_ENV, "").strip()

    if not raw_value:
        raise MasterKeyMissing(
            f"{MASTER_KEY_ENV} is not set. Generate one with "
            "`python -m tools.manage_platform generate-master-key` and store it "
            "in the environment before starting the server."
        )

    try:
        master_key = _b64decode(raw_value)
    except (ValueError, TypeError) as exc:
        raise MasterKeyMissing(
            f"{MASTER_KEY_ENV} is not valid base64."
        ) from exc

    if len(master_key) != KEY_BYTES:
        raise MasterKeyMissing(
            f"{MASTER_KEY_ENV} must decode to {KEY_BYTES} bytes, "
            f"got {len(master_key)}."
        )

    return master_key


def _company_aad(company_id: int) -> bytes:
    """Bind sealed key material to one company so it cannot be replayed."""
    return f"tzone:company:{int(company_id)}".encode("utf-8")


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> str:
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return _b64encode(nonce + ciphertext)


def _unseal(key: bytes, sealed: str, aad: bytes) -> bytes:
    raw = _b64decode(sealed)

    if len(raw) <= NONCE_BYTES:
        raise InvalidTag("sealed blob is too short")

    nonce = raw[:NONCE_BYTES]
    ciphertext = raw[NONCE_BYTES:]

    return AESGCM(key).decrypt(nonce, ciphertext, aad)


def derive_code_key(workspace_code: str, salt: bytes) -> bytes:
    """Stretch a workspace code into a wrapping key."""
    normalized = normalize_workspace_code(workspace_code)

    if not normalized:
        raise InvalidWorkspaceCode("Workspace code is empty.")

    return hashlib.pbkdf2_hmac(
        "sha256",
        normalized.encode("utf-8"),
        salt,
        KDF_ITERATIONS,
        dklen=KEY_BYTES,
    )


def wrap_with_master(
    company_key: bytes,
    company_id: int,
    master_key: bytes | None = None,
) -> str:
    """Seal a company key so the server can open the database unattended."""
    key = master_key if master_key is not None else load_master_key()
    return _seal(key, company_key, _company_aad(company_id))


def unwrap_with_master(
    sealed_key: str,
    company_id: int,
    master_key: bytes | None = None,
) -> bytes:
    """Recover a company key using the server master key."""
    key = master_key if master_key is not None else load_master_key()

    try:
        return _unseal(key, sealed_key, _company_aad(company_id))
    except (InvalidTag, ValueError, TypeError) as exc:
        raise CorruptedKeyMaterial(
            f"Stored key material for company {company_id} could not be "
            "authenticated. The master key may be wrong, or the record was "
            "tampered with."
        ) from exc


def wrap_with_code(
    company_key: bytes,
    workspace_code: str,
    salt: bytes,
    company_id: int,
) -> str:
    """Seal a company key behind the workspace code an employee types."""
    code_key = derive_code_key(workspace_code, salt)
    return _seal(code_key, company_key, _company_aad(company_id))


def unwrap_with_code(
    sealed_key: str,
    workspace_code: str,
    salt: bytes,
    company_id: int,
) -> bytes:
    """Recover a company key from a typed workspace code.

    Raises :class:`InvalidWorkspaceCode` when the code is wrong. The GCM tag is
    what fails, so a wrong code is rejected without ever revealing the key.
    """
    code_key = derive_code_key(workspace_code, salt)

    try:
        return _unseal(code_key, sealed_key, _company_aad(company_id))
    except (InvalidTag, ValueError, TypeError) as exc:
        raise InvalidWorkspaceCode(
            "Workspace code is incorrect."
        ) from exc


def verify_workspace_code(
    sealed_key: str,
    workspace_code: str,
    salt: bytes,
    company_id: int,
) -> bool:
    """Return whether a typed workspace code unseals this company's key."""
    try:
        unwrap_with_code(sealed_key, workspace_code, salt, company_id)
        return True
    except InvalidWorkspaceCode:
        return False


def rewrap_with_new_code(
    sealed_by_master: str,
    company_id: int,
    new_workspace_code: str,
    master_key: bytes | None = None,
) -> tuple[str, bytes]:
    """Rotate the workspace code without changing the database key.

    Returns the freshly sealed blob and its new salt. The database file is
    untouched, so rotating a code never requires re-encrypting the data.
    """
    company_key = unwrap_with_master(sealed_by_master, company_id, master_key)
    salt = generate_salt()
    sealed = wrap_with_code(company_key, new_workspace_code, salt, company_id)
    return sealed, salt


def seal_secret(
    plaintext: str,
    company_key: bytes,
    company_id: int,
    context: str,
) -> str:
    """Seal a short secret — an access token, an app secret — for one company.

    Channel accounts live in the shared control database so an inbound webhook
    can be routed before the company is known, but the credentials on them
    belong to one company. Sealing them under that company's database key means
    a dump of the control database yields no usable tokens.

    ``context`` distinguishes secrets on the same record (for example the access
    token from the verify token), so a sealed value cannot be moved from one
    field to another.
    """
    return _seal(
        company_key,
        str(plaintext).encode("utf-8"),
        f"{_company_aad(company_id).decode()}:{context}".encode("utf-8"),
    )


def unseal_secret(
    sealed: str,
    company_key: bytes,
    company_id: int,
    context: str,
) -> str:
    """Recover a secret sealed by :func:`seal_secret`."""
    try:
        raw = _unseal(
            company_key,
            sealed,
            f"{_company_aad(company_id).decode()}:{context}".encode("utf-8"),
        )
    except (InvalidTag, ValueError, TypeError) as exc:
        raise CorruptedKeyMaterial(
            f"A stored {context} for company {company_id} could not be "
            "authenticated. It was written with a different key, or the row "
            "was tampered with."
        ) from exc

    return raw.decode("utf-8")


def sqlcipher_key_literal(company_key: bytes) -> str:
    """Format a raw key for ``PRAGMA key``.

    The raw-hex form tells SQLCipher to use these bytes directly instead of
    running its own KDF over a passphrase, which is correct here because the key
    is already 256 bits of entropy from a CSPRNG.
    """
    if len(company_key) != KEY_BYTES:
        raise KeyringError(
            f"Database key must be {KEY_BYTES} bytes, got {len(company_key)}."
        )

    return f"x'{company_key.hex()}'"
