import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=True)


def _env_path(name: str, default: Path) -> Path:
    """Resolve a configurable path, always against the project root.

    Paths are resolved from ``BASE_DIR`` rather than the working directory so
    the platform behaves identically whether it is started from the project
    root, from systemd, or from a container with a different workdir.
    """
    raw = os.getenv(name, "").strip()

    if not raw:
        return default

    candidate = Path(raw).expanduser()

    return candidate if candidate.is_absolute() else (BASE_DIR / candidate)


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()

    if not raw:
        return list(default)

    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()

    if not raw:
        return default

    return raw in {"1", "true", "yes", "on"}


@dataclass
class AppConfig:
    APP_NAME: str = "T-ZONE Platform API"
    VERSION: str = "4.0.0"

    APP_ENV: str = os.getenv("APP_ENV", "development")
    DEBUG: bool = _env_bool("DEBUG", False)
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    DEFAULT_LANGUAGE: str = os.getenv("DEFAULT_LANGUAGE", "ar")
    COMPANY_NAME: str = os.getenv("COMPANY_NAME", "T-ZONE")

    # ------------------------------------------------------------------
    # Paths — all absolute, all derived from the project root
    # ------------------------------------------------------------------
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = field(
        default_factory=lambda: _env_path("DATA_DIR", BASE_DIR / "data")
    )
    LOG_DIR: Path = field(
        default_factory=lambda: _env_path("LOG_DIR", BASE_DIR / "logs")
    )
    FEATURES_DIR: Path = field(
        default_factory=lambda: _env_path("FEATURES_DIR", BASE_DIR / "features")
    )
    CONFIG_DIR: Path = field(
        default_factory=lambda: _env_path("CONFIG_DIR", BASE_DIR / "config")
    )
    UPLOAD_DIR: Path = field(
        default_factory=lambda: _env_path("UPLOAD_DIR", BASE_DIR / "data" / "uploads")
    )
    MEDIA_DIR: Path = field(
        default_factory=lambda: _env_path("MEDIA_DIR", BASE_DIR / "data" / "media")
    )

    # ------------------------------------------------------------------
    # HTTP surface
    # ------------------------------------------------------------------
    CORS_ORIGINS: list[str] = field(
        default_factory=lambda: _env_list(
            "CORS_ORIGINS",
            ["http://localhost:5173", "http://127.0.0.1:5173"],
        )
    )
    # Interactive API docs map the entire attack surface, so they stay off
    # unless explicitly enabled.
    ENABLE_DOCS: bool = _env_bool(
        "ENABLE_DOCS",
        os.getenv("APP_ENV", "development") != "production",
    )

    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720")
    )
    LOGIN_MAX_ATTEMPTS: int = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_LOCKOUT_MINUTES: int = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "30"))

    # Failures from one address before that address is throttled — deliberately
    # far above LOGIN_MAX_ATTEMPTS, and this gap is load-bearing.
    #
    # An address is not an account. A whole office reaches this platform from
    # one address, so throttling it at five failures would mean one colleague
    # fumbling their password locks out everyone sitting near them. That is the
    # collateral damage the account lock was redesigned to avoid, and setting
    # the two thresholds equal reintroduces it through the other door.
    #
    # Twenty is above what a floor of people mistyping produces in half an hour
    # and far below what someone working through a password list produces.
    LOGIN_ADDRESS_MAX_ATTEMPTS: int = int(
        os.getenv("LOGIN_ADDRESS_MAX_ATTEMPTS", "20")
    )

    # How long a password-reset link stays usable. Short on purpose: the link is
    # a bearer credential for one account, and it arrives in a mailbox this
    # platform does not control.
    PASSWORD_RESET_TTL_MINUTES: int = int(
        os.getenv("PASSWORD_RESET_TTL_MINUTES", "30")
    )

    # ------------------------------------------------------------------
    # Outbound email
    # ------------------------------------------------------------------
    # Used for password-reset links and security notices. There is no fallback
    # channel: if this is not configured, the endpoints that depend on it refuse
    # rather than report success for a message nobody will receive.
    #
    # `console` prints the message to the log instead of sending, which is what
    # development and the test suite use.
    EMAIL_BACKEND: str = os.getenv("EMAIL_BACKEND", "console").strip().lower()

    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "")
    SMTP_STARTTLS: bool = _env_bool("SMTP_STARTTLS", True)
    SMTP_TIMEOUT_SECONDS: int = int(os.getenv("SMTP_TIMEOUT_SECONDS", "20"))

    # The origin the browser reaches this platform on. Password-reset links are
    # built from it, so a wrong value produces links that go nowhere.
    APP_PUBLIC_URL: str = os.getenv("APP_PUBLIC_URL", "http://localhost:5173")

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------
    # App secrets sign inbound webhooks. Without them any host on the internet
    # can inject customer messages, so an unset secret is treated as a hard
    # rejection rather than a skipped check.
    META_APP_SECRET: str = os.getenv("META_APP_SECRET", "")
    WHATSAPP_APP_SECRET: str = os.getenv(
        "WHATSAPP_APP_SECRET",
        os.getenv("META_APP_SECRET", ""),
    )
    ALLOW_UNSIGNED_WEBHOOKS: bool = _env_bool("ALLOW_UNSIGNED_WEBHOOKS", False)

    # The previous app secret, kept live during a rotation. Meta signs with
    # whichever secret was current when it queued the delivery, so without an
    # overlap every rotation drops the events already in flight.
    META_APP_SECRET_PREVIOUS: str = os.getenv("META_APP_SECRET_PREVIOUS", "")
    WHATSAPP_APP_SECRET_PREVIOUS: str = os.getenv(
        "WHATSAPP_APP_SECRET_PREVIOUS",
        os.getenv("META_APP_SECRET_PREVIOUS", ""),
    )

    # ------------------------------------------------------------------
    # Hard platform ceilings
    # ------------------------------------------------------------------
    # One set of numbers for everybody, and deliberately not for sale. These
    # protect the process itself, so no plan may raise them — a subscription
    # that could buy a value which stalls the server is not a subscription
    # tier, it is a defect. Commercial per-plan quotas sit *inside* these.

    # Bigger than any genuine batch and far smaller than nginx's 25 MB, which
    # has to cover customer media uploads too. Applied in the application so a
    # deployment that never sees nginx is still bounded.
    WEBHOOK_MAX_BODY_BYTES: int = int(
        os.getenv("WEBHOOK_MAX_BODY_BYTES", str(5 * 1024 * 1024))
    )

    # Meta batches deliveries in tens. A single signed body was otherwise free
    # to carry hundreds of thousands of events, each costing seven database
    # writes and possibly an outbound Graph call.
    WEBHOOK_MAX_EVENTS: int = int(os.getenv("WEBHOOK_MAX_EVENTS", "1000"))

    # A pending batch holds the messages one customer sent while the assistant
    # waited for them to finish typing. Past this the batch is delivered as it
    # stands rather than growing without limit.
    PENDING_REPLY_MAX_MESSAGES: int = int(
        os.getenv("PENDING_REPLY_MAX_MESSAGES", "50")
    )

    # Every new message pushes the delivery time out, so a sustained flood at
    # one customer kept its batch permanently deferred. This is the ceiling on
    # that deferral: past it the batch goes regardless of new arrivals.
    PENDING_REPLY_MAX_DEFERRAL_SECONDS: int = int(
        os.getenv("PENDING_REPLY_MAX_DEFERRAL_SECONDS", "300")
    )

    # The customer-profile cache had no bound at all, so distinct sender ids
    # grew it until the process ran out of memory.
    PROFILE_CACHE_MAX_ENTRIES: int = int(
        os.getenv("PROFILE_CACHE_MAX_ENTRIES", "10000")
    )

    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_API_VERSION: str = os.getenv("WHATSAPP_API_VERSION", "v21.0")

    META_VERIFY_TOKEN: str = os.getenv("META_VERIFY_TOKEN", "")
    META_PAGE_ACCESS_TOKEN: str = os.getenv("META_PAGE_ACCESS_TOKEN", "")
    META_INSTAGRAM_ACCESS_TOKEN: str = os.getenv("META_INSTAGRAM_ACCESS_TOKEN", "")
    META_API_VERSION: str = os.getenv("META_API_VERSION", "v21.0")

    FACEBOOK_PAGE_ID: str = os.getenv("FACEBOOK_PAGE_ID", "")
    INSTAGRAM_BUSINESS_ID: str = os.getenv("INSTAGRAM_BUSINESS_ID", "")

    # ------------------------------------------------------------------
    # Assistant
    # ------------------------------------------------------------------
    AI_ENABLED: bool = _env_bool("AI_ENABLED", True)
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    OPENAI_API_URL: str = os.getenv(
        "OPENAI_API_URL",
        "https://api.openai.com/v1/responses",
    )
    OPENAI_TIMEOUT_SECONDS: int = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "40"))

    SUPPORTED_LANGUAGES: list[str] = field(
        default_factory=lambda: ["en", "ar"]
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

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() == "production"


config = AppConfig()
