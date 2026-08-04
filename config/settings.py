import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=True)


@dataclass
class AppConfig:
    APP_NAME: str = "T-ZONE Platform API"
    VERSION: str = "3.0.0"

    APP_ENV: str = os.getenv("APP_ENV", "development")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    DEFAULT_LANGUAGE: str = os.getenv("DEFAULT_LANGUAGE", "ar")
    COMPANY_NAME: str = os.getenv("COMPANY_NAME", "T-ZONE")

    DATABASE_PATH: str = os.getenv(
        "DATABASE_PATH",
        str(BASE_DIR / "database" / "tzone.db"),
    )

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'database' / 'tzone.db'}",
    )

    JWT_SECRET: str = os.getenv(
        "JWT_SECRET",
        "change-this-before-production",
    )

    JWT_ALGORITHM: str = os.getenv(
        "JWT_ALGORITHM",
        "HS256",
    )

    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440")
    )

    DEFAULT_WORKSPACE_ID: int = int(
        os.getenv("DEFAULT_WORKSPACE_ID", "1")
    )

    DEFAULT_COMPANY_ID: int = int(
        os.getenv("DEFAULT_COMPANY_ID", "1")
    )

    DEFAULT_BRANCH_ID: int = int(
        os.getenv("DEFAULT_BRANCH_ID", "1")
    )

    TELEGRAM_BOT_TOKEN: str = os.getenv(
        "TELEGRAM_BOT_TOKEN",
        "",
    )

    WHATSAPP_VERIFY_TOKEN: str = os.getenv(
        "WHATSAPP_VERIFY_TOKEN",
        "tzone_verify_token",
    )

    WHATSAPP_ACCESS_TOKEN: str = os.getenv(
        "WHATSAPP_ACCESS_TOKEN",
        "",
    )

    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv(
        "WHATSAPP_PHONE_NUMBER_ID",
        "",
    )

    WHATSAPP_API_VERSION: str = os.getenv(
        "WHATSAPP_API_VERSION",
        "v21.0",
    )

    META_VERIFY_TOKEN: str = os.getenv(
        "META_VERIFY_TOKEN",
        "tzone_meta_verify_token",
    )

    META_PAGE_ACCESS_TOKEN: str = os.getenv(
        "META_PAGE_ACCESS_TOKEN",
        "",
    )

    # Used to verify the X-Hub-Signature-256 header on incoming webhook
    # POSTs (Messenger/Instagram via channels/meta and WhatsApp Cloud API
    # via channels/whatsapp -- both live under the same Meta developer
    # app in a typical setup, so they share this one secret). Found in
    # the Meta App Dashboard under App Settings > Basic > App Secret.
    FACEBOOK_APP_SECRET: str = os.getenv(
        "FACEBOOK_APP_SECRET",
        "",
    )

    META_API_VERSION: str = os.getenv(
        "META_API_VERSION",
        "v21.0",
    )

    FACEBOOK_PAGE_ID: str = os.getenv(
        "FACEBOOK_PAGE_ID",
        "",
    )

    INSTAGRAM_BUSINESS_ID: str = os.getenv(
        "INSTAGRAM_BUSINESS_ID",
        "",
    )

    FACEBOOK_APP_ID: str = os.getenv(
        "FACEBOOK_APP_ID",
        "",
    )

    FACEBOOK_APP_SECRET: str = os.getenv(
        "FACEBOOK_APP_SECRET",
        "",
    )

    PUBLIC_APP_URL: str = os.getenv(
        "PUBLIC_APP_URL",
        "http://127.0.0.1:8000",
    )

    FRONTEND_URL: str = os.getenv(
        "FRONTEND_URL",
        "http://localhost:5173",
    )

    # INSECURE DEV DEFAULT ONLY -- production MUST override this with a
    # real Fernet key (generate one via `Fernet.generate_key().decode()`).
    # Using the default in production means anyone with repo/source access
    # can decrypt every stored channel access token.
    TOKEN_ENCRYPTION_KEY: str = os.getenv(
        "TOKEN_ENCRYPTION_KEY",
        "aW5zZWN1cmUtZGV2LW9ubHktZmVybmV0LWtleS0zMng=",
    )

    AI_ENABLED: bool = (
        os.getenv("AI_ENABLED", "true").lower() == "true"
    )

    OPENAI_API_KEY: str = os.getenv(
        "OPENAI_API_KEY",
        "",
    )

    OPENAI_MODEL: str = os.getenv(
        "OPENAI_MODEL",
        "gpt-4.1-mini",
    )

    OPENAI_API_URL: str = os.getenv(
        "OPENAI_API_URL",
        "https://api.openai.com/v1/responses",
    )

    UPLOAD_PATH: str = os.getenv(
        "UPLOAD_PATH",
        str(BASE_DIR / "data" / "uploads"),
    )

    MEDIA_PATH: str = os.getenv(
        "MEDIA_PATH",
        str(BASE_DIR / "data" / "media"),
    )

    SUPPORTED_LANGUAGES: list[str] = field(
        default_factory=lambda: [
            "en",
            "ar",
        ]
    )

    SUPPORTED_CHANNELS: list[str] = field(
        default_factory=lambda: [
            "telegram",
            "whatsapp",
            "messenger",
            "instagram",
            "website_chat",
        ]
    )

    BUSINESS_DEPARTMENTS: list[str] = field(
        default_factory=lambda: [
            "sales",
            "iptv",
            "maintenance",
            "accounting",
            "telecom",
            "orders",
            "information",
            "human_support",
            "unknown",
        ]
    )


config = AppConfig()