import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config.settings import config


def _derive_fernet_key() -> bytes:
    """Fernet needs a 32-byte urlsafe-base64 key. Derive one from
    JWT_SECRET so there's no extra config value to set up — same secret,
    different purpose, standard key-derivation (SHA-256 then base64)."""
    secret = (config.JWT_SECRET or "tzone-fallback-secret").encode("utf-8")
    digest = hashlib.sha256(secret).digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_derive_fernet_key())


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise ValueError("Could not decrypt this credential — it may have been encrypted with a different JWT_SECRET.")
