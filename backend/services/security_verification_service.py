import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from backend.services.email_service import send_email
from database.database import db


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


CODE_TTL_MINUTES = 10
ELEVATED_TTL_MINUTES = 20
# A 6-digit code has only 1,000,000 combinations; with no attempt limit an
# authenticated attacker (e.g. one who stole a session but not the victim's
# inbox) could script through the keyspace well within the 10-minute TTL.
# Lock the code out after this many wrong guesses, forcing a fresh
# request_code() (a brand-new code + hash) instead of unlimited retries.
MAX_VERIFY_ATTEMPTS = 5


class SecurityVerificationError(Exception):
    pass


class SecurityVerificationService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS email_verifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    purpose TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS elevated_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    purpose TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS elevated_session_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    purpose TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(email_verifications)")}
            if "attempts" not in existing_columns:
                conn.execute("ALTER TABLE email_verifications ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            conn.commit()

    def request_code(self, *, user_id: int, email: str, purpose: str) -> tuple[bool, str]:
        """Generates a 6-digit code, emails it, and stores only its hash.
        Returns (sent, reason) — reason is empty on success, or a
        specific diagnostic message on failure."""
        code = f"{secrets.randbelow(1000000):06d}"
        now = utc_now()
        expires_at = (now + timedelta(minutes=CODE_TTL_MINUTES)).isoformat()

        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO email_verifications (user_id, purpose, code_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, purpose, _hash(code), expires_at, now.isoformat()),
            )
            conn.commit()

        return send_email(
            to_email=email,
            subject="Your T-ZONE verification code",
            body=(
                f"Your verification code is: {code}\n\n"
                f"It expires in {CODE_TTL_MINUTES} minutes. "
                f"If you didn't request this, you can ignore this email."
            ),
        )

    def verify_code(self, *, user_id: int, code: str, purpose: str) -> str:
        """Verifies the code and, if valid, issues an elevated-access
        token (plaintext returned once, only the hash is stored)."""
        now = utc_now()
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT id, code_hash, expires_at, attempts FROM email_verifications
                WHERE user_id = ? AND purpose = ? AND consumed_at IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, purpose),
            ).fetchone()

            if not row:
                raise SecurityVerificationError("No verification code was requested. Please request a new one.")
            if datetime.fromisoformat(row["expires_at"]) < now:
                raise SecurityVerificationError("This code has expired. Please request a new one.")
            # A 6-digit code is only 1,000,000 combinations; without an
            # attempt cap an authenticated attacker (e.g. one who stole a
            # session but not the victim's inbox) could script through the
            # keyspace inside the TTL. Count every wrong guess (even after
            # the cap) so a script that ignores the error can't keep probing
            # once locked out.
            if row["attempts"] >= MAX_VERIFY_ATTEMPTS:
                raise SecurityVerificationError("Too many incorrect attempts. Please request a new code.")
            if row["code_hash"] != _hash(code.strip()):
                conn.execute(
                    "UPDATE email_verifications SET attempts = attempts + 1 WHERE id = ?", (row["id"],),
                )
                conn.commit()
                raise SecurityVerificationError("Incorrect code.")

            conn.execute(
                "UPDATE email_verifications SET consumed_at = ? WHERE id = ?",
                (now.isoformat(), row["id"]),
            )

            token = secrets.token_urlsafe(32)
            expires_at = (now + timedelta(minutes=ELEVATED_TTL_MINUTES)).isoformat()
            conn.execute(
                """
                INSERT INTO elevated_sessions (user_id, purpose, token_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, purpose, _hash(token), expires_at, now.isoformat()),
            )
            conn.commit()

        return token

    def check_elevated(self, *, user_id: int, token: str, purpose: str) -> bool:
        if not token:
            return False
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT expires_at FROM elevated_sessions
                WHERE user_id = ? AND purpose = ? AND token_hash = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, purpose, _hash(token)),
            ).fetchone()
        if not row:
            return False
        return datetime.fromisoformat(row["expires_at"]) >= utc_now()

    def log_change(self, *, user_id: int, purpose: str, description: str) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO elevated_session_changes (user_id, purpose, description, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, purpose, description, utc_now().isoformat()),
            )
            conn.commit()

    def get_recent_changes(self, *, user_id: int, purpose: str, token: str) -> list[dict]:
        """Returns what changed during the current elevated session (since
        it started) — shown to the user as a summary when they leave."""
        with db.connect() as conn:
            session_row = conn.execute(
                """
                SELECT created_at FROM elevated_sessions
                WHERE user_id = ? AND purpose = ? AND token_hash = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, purpose, _hash(token)),
            ).fetchone()
            if not session_row:
                return []
            rows = conn.execute(
                """
                SELECT description, created_at FROM elevated_session_changes
                WHERE user_id = ? AND purpose = ? AND created_at >= ?
                ORDER BY created_at ASC
                """,
                (user_id, purpose, session_row["created_at"]),
            ).fetchall()
        return [dict(row) for row in rows]


security_verification_service = SecurityVerificationService()
