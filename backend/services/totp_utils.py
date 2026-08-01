"""Pure-stdlib RFC 6238 TOTP helpers (no third-party dependency).

Implements the standard 30-second time step, 6-digit, SHA1 authenticator
codes compatible with Google Authenticator, Authy, 1Password, etc.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


TIME_STEP_SECONDS = 30
CODE_DIGITS = 6


def generate_secret(length_bytes: int = 20) -> str:
    """Return a new random base32 secret (no padding), the standard TOTP key."""
    raw = secrets.token_bytes(length_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _normalize_secret(secret: str) -> bytes:
    cleaned = secret.strip().replace(" ", "").upper()
    # base32 requires the length to be a multiple of 8 (padding with '=').
    padding = (-len(cleaned)) % 8
    cleaned += "=" * padding
    return base64.b32decode(cleaned, casefold=True)


def _hotp(key: bytes, counter: int) -> str:
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** CODE_DIGITS)).zfill(CODE_DIGITS)


def generate(secret: str, at_time: float | None = None) -> str:
    """Return the current TOTP code for the given base32 secret."""
    if at_time is None:
        at_time = time.time()
    key = _normalize_secret(secret)
    counter = int(at_time // TIME_STEP_SECONDS)
    return _hotp(key, counter)


def verify(secret: str, code: str, window: int = 1, at_time: float | None = None) -> bool:
    """Verify a code, accepting the current 30s step +/- `window` steps
    to tolerate clock skew. Constant-time comparison per candidate."""
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != CODE_DIGITS:
        return False
    if at_time is None:
        at_time = time.time()
    try:
        key = _normalize_secret(secret)
    except Exception:
        return False
    counter = int(at_time // TIME_STEP_SECONDS)
    for offset in range(-window, window + 1):
        candidate = _hotp(key, counter + offset)
        if hmac.compare_digest(candidate, code):
            return True
    return False


def provisioning_uri(secret: str, account_name: str, issuer: str = "T-ZONE") -> str:
    """Return an otpauth:// URI for authenticator apps (QR or manual entry)."""
    label = quote(f"{issuer}:{account_name}")
    params = (
        f"secret={secret}"
        f"&issuer={quote(issuer)}"
        f"&algorithm=SHA1"
        f"&digits={CODE_DIGITS}"
        f"&period={TIME_STEP_SECONDS}"
    )
    return f"otpauth://totp/{label}?{params}"
