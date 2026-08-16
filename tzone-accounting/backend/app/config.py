"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("ACCOUNTING_DB_PATH", BASE_DIR / "data" / "accounting.db")
        )
    )
    jwt_secret: str = os.environ.get("ACCOUNTING_JWT_SECRET", "dev-only-insecure-secret")
    jwt_ttl_minutes: int = int(os.environ.get("ACCOUNTING_JWT_TTL_MINUTES", "720"))
    cors_origins: list[str] = field(
        default_factory=lambda: _split_csv(
            os.environ.get(
                "ACCOUNTING_CORS_ORIGINS",
                "http://127.0.0.1:5173,http://localhost:5173",
            )
        )
    )
    admin_username: str = os.environ.get("ACCOUNTING_ADMIN_USERNAME", "admin")
    admin_password: str = os.environ.get("ACCOUNTING_ADMIN_PASSWORD", "admin123")

    # Largest batch a client may push or pull in one request.
    max_batch: int = 500


def get_settings() -> Settings:
    """Built fresh so tests can point ACCOUNTING_DB_PATH at a temp file."""
    return Settings()
