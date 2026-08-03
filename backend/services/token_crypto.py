"""Symmetric encryption for channel access tokens at rest.

Backed by Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography`
package, keyed by config.TOKEN_ENCRYPTION_KEY. This is what makes the
`channel_accounts.access_token_encrypted` column name actually true --
previously nothing in the codebase encrypted or decrypted anything.
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from config.settings import config

logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet:
    key = config.TOKEN_ENCRYPTION_KEY
    if isinstance(key, str):
        key = key.encode("utf-8")
    return Fernet(key)


def encrypt_token(plaintext: str) -> str:
    """Encrypt a plaintext access token for storage.

    Raises if the configured TOKEN_ENCRYPTION_KEY is not a valid Fernet
    key -- a misconfigured encryption key should fail loudly at the point
    tokens are written, not silently store plaintext or garbage.
    """
    fernet = _get_fernet()
    token_bytes = plaintext.encode("utf-8")
    return fernet.encrypt(token_bytes).decode("utf-8")


def decrypt_token(ciphertext: str | None) -> str | None:
    """Decrypt a stored token, returning None on any failure.

    Failures (key rotated, corrupt data, wrong key, empty/None input) are
    logged and swallowed rather than raised -- callers should treat a
    None return as "no usable token" and fall back accordingly, not crash
    an outbound send path.
    """
    if not ciphertext:
        return None

    try:
        fernet = _get_fernet()
        decrypted = fernet.decrypt(ciphertext.encode("utf-8"))
        return decrypted.decode("utf-8")
    except InvalidToken:
        logger.warning(
            "token_crypto: failed to decrypt token (invalid token or "
            "rotated/mismatched TOKEN_ENCRYPTION_KEY)."
        )
        return None
    except Exception:
        logger.exception("token_crypto: unexpected error decrypting token.")
        return None
