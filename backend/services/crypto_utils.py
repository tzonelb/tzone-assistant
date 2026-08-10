import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from config.settings import config

logger = logging.getLogger(__name__)

DEFAULT_JWT_SECRET = "change-this-before-production"


def _is_local_dev() -> bool:
    """Same standard this session applied to the WhatsApp bridge secret
    (channels/whatsapp_qr/service.py): don't trust APP_ENV alone (it
    defaults to "development"), so a real deploy that forgot to set
    APP_ENV=production would otherwise be treated as dev (fail-open)."""
    return (config.DEBUG or getattr(config, "APP_ENV", "") == "development") \
        and config.JWT_SECRET == DEFAULT_JWT_SECRET


def _derive_fernet_key() -> bytes:
    """Fernet needs a 32-byte urlsafe-base64 key. Derive one from
    JWT_SECRET so there's no extra config value to set up — same secret,
    different purpose, standard key-derivation (SHA-256 then base64).

    SECURITY: every encrypted channel credential in the database (Telegram
    bot tokens, WhatsApp/Messenger/Instagram Graph tokens, the Instagram
    direct-login session blob, Facebook cookie pairs) is only as strong as
    this key. Unlike WA_BRIDGE_SECRET (which refuses the shipped default in
    production), this derivation previously had NO runtime guard — a
    deployment that forgot to set JWT_SECRET would encrypt every one of
    those secrets with a key derivable from the PUBLIC literal string
    "change-this-before-production" (config/settings.py), decryptable by
    anyone with read access to the DB file using only this source code.
    Loudly warn (not hard-fail — a running app must not crash on import)
    the moment this module is loaded outside local dev with the default
    secret still in place, so it's impossible to miss in logs/startup.
    """
    if not _is_local_dev() and config.JWT_SECRET == DEFAULT_JWT_SECRET:
        logger.error(
            "SECURITY: JWT_SECRET is still the built-in default outside local "
            "development. Every encrypted channel credential in the database "
            "(Telegram/WhatsApp/Instagram tokens, session blobs, cookies) is "
            "decryptable by anyone with read access to the DB file using only "
            "public source code. Set a strong, unique JWT_SECRET in .env "
            "immediately — this also signs every auth session token."
        )
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
