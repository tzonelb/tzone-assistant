"""Connecting a Facebook Page, Instagram account or WhatsApp number to a company.

This is what makes the platform genuinely multi-company. Inbound routing looks a
message up by the account it arrived on, and outbound sending uses that
account's own token — so two companies on the same server answer their own
customers from their own pages.

Records live in the control database because a webhook must be routed before we
know which company it belongs to. The credentials on them are sealed under the
owning company's database key, so the control database holds no usable secret.
"""

from __future__ import annotations

import imaplib
import json
import logging
import secrets
import ssl
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.security import keyring
from backend.security.keyring import CorruptedKeyMaterial
from backend.services.business_department_service import business_department_service
from backend.services.plan_service import PlanLimitExceeded, plan_service
from database.manager import database_manager


logger = logging.getLogger(__name__)


SUPPORTED_CHANNELS = (
    "messenger", "instagram", "whatsapp", "telegram", "slack", "discord", "webchat",
    "email", "viber", "line", "sms", "google_chat",
)

# Which identifier each channel is routed by. Getting this wrong sends one
# company's customers to another, so it is declared once here.
#
# Telegram and Slack both route on `external_account_id` rather than a column
# of their own: a Telegram bot has exactly one identity (the prefix of its own
# token) and a Slack app has exactly one workspace (its `team_id`, read back
# from Slack itself with the token the operator pasted). Both are derived, not
# typed, for the same reason -- see `telegram_bot_id` and `slack_team_id`
# below -- and the existing unique index on `(channel, external_account_id)`
# already stops two companies claiming the same bot or the same workspace.
ROUTING_FIELD = {
    "messenger": "page_id",
    "instagram": "instagram_business_id",
    "whatsapp": "phone_number_id",
    "telegram": "external_account_id",
    "slack": "external_account_id",
    "discord": "external_account_id",
    "webchat": "external_account_id",
    # Email is the exception among these: every other channel on this column
    # derives or generates its routing value, so the operator never types the
    # thing that decides where a message lands. A mailbox has nothing to
    # derive it from -- there is no app to ask "which address is this" -- so
    # the operator types the address itself, and `verify_imap_login` below is
    # what stands in for the "ask the provider" step every other channel gets
    # for free.
    "email": "external_account_id",
    # Viber, back to the derived pattern: a Viber "public account" (bot) has
    # exactly one identity, read back from Viber's own `get_account_info`
    # with the token the operator pasted -- see `viber_account_id` below.
    "viber": "external_account_id",
    # LINE, the same derived pattern: a channel's bot has exactly one user
    # id, read back from LINE's own `bot/info` with the Channel Access Token
    # the operator pasted -- see `line_bot_user_id` below.
    "line": "external_account_id",
    # SMS is the second typed-not-derived exception, for the same reason
    # email is: the operator's Twilio phone number is not something any API
    # call can guess or generate, so they type it -- and `twilio_phone_number_sid`
    # below stands in for the "ask the provider" step by confirming that
    # number genuinely belongs to the account whose credentials came with it.
    "sms": "external_account_id",
    # Google Chat, back to the derived pattern, but derived from the
    # credential itself rather than an API call to a "who am I" endpoint --
    # Chat apps have no such endpoint. A Google Cloud service account's
    # `client_email` is globally unique (Google enforces that at creation),
    # so it stands in for a bot id exactly the way Viber's and LINE's do --
    # see `google_chat_parse_service_account` below.
    "google_chat": "external_account_id",
}


SECRET_FIELDS = {
    "access_token": "access_token_sealed",
    "verify_token": "verify_token_sealed",
    "app_secret": "app_secret_sealed",
}


class ChannelAccountError(RuntimeError):
    """A channel account could not be created or updated."""


def telegram_bot_id(token: str) -> str:
    """The bot's numeric id, read out of its own token.

    A Telegram bot token is `<bot_id>:<secret>`. Deriving the id rather than
    asking an operator to type it removes the one mistake that would matter
    here — a mistyped id routes another company's customers into this inbox, or
    silently receives nothing at all.

    Raises rather than guessing: a token this cannot parse is not a token.
    """
    candidate = str(token or "").strip()
    bot_id, separator, secret = candidate.partition(":")

    if not separator or not bot_id.isdigit() or not secret:
        raise ChannelAccountError(
            "That does not look like a Telegram bot token. BotFather issues "
            "them in the form 123456789:AA... — paste the whole line."
        )

    return bot_id


SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"
SLACK_TIMEOUT_SECONDS = 10


def slack_team_id(bot_token: str) -> str:
    """The workspace's id, read back from Slack with the bot token itself.

    A Slack bot token carries no workspace id the way a Telegram token carries
    its bot id, so there is nothing to parse locally -- it has to be asked of
    Slack's own `auth.test`, which every valid bot token can call for free and
    which fails immediately for a token that is wrong, revoked, or pasted from
    the wrong app. Deriving it here keeps the same guarantee `telegram_bot_id`
    gives: the operator never types the identifier that decides where a
    workspace's messages get routed, so there is no transcription that could
    misroute or collide with another company's.
    """
    token = str(bot_token or "").strip()

    if not token:
        raise ChannelAccountError("A Slack account needs its Bot User OAuth Token.")

    try:
        response = httpx.post(
            SLACK_AUTH_TEST_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=SLACK_TIMEOUT_SECONDS,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ChannelAccountError(
            "Could not reach Slack to verify that token. Please try again."
        ) from exc

    if not body.get("ok"):
        raise ChannelAccountError(
            "Slack rejected that token"
            + (f" ({body.get('error')})" if body.get("error") else "")
            + ". Paste the Bot User OAuth Token (starts with xoxb-) from your "
            "Slack app's OAuth & Permissions page."
        )

    team_id = str(body.get("team_id") or "").strip()

    if not team_id:
        raise ChannelAccountError(
            "Slack did not return a workspace id for that token."
        )

    return team_id


DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_TIMEOUT_SECONDS = 10


def discord_bot_id(bot_token: str) -> str:
    """The bot's own Discord user id, read back with the token itself.

    Same reasoning as `slack_team_id`: a Discord bot token carries no id to
    parse locally, so it has to be asked of Discord's own API. Each company
    creates its own bot application, so that bot's id is a single, stable
    identity for the whole workspace it will run in -- the same role a
    Telegram bot id or a Slack team id plays for their channels.
    """
    token = str(bot_token or "").strip()

    if not token:
        raise ChannelAccountError("A Discord account needs its bot token.")

    try:
        response = httpx.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers={"Authorization": f"Bot {token}"},
            timeout=DISCORD_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ChannelAccountError(
            "Could not reach Discord to verify that token. Please try again."
        ) from exc

    if response.status_code != 200:
        raise ChannelAccountError(
            "Discord rejected that token. Paste the bot token from your "
            "application's Bot page in the Discord Developer Portal."
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ChannelAccountError(
            "Discord did not return a usable response for that token."
        ) from exc

    bot_id = str(body.get("id") or "").strip()

    if not bot_id:
        raise ChannelAccountError("Discord did not return a bot id for that token.")

    return bot_id


VIBER_API_BASE = "https://chatapi.viber.com/pa"
VIBER_TIMEOUT_SECONDS = 10


def viber_account_id(bot_token: str) -> str:
    """The bot's own public-account id, read back from Viber itself.

    Same reasoning as `slack_team_id` and `discord_bot_id`: a Viber
    authentication token carries no id to parse locally, so it has to be
    asked of Viber's own `get_account_info`, which every valid token can call
    for free and which fails immediately for a token that is wrong or
    revoked. The id comes back in the form ``pa:<digits>`` -- Viber's own
    prefix for a public account, kept as-is rather than stripped, since it is
    exactly the value every other Viber API call already expects to see.
    """
    token = str(bot_token or "").strip()

    if not token:
        raise ChannelAccountError(
            "A Viber account needs its bot Authentication Token."
        )

    try:
        response = httpx.post(
            f"{VIBER_API_BASE}/get_account_info",
            headers={"X-Viber-Auth-Token": token},
            json={},
            timeout=VIBER_TIMEOUT_SECONDS,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ChannelAccountError(
            "Could not reach Viber to verify that token. Please try again."
        ) from exc

    if body.get("status") != 0:
        raise ChannelAccountError(
            "Viber rejected that token"
            + (
                f" ({body.get('status_message')})"
                if body.get("status_message")
                else ""
            )
            + ". Paste the Authentication Token from your bot's page on the "
            "Viber Admin Panel."
        )

    account_id = str(body.get("id") or "").strip()

    if not account_id:
        raise ChannelAccountError("Viber did not return an account id for that token.")

    return account_id


def register_viber_webhook(*, token: str, account_id: int) -> None:
    """Point this bot's webhook at this platform's own route.

    Unlike Telegram -- whose webhook secret is typed in because nothing here
    can call `setWebhook` without knowing the platform's own public URL at
    the time the operator connects -- Viber's registration needs nothing
    Telegram's didn't already have available, so there is no reason to make
    the operator do this by hand. Called once, right after the account row
    exists (so the URL can carry its id), by the route that creates or
    re-enables the account -- see `backend/api/routes/channels.py`.
    """
    from config.settings import config

    url = f"{config.APP_PUBLIC_URL}/webhook/viber/{int(account_id)}"

    try:
        response = httpx.post(
            f"{VIBER_API_BASE}/set_webhook",
            headers={"X-Viber-Auth-Token": token},
            json={"url": url, "event_types": ["message", "conversation_started"]},
            timeout=VIBER_TIMEOUT_SECONDS,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ChannelAccountError(
            "Could not register this bot's webhook with Viber. Please try "
            "again."
        ) from exc

    if body.get("status") != 0:
        raise ChannelAccountError(
            "Viber rejected the webhook registration"
            + (
                f" ({body.get('status_message')})"
                if body.get("status_message")
                else ""
            )
            + "."
        )


def unregister_viber_webhook(token: str) -> None:
    """Best-effort: stop Viber sending events for a disconnected account.

    Never raises -- called from delete/disable paths that must still succeed
    locally even when Viber's own API is unreachable. An account this
    platform no longer serves will simply be refused at the webhook route
    (see `channels/viber/webhook.py`) if Viber keeps delivering to it anyway.
    """
    try:
        httpx.post(
            f"{VIBER_API_BASE}/set_webhook",
            headers={"X-Viber-Auth-Token": token},
            json={"url": ""},
            timeout=VIBER_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning(
            "Could not unregister a Viber webhook; the token may already be "
            "invalid",
            exc_info=True,
        )


LINE_API_BASE = "https://api.line.me/v2/bot"
LINE_TIMEOUT_SECONDS = 10


def line_bot_user_id(access_token: str) -> str:
    """The bot's own user id, read back from LINE's own `bot/info`.

    Same reasoning as `viber_account_id`: a LINE Channel Access Token carries
    no id to parse locally, so it has to be asked of LINE itself. The id
    comes back in the form ``U<hex>`` -- the same shape a LINE customer's own
    user id has, since a bot is addressed the same way a person is.
    """
    token = str(access_token or "").strip()

    if not token:
        raise ChannelAccountError("A LINE account needs its Channel Access Token.")

    try:
        response = httpx.get(
            f"{LINE_API_BASE}/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=LINE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ChannelAccountError(
            "Could not reach LINE to verify that token. Please try again."
        ) from exc

    if response.status_code != 200:
        raise ChannelAccountError(
            "LINE rejected that token. Paste the Channel Access Token from "
            "your channel's Messaging API tab."
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ChannelAccountError(
            "LINE did not return a usable response for that token."
        ) from exc

    bot_user_id = str(body.get("userId") or "").strip()

    if not bot_user_id:
        raise ChannelAccountError("LINE did not return a bot id for that token.")

    return bot_user_id


def register_line_webhook(*, access_token: str, account_id: int) -> None:
    """Point this bot's webhook at this platform's own route.

    Same reasoning as `register_viber_webhook`: LINE's endpoint can be set
    with a plain REST call (`PUT .../channel/webhook/endpoint`), so there is
    no reason to make the operator paste a URL into the LINE Developers
    Console by hand. Called once, right after the account row exists.
    """
    from config.settings import config

    url = f"{config.APP_PUBLIC_URL}/webhook/line/{int(account_id)}"

    try:
        response = httpx.put(
            f"{LINE_API_BASE}/channel/webhook/endpoint",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"endpoint": url},
            timeout=LINE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ChannelAccountError(
            "Could not register this bot's webhook with LINE. Please try "
            "again."
        ) from exc

    if response.status_code != 200:
        raise ChannelAccountError(
            "LINE rejected the webhook registration. Please try again."
        )


TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
TWILIO_TIMEOUT_SECONDS = 10


def twilio_phone_number_sid(
    *, account_sid: str, auth_token: str, phone_number: str
) -> str:
    """Confirm a phone number genuinely belongs to this Twilio account, and
    return its own resource id (`PN...`).

    An operator's Twilio phone number cannot be derived the way a bot's id
    can -- there is no "which number is this account's" question with one
    answer, an account may hold several -- so it is typed in, and this is
    what stands in for every other channel's "ask the provider" step: a
    wrong number, or credentials that do not own it, fail here rather than
    being discovered the first time a customer texts it and nothing happens.
    The id this returns is not optional bookkeeping -- `register_sms_webhook`
    needs it, because Twilio's webhook is a property of the phone number
    resource, addressed by this id, not by the number's own digits.
    """
    try:
        response = httpx.get(
            f"{TWILIO_API_BASE}/Accounts/{account_sid}/IncomingPhoneNumbers.json",
            params={"PhoneNumber": phone_number},
            auth=(account_sid, auth_token),
            timeout=TWILIO_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ChannelAccountError(
            "Could not reach Twilio to verify that phone number. Please try "
            "again."
        ) from exc

    if response.status_code == 401:
        raise ChannelAccountError(
            "Twilio rejected those credentials. Paste the Account SID and "
            "Auth Token from your Twilio Console."
        )

    if response.status_code != 200:
        raise ChannelAccountError(
            "Could not verify that phone number with Twilio. Please try "
            "again."
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ChannelAccountError(
            "Twilio did not return a usable response for that number."
        ) from exc

    numbers = body.get("incoming_phone_numbers") or []

    if not numbers:
        raise ChannelAccountError(
            "That phone number was not found on this Twilio account."
        )

    phone_sid = str(numbers[0].get("sid") or "").strip()

    if not phone_sid:
        raise ChannelAccountError(
            "Twilio did not return an id for that phone number."
        )

    return phone_sid


def register_sms_webhook(
    *, account_sid: str, auth_token: str, phone_number_sid: str, account_id: int
) -> None:
    """Point this phone number's SMS webhook at this platform's own route.

    Same reasoning as `register_viber_webhook` and `register_line_webhook`:
    Twilio's `SmsUrl` can be set with a plain REST call, so there is no
    reason to make the operator paste a URL into the Twilio Console by hand.
    """
    from config.settings import config

    url = f"{config.APP_PUBLIC_URL}/webhook/sms/{int(account_id)}"

    try:
        response = httpx.post(
            f"{TWILIO_API_BASE}/Accounts/{account_sid}"
            f"/IncomingPhoneNumbers/{phone_number_sid}.json",
            data={"SmsUrl": url},
            auth=(account_sid, auth_token),
            timeout=TWILIO_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ChannelAccountError(
            "Could not register this number's webhook with Twilio. Please "
            "try again."
        ) from exc

    if response.status_code != 200:
        raise ChannelAccountError(
            "Twilio rejected the webhook registration. Please try again."
        )


GOOGLE_CHAT_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_CHAT_SCOPE = "https://www.googleapis.com/auth/chat.bot"
GOOGLE_CHAT_API_BASE = "https://chat.googleapis.com/v1"
GOOGLE_CHAT_TIMEOUT_SECONDS = 10


def google_chat_parse_service_account(raw_json: str) -> dict[str, str]:
    """Pull the three fields this platform needs out of a pasted service
    account key file.

    A Google Cloud service account key is a JSON document, not a single
    token -- there is no shorter credential a Chat app can authenticate
    with. Asking the operator to transcribe individual fields out of it
    invites transcription errors on a value (`private_key`) that is
    thousands of characters of PEM; asking them to paste the whole file, as
    downloaded, is both the natural operator action and the one least
    likely to corrupt the key.
    """
    try:
        parsed = json.loads(raw_json)
    except (ValueError, TypeError) as exc:
        raise ChannelAccountError(
            "That doesn't look like a service account key file. Paste the "
            "whole JSON file Google Cloud downloaded for you."
        ) from exc

    if not isinstance(parsed, dict):
        raise ChannelAccountError(
            "That doesn't look like a service account key file. Paste the "
            "whole JSON file Google Cloud downloaded for you."
        )

    client_email = str(parsed.get("client_email") or "").strip()
    private_key = str(parsed.get("private_key") or "").strip()
    project_id = str(parsed.get("project_id") or "").strip()

    if not client_email or not private_key:
        raise ChannelAccountError(
            "That service account key is missing its client_email or "
            "private_key field. Paste the file exactly as Google Cloud "
            "downloaded it."
        )

    return {"client_email": client_email, "private_key": private_key, "project_id": project_id}


def google_chat_mint_access_token(*, client_email: str, private_key: str) -> str:
    """Prove a pasted service account key is real by using it to get a token.

    This stands in for every other channel's "ask the provider" step: a
    Chat app has no `bot/info`-style endpoint to confirm an identity
    against, so the confirmation is that Google's own token endpoint
    accepts a JWT self-signed with the pasted key and hands back a real
    access token for it -- a malformed or fabricated key fails here rather
    than the first time a customer messages the bot.

    The JWT-bearer grant (RFC 7523) is Google's documented flow for a
    service account to authenticate as itself: a JWT whose claims are
    `iss`/`sub` (the service account's own email), `scope`, `aud` (the
    token endpoint) and a short expiry, signed with the account's own
    private key, exchanged for an access token in one POST. No Google SDK
    needed -- PyJWT signs the RS256 assertion and httpx posts it.
    """
    import jwt as pyjwt

    now = int(datetime.now(timezone.utc).timestamp())

    try:
        assertion = pyjwt.encode(
            {
                "iss": client_email,
                "scope": GOOGLE_CHAT_SCOPE,
                "aud": GOOGLE_CHAT_TOKEN_URI,
                "iat": now,
                "exp": now + 3600,
            },
            private_key,
            algorithm="RS256",
        )
    except (ValueError, TypeError, pyjwt.PyJWTError) as exc:
        raise ChannelAccountError(
            "That private key is not a usable RSA key. Paste the service "
            "account key file exactly as Google Cloud downloaded it."
        ) from exc

    try:
        response = httpx.post(
            GOOGLE_CHAT_TOKEN_URI,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=GOOGLE_CHAT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ChannelAccountError(
            "Could not reach Google to verify that service account key. "
            "Please try again."
        ) from exc

    if response.status_code != 200:
        raise ChannelAccountError(
            "Google rejected that service account key. Confirm the Chat "
            "API is enabled on its project and paste the key file again."
        )

    try:
        access_token = str(response.json().get("access_token") or "")
    except ValueError as exc:
        raise ChannelAccountError(
            "Google did not return a usable response for that key."
        ) from exc

    if not access_token:
        raise ChannelAccountError(
            "Google did not return an access token for that key."
        )

    return access_token


def generate_webchat_widget_key() -> str:
    """A new public identifier for a website chat widget.

    Not a secret, unlike every other channel's routing id being derived from
    one: there is no bot or app behind a website widget to ask, and nothing
    to protect by hiding this value -- it is meant to sit in a company's own
    page source, inside the embed snippet, wherever their site publishes it.
    Random rather than sequential so one company's widget key gives no hint
    about another's, the same reasoning behind every other token this
    platform mints.
    """
    return f"wc_{secrets.token_urlsafe(24)}"


EMAIL_IMAP_TIMEOUT_SECONDS = 10

# The non-secret half of an email account's connection settings, packed into
# the generic `config_json` column (see `database/schema_control.py`) rather
# than the three sealed slots every other channel's secrets fit in -- a host
# name and a port are not credentials, and sealing them would cost every
# reader a decrypt for nothing gained. Also the whole surface an operator may
# send on create or update; `_pack_email_config` and the update path in
# `update_account` both read from this exact set.
EMAIL_CONFIG_FIELDS = (
    "imap_host", "imap_port", "imap_use_ssl",
    "smtp_host", "smtp_port", "smtp_use_starttls",
)


def verify_imap_login(
    *, address: str, password: str, host: str, port: int, use_ssl: bool
) -> None:
    """Prove a mailbox's credentials work before this platform starts polling it.

    Every other channel on this platform derives its routing id by asking the
    provider a question a wrong credential fails immediately --
    `slack_team_id`'s `auth.test`, `discord_bot_id`'s `/users/@me`. A mailbox
    has no such call, only a login, so this makes that login here, once, at
    connect time -- the alternative is a typo discovered only when
    `channels/email/poller.py` runs on its own schedule and silently reads
    nothing, ever.
    """
    try:
        if use_ssl:
            connection = imaplib.IMAP4_SSL(
                host, port, timeout=EMAIL_IMAP_TIMEOUT_SECONDS
            )
        else:
            connection = imaplib.IMAP4(host, port, timeout=EMAIL_IMAP_TIMEOUT_SECONDS)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise ChannelAccountError(
            f"Could not reach the IMAP server at {host}:{port}. {exc}"
        ) from exc

    try:
        connection.login(address, password)
    except imaplib.IMAP4.error as exc:
        raise ChannelAccountError(
            "The mailbox server rejected that address or password."
        ) from exc
    except (OSError, ssl.SSLError) as exc:
        raise ChannelAccountError(f"Could not verify that mailbox: {exc}") from exc
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001
            pass


def _pack_email_config(values: dict[str, Any]) -> dict[str, Any]:
    """The email account's non-secret settings, normalised and defaulted.

    Ports default the way the protocol does: 993 for IMAP-over-TLS, 143
    for plaintext IMAP, 587 for SMTP submission with STARTTLS. A company
    pointed at a mail server that genuinely uses something else still types
    the port explicitly, same as any other field here.
    """
    imap_use_ssl = bool(values.get("imap_use_ssl", True))
    smtp_use_starttls = bool(values.get("smtp_use_starttls", True))

    return {
        "imap_host": str(values.get("imap_host") or "").strip(),
        "imap_port": int(values.get("imap_port") or (993 if imap_use_ssl else 143)),
        "imap_use_ssl": imap_use_ssl,
        "smtp_host": str(values.get("smtp_host") or "").strip(),
        "smtp_port": int(values.get("smtp_port") or 587),
        "smtp_use_starttls": smtp_use_starttls,
    }


def _loads_config(raw: str | None) -> dict[str, Any]:
    """Parse `config_json` defensively. A malformed value reads as empty
    settings, never as a crash on a screen that only wants to display them."""
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}

    return parsed if isinstance(parsed, dict) else {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChannelAccountService:
    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_accounts(self, company_id: int) -> list[dict[str, Any]]:
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT channel_accounts.*, branches.name AS branch_name
                FROM channel_accounts
                LEFT JOIN branches
                       ON branches.id = channel_accounts.branch_id
                      AND branches.company_id = channel_accounts.company_id
                WHERE channel_accounts.company_id = ?
                ORDER BY channel_accounts.id ASC
                """,
                (int(company_id),),
            ).fetchall()

        return [self._public(row) for row in rows]

    def connected_channels(self, company_id: int) -> list[str]:
        """The channel types this company actually has switched on.

        The inbox used to build its channel filters from
        `SELECT DISTINCT channel FROM conversations` — that is, from message
        history rather than from what the company connected. Two wrong answers
        came out of it: a company that has just connected Instagram sees no
        Instagram filter until the first message arrives, and a company that
        once received a single test message on Messenger keeps a Messenger
        filter it never asked for and cannot get rid of.

        A company should see the channels it runs. Conversations belonging to a
        channel that was later disconnected are still reachable under "all" —
        disconnecting an account must not hide a customer's history.
        """
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT channel
                FROM channel_accounts
                WHERE company_id = ? AND status = 'active'
                ORDER BY channel
                """,
                (int(company_id),),
            ).fetchall()

        return [str(row["channel"]) for row in rows if row["channel"]]

    def get_account(self, company_id: int, account_id: int) -> dict[str, Any] | None:
        row = self._row(company_id, account_id)
        return self._public(row) if row else None

    def _row(self, company_id: int, account_id: int):
        with database_manager.control() as conn:
            return conn.execute(
                """
                SELECT * FROM channel_accounts
                WHERE id = ? AND company_id = ? LIMIT 1
                """,
                (int(account_id), int(company_id)),
            ).fetchone()

    def _public(self, row: Any) -> dict[str, Any]:
        """Shape a record for the browser.

        Sealed values never leave the server, not even encrypted. The screen
        only needs to know whether a credential is present.
        """
        data = dict(row)

        for field, column in SECRET_FIELDS.items():
            data[f"has_{field}"] = bool(data.pop(column, None))

        # The one channel with non-secret settings of its own -- see
        # `config_json` in `database/schema_control.py`. Parsed back into an
        # object for the screen rather than left as a JSON string, the same
        # shape every other structured field on this record already has.
        raw_config = data.pop("config_json", None)
        data["config"] = _loads_config(raw_config)

        return data

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def _validate(self, channel: str, values: dict[str, Any]) -> str:
        normalized = str(channel or "").strip().lower()

        if normalized not in SUPPORTED_CHANNELS:
            raise ChannelAccountError(
                f"Channel must be one of: {', '.join(SUPPORTED_CHANNELS)}."
            )

        routing_field = ROUTING_FIELD[normalized]

        # Telegram is the one channel whose routing id is not typed in: it is
        # the prefix of the bot token the operator is already pasting. Deriving
        # it removes the only transcription error that would matter — a wrong
        # id either receives nothing or, worse, claims an id another company
        # was routing on.
        if normalized == "telegram" and not values.get(routing_field):
            token = values.get("access_token")

            if not token:
                raise ChannelAccountError(
                    "A Telegram account needs the bot token from BotFather."
                )

            values[routing_field] = telegram_bot_id(token)

        # Slack, the same reasoning as Telegram just above: the routing id is
        # the workspace's team_id, asked of Slack itself rather than typed in.
        if normalized == "slack" and not values.get(routing_field):
            token = values.get("access_token")

            if not token:
                raise ChannelAccountError(
                    "A Slack account needs its Bot User OAuth Token."
                )

            values[routing_field] = slack_team_id(token)

        # Discord, the same reasoning again: the routing id is the bot's own
        # user id, asked of Discord itself rather than typed in.
        if normalized == "discord" and not values.get(routing_field):
            token = values.get("access_token")

            if not token:
                raise ChannelAccountError("A Discord account needs its bot token.")

            values[routing_field] = discord_bot_id(token)

        # Viber, the same reasoning as Slack and Discord just above: the
        # routing id is the bot's own public-account id, asked of Viber
        # itself rather than typed in.
        if normalized == "viber" and not values.get(routing_field):
            token = values.get("access_token")

            if not token:
                raise ChannelAccountError(
                    "A Viber account needs its bot Authentication Token."
                )

            values[routing_field] = viber_account_id(token)

        # LINE, the same reasoning again for the routing id -- but LINE is
        # also the second channel (after Slack) that needs two credentials:
        # a Channel Access Token to call the API with, and a separate
        # Channel Secret to verify inbound webhook signatures with. Neither
        # substitutes for the other, so both are required up front rather
        # than discovering the missing one the first time a delivery arrives
        # unverifiable.
        if normalized == "line":
            token = values.get("access_token")
            secret = values.get("verify_token")

            if not token:
                raise ChannelAccountError(
                    "A LINE account needs its Channel Access Token."
                )

            if not secret:
                raise ChannelAccountError("A LINE account needs its Channel Secret.")

            if not values.get(routing_field):
                values[routing_field] = line_bot_user_id(token)

        # Website live chat needs nothing from the operator at all: there is
        # no bot, no app, no account on another platform to connect. The
        # widget key is minted here, the one channel where the routing id is
        # generated rather than derived from something the operator supplied.
        if normalized == "webchat" and not values.get(routing_field):
            values[routing_field] = generate_webchat_widget_key()

        # Email, unlike every branch above: the routing value is typed by the
        # operator (the mailbox address), not derived or generated, so there
        # is nothing to fill in here. What this platform can still do is
        # prove the credentials actually open that mailbox before the account
        # is saved -- `verify_imap_login` -- and refuse a config with no
        # server to poll or send through at all.
        if normalized == "email":
            address = str(values.get(routing_field) or "").strip()
            password = values.get("access_token")
            imap_host = str(values.get("imap_host") or "").strip()
            smtp_host = str(values.get("smtp_host") or "").strip()

            if not address:
                raise ChannelAccountError(
                    "An email account needs the mailbox address customers "
                    "write to."
                )

            if not password:
                raise ChannelAccountError(
                    "An email account needs the mailbox password."
                )

            if not imap_host:
                raise ChannelAccountError(
                    "An email account needs its IMAP server address."
                )

            if not smtp_host:
                raise ChannelAccountError(
                    "An email account needs its SMTP server address."
                )

            config = _pack_email_config(values)

            verify_imap_login(
                address=address,
                password=password,
                host=config["imap_host"],
                port=config["imap_port"],
                use_ssl=config["imap_use_ssl"],
            )

            values[routing_field] = address

        # SMS, the same "typed, then verified" shape as email: a phone
        # number is not something to derive, so the operator types it, and
        # `twilio_phone_number_sid` stands in for the "ask the provider"
        # step -- proving both that the credentials are real and that this
        # account genuinely owns that number, and returning the id
        # `register_sms_webhook` needs.
        if normalized == "sms":
            phone_number = str(values.get(routing_field) or "").strip()
            auth_token = values.get("access_token")
            account_sid = str(values.get("account_sid") or "").strip()

            if not phone_number:
                raise ChannelAccountError(
                    "An SMS account needs the phone number customers text."
                )

            if not auth_token:
                raise ChannelAccountError("An SMS account needs its Twilio Auth Token.")

            if not account_sid:
                raise ChannelAccountError("An SMS account needs its Twilio Account SID.")

            values["_twilio_phone_number_sid"] = twilio_phone_number_sid(
                account_sid=account_sid,
                auth_token=auth_token,
                phone_number=phone_number,
            )
            values[routing_field] = phone_number

        # Google Chat, back to the derived pattern (see ROUTING_FIELD's
        # comment): the operator pastes a service account key file whole,
        # rather than any individual field of it, and the bot id this
        # account is routed by is read out of that file rather than typed.
        if normalized == "google_chat":
            raw_key = values.get("access_token")

            if not raw_key:
                raise ChannelAccountError(
                    "A Google Chat account needs its service account JSON key."
                )

            parsed_key = google_chat_parse_service_account(raw_key)
            google_chat_mint_access_token(
                client_email=parsed_key["client_email"],
                private_key=parsed_key["private_key"],
            )
            values["_google_chat_project_id"] = parsed_key["project_id"]
            values[routing_field] = parsed_key["client_email"]
            # Only the private key is sealed from here on -- `client_email`
            # is already stored, unsealed, as the routing identifier above,
            # and duplicating it in the sealed field would only mean two
            # places for the same value to drift.
            values["access_token"] = parsed_key["private_key"]

        if not values.get(routing_field):
            raise ChannelAccountError(
                f"A {normalized} account needs a {routing_field.replace('_', ' ')} "
                "so inbound messages can be routed to this company."
            )

        return normalized

    @staticmethod
    def _resolve_department_id(company_id: int, value: Any) -> int | None:
        """Check the department this account is being pointed at.

        The pointer lives in the control database and the department lives in
        the company's own, so nothing enforces the link for us. An id the
        company does not own is refused rather than stored: ids restart at 1 in
        every company's database, so an unchecked value would silently point
        one company's account at another company's section.

        ``None`` and empty clear the pointer, which is how a company stops
        routing an account by channel at all.
        """
        if value in (None, "", 0):
            return None

        try:
            department_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ChannelAccountError("Department id must be a number.") from exc

        department = business_department_service.get_department(
            company_id=int(company_id),
            department_id=department_id,
        )

        if not department:
            raise ChannelAccountError(
                "That department does not belong to this company."
            )

        return department_id

    @staticmethod
    def _resolve_branch_id(company_id: int, value: Any) -> int | None:
        """The same check for the branch, which never had one.

        `branch_id` sat in the plain-column list and went into the row exactly
        as it arrived. Both tables live in the control database, so unlike the
        department there is no cross-database excuse — the join in
        `list_accounts` simply matched on the branch id alone, with no company
        condition. Setting an account's `branch_id` to a number belonging to
        another company put that company's branch name in this company's
        channel list and on its dashboard. Small in volume, one name, and
        exactly the shape this platform must not have: a value from another
        company's row on this company's screen.

        Refused at the door rather than filtered at the read, because a stored
        pointer to someone else's row is wrong even while nothing displays it.
        """
        if value in (None, "", 0):
            return None

        try:
            branch_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ChannelAccountError("Branch id must be a number.") from exc

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT id FROM branches WHERE id = ? AND company_id = ?",
                (branch_id, int(company_id)),
            ).fetchone()

        if not row:
            raise ChannelAccountError(
                "That branch does not belong to this company."
            )

        return branch_id

    @staticmethod
    def _active_account_count(conn: Any, company_id: int) -> int:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total FROM channel_accounts
            WHERE company_id = ? AND status = 'active'
            """,
            (int(company_id),),
        ).fetchone()

        return int(row["total"]) if row else 0

    def _assert_channel_available(self, conn: Any, company_id: int) -> None:
        """Refuse a channel the plan does not have room for.

        The bundle limits **how many** accounts, never which kinds: a
        three-channel plan may be spent on three Instagram accounts, or on one
        each of three types. So this counts rows and nothing else.

        Counted on active accounts, and applied on both paths that can produce
        one — creating an account, and switching a disabled one back to active.
        Guarding only the create would leave a limit anybody could step around
        by disabling an account and re-enabling it.
        """
        try:
            plan_service.check(
                company_id,
                "max_channel_accounts",
                self._active_account_count(conn, company_id),
            )
        except PlanLimitExceeded as exc:
            raise ChannelAccountError(str(exc)) from exc

    def _assert_routing_id_is_free(
        self,
        conn,
        *,
        channel: str,
        routing_field: str,
        routing_value: str,
        exclude_id: int | None = None,
    ) -> None:
        """Refuse to point one account id at two companies.

        Without this, connecting a page already claimed elsewhere would make
        routing depend on row order — and silently deliver a company's customers
        to whichever record happened to be found first.
        """
        query = f"""
            SELECT id, company_id FROM channel_accounts
            WHERE {routing_field} = ? AND channel = ?
        """
        params: list[Any] = [str(routing_value), channel]

        if exclude_id is not None:
            query += " AND id != ?"
            params.append(int(exclude_id))

        existing = conn.execute(query + " LIMIT 1", params).fetchone()

        if existing:
            raise ChannelAccountError(
                "This account is already connected to another company on this "
                "platform. Disconnect it there first."
            )

    def create_account(
        self,
        *,
        company_id: int,
        channel: str,
        name: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        company_id = int(company_id)
        normalized_channel = self._validate(channel, values)
        routing_field = ROUTING_FIELD[normalized_channel]
        now = utc_now_iso()

        # Fails fast when the company has no provisioned database, rather than
        # writing a record whose secrets could never be sealed.
        company_key = database_manager.company_key(company_id)

        department_id = self._resolve_department_id(
            company_id, values.get("department_id")
        )
        branch_id = self._resolve_branch_id(company_id, values.get("branch_id"))

        with database_manager.control() as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                self._assert_routing_id_is_free(
                    conn,
                    channel=normalized_channel,
                    routing_field=routing_field,
                    routing_value=values[routing_field],
                )

                self._assert_channel_available(conn, company_id)

                cursor = conn.execute(
                    """
                    INSERT INTO channel_accounts (
                        company_id, branch_id, department_id, channel, name,
                        external_account_id, phone_number_id, page_id,
                        instagram_business_id, status,
                        ai_enabled, flow_enabled, voice_ai_enabled, image_ai_enabled,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        branch_id,
                        department_id,
                        normalized_channel,
                        str(name).strip(),
                        values.get("external_account_id") or values.get(routing_field),
                        values.get("phone_number_id"),
                        values.get("page_id"),
                        values.get("instagram_business_id"),
                        values.get("status", "active"),
                        1 if values.get("ai_enabled", True) else 0,
                        1 if values.get("flow_enabled", True) else 0,
                        1 if values.get("voice_ai_enabled", False) else 0,
                        1 if values.get("image_ai_enabled", False) else 0,
                        now,
                        now,
                    ),
                )

                account_id = int(cursor.lastrowid)

                if normalized_channel == "email":
                    conn.execute(
                        "UPDATE channel_accounts SET config_json = ? WHERE id = ?",
                        (json.dumps(_pack_email_config(values)), account_id),
                    )

                # SMS's own non-secret settings, the same generic slot email's
                # settings live in. `account_sid` is not sealed alongside the
                # Auth Token: Twilio's own security model treats it as a
                # public identifier, not a credential -- it already appears
                # on every inbound webhook Twilio itself sends.
                if normalized_channel == "sms":
                    conn.execute(
                        "UPDATE channel_accounts SET config_json = ? WHERE id = ?",
                        (
                            json.dumps(
                                {
                                    "account_sid": values.get("account_sid"),
                                    "phone_number_sid": values.get(
                                        "_twilio_phone_number_sid"
                                    ),
                                }
                            ),
                            account_id,
                        ),
                    )

                # Google Chat's own non-secret setting: the Cloud project the
                # service account belongs to, kept purely for the operator's
                # own reference on the account list -- nothing here reads it
                # back to authenticate or send.
                if normalized_channel == "google_chat":
                    conn.execute(
                        "UPDATE channel_accounts SET config_json = ? WHERE id = ?",
                        (
                            json.dumps(
                                {"project_id": values.get("_google_chat_project_id")}
                            ),
                            account_id,
                        ),
                    )

                for field, column in SECRET_FIELDS.items():
                    secret = values.get(field)

                    if secret:
                        conn.execute(
                            f"UPDATE channel_accounts SET {column} = ? WHERE id = ?",
                            (
                                keyring.seal_secret(
                                    secret, company_key, company_id, field
                                ),
                                account_id,
                            ),
                        )

                conn.commit()

            except Exception:
                conn.rollback()
                raise

        logger.info(
            "Connected %s account id=%s to company %s",
            normalized_channel,
            account_id,
            company_id,
        )

        return self.get_account(company_id, account_id)

    def update_account(
        self,
        *,
        company_id: int,
        account_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        company_id = int(company_id)
        account_id = int(account_id)

        existing = self._row(company_id, account_id)

        if not existing:
            raise ChannelAccountError("Channel account not found.")

        company_key = database_manager.company_key(company_id)
        channel = str(existing["channel"])
        routing_field = ROUTING_FIELD.get(channel, "external_account_id")

        plain_columns = (
            "name",
            "status",
            "external_account_id",
            "phone_number_id",
            "page_id",
            "instagram_business_id",
            "ai_enabled",
            "flow_enabled",
            "voice_ai_enabled",
            "image_ai_enabled",
        )

        assignments: list[str] = []
        params: list[Any] = []

        for column in plain_columns:
            if column not in values:
                continue

            value = values[column]

            if column.endswith("_enabled"):
                value = 1 if value else 0

            assignments.append(f"{column} = ?")
            params.append(value)

        # Validated rather than passed through with the other plain columns:
        # each names a row this company must own, and an id from another
        # company must be refused before it is written. `branch_id` was in the
        # plain list until this was written, which is how an account came to be
        # able to point at another company's branch.
        if "department_id" in values:
            assignments.append("department_id = ?")
            params.append(
                self._resolve_department_id(company_id, values["department_id"])
            )

        if "branch_id" in values:
            assignments.append("branch_id = ?")
            params.append(self._resolve_branch_id(company_id, values["branch_id"]))

        # Email's IMAP/SMTP settings, merged rather than replaced: an operator
        # changing just the password should not have to retype the mail
        # server too, so whatever this call did not send is kept from the
        # row that already exists.
        if channel == "email" and any(
            field in values for field in EMAIL_CONFIG_FIELDS
        ):
            merged = {**_loads_config(existing["config_json"]), **values}
            assignments.append("config_json = ?")
            params.append(json.dumps(_pack_email_config(merged)))

        for field, column in SECRET_FIELDS.items():
            if field not in values:
                continue

            secret = values[field]
            assignments.append(f"{column} = ?")
            params.append(
                keyring.seal_secret(secret, company_key, company_id, field)
                if secret
                else None
            )

        if not assignments:
            return self.get_account(company_id, account_id)

        with database_manager.control() as conn:
            conn.execute("BEGIN IMMEDIATE")

            try:
                if values.get(routing_field):
                    self._assert_routing_id_is_free(
                        conn,
                        channel=channel,
                        routing_field=routing_field,
                        routing_value=values[routing_field],
                        exclude_id=account_id,
                    )

                # Only when this puts an account back into service. Re-saving an
                # account that is already active — renaming it, pointing it at
                # a different department — must not be refused for occupying
                # the slot it already occupies.
                if (
                    str(values.get("status") or "") == "active"
                    and str(existing["status"]) != "active"
                ):
                    self._assert_channel_available(conn, company_id)

                assignments.append("updated_at = ?")
                params.extend([utc_now_iso(), account_id, company_id])

                conn.execute(
                    f"""
                    UPDATE channel_accounts
                    SET {', '.join(assignments)}
                    WHERE id = ? AND company_id = ?
                    """,
                    params,
                )
                conn.commit()

            except Exception:
                conn.rollback()
                raise

        return self.get_account(company_id, account_id)

    def delete_account(self, company_id: int, account_id: int) -> bool:
        with database_manager.control() as conn:
            cursor = conn.execute(
                "DELETE FROM channel_accounts WHERE id = ? AND company_id = ?",
                (int(account_id), int(company_id)),
            )
            conn.commit()

        if cursor.rowcount:
            logger.info(
                "Disconnected channel account id=%s from company %s",
                account_id,
                company_id,
            )

        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Credentials for the sending path
    # ------------------------------------------------------------------

    def credentials_for(
        self,
        *,
        company_id: int,
        channel: str,
        account_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Return the sending credentials for a company's channel.

        Returns ``None`` when the company has no active account on that channel,
        which the caller must treat as "cannot send" — never as "fall back to
        someone else's token".

        ``account_id`` names *which* account, for a company that has connected
        more than one on the same channel. Without it the lowest id wins, which
        is fine for a reply — that goes back out on the account the message
        arrived on — but wrong for a scheduled post, where the company picked a
        page and the post went to whichever one happened to be connected first.

        The id is matched together with the company, the channel and the active
        status rather than on its own, so an id belonging to another company
        selects nothing and the caller is told it cannot send.
        """
        company_id = int(company_id)
        normalized = str(channel or "").strip().lower()

        with database_manager.control() as conn:
            if account_id:
                row = conn.execute(
                    """
                    SELECT * FROM channel_accounts
                    WHERE id = ? AND company_id = ? AND channel = ?
                      AND status = 'active'
                    LIMIT 1
                    """,
                    (int(account_id), company_id, normalized),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM channel_accounts
                    WHERE company_id = ? AND channel = ? AND status = 'active'
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (company_id, normalized),
                ).fetchone()

        if not row:
            return None

        credentials: dict[str, Any] = {
            "id": int(row["id"]),
            "channel": normalized,
            # The display name the operator gave this account when connecting
            # it. Most senders never touch it -- Slack and Discord identify
            # the sender through the bot's own app identity -- but Viber's
            # `send_message` takes a `sender.name` on every call, and the
            # name the operator already chose here is a better default than
            # a hardcoded one.
            "name": row["name"],
            "page_id": row["page_id"],
            "phone_number_id": row["phone_number_id"],
            "instagram_business_id": row["instagram_business_id"],
            # Every channel's routing value, not only email's -- harmless for
            # the others (they already have their own dedicated field above),
            # and it is the only place email's mailbox address reaches the
            # sender, which has no other column of its own to read it from.
            "external_account_id": row["external_account_id"],
            "config": _loads_config(row["config_json"]),
            "access_token": None,
        }

        sealed = row["access_token_sealed"]

        if sealed:
            try:
                credentials["access_token"] = keyring.unseal_secret(
                    sealed,
                    database_manager.company_key(company_id),
                    company_id,
                    "access_token",
                )
            except CorruptedKeyMaterial:
                logger.error(
                    "Access token for company %s channel %s could not be "
                    "unsealed; refusing to send rather than using a stale value",
                    company_id,
                    normalized,
                )
                return None

        return credentials

    def active_accounts_for_channel(self, channel: str) -> list[dict[str, Any]]:
        """Every active account of one channel, across every company.

        Built for Discord: unlike a webhook channel, which is reachable the
        moment a route exists, a Discord bot needs its own outbound Gateway
        connection held open -- so at boot, and nowhere else, something has to
        ask "which bots does this platform need to connect right now" rather
        than waiting to be asked by an inbound request.
        """
        normalized = str(channel or "").strip().lower()

        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT id, company_id, access_token_sealed
                FROM channel_accounts
                WHERE channel = ? AND status = 'active'
                """,
                (normalized,),
            ).fetchall()

        accounts: list[dict[str, Any]] = []

        for row in rows:
            company_id = int(row["company_id"])
            token = None

            if row["access_token_sealed"]:
                try:
                    token = keyring.unseal_secret(
                        row["access_token_sealed"],
                        database_manager.company_key(company_id),
                        company_id,
                        "access_token",
                    )
                except CorruptedKeyMaterial:
                    logger.error(
                        "Access token for company %s channel %s account %s "
                        "could not be unsealed; skipping it",
                        company_id,
                        normalized,
                        row["id"],
                    )
                    continue

            if not token:
                continue

            accounts.append(
                {
                    "account_id": int(row["id"]),
                    "company_id": company_id,
                    "access_token": token,
                }
            )

        return accounts

    # ------------------------------------------------------------------
    # Credentials for the inbound path
    # ------------------------------------------------------------------

    def verify_token_for(
        self,
        *,
        company_id: int,
        account_id: int,
    ) -> str | None:
        """The webhook secret registered on one account, unsealed.

        Telegram has no request signature. What it has is a secret registered
        with `setWebhook` and echoed on every delivery in
        `X-Telegram-Bot-Api-Secret-Token`, so that value is this channel's whole
        authentication and lives in the same sealed column Meta's verify token
        does.

        Returns ``None`` when there is none, and the caller **refuses** the
        delivery rather than trusting it: a Telegram webhook URL carries only
        the bot id, which is public, so an unauthenticated endpoint would let
        anybody post into that company's inbox as any customer they chose.
        """
        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT verify_token_sealed FROM channel_accounts
                WHERE id = ? AND company_id = ? AND status = 'active'
                LIMIT 1
                """,
                (int(account_id), int(company_id)),
            ).fetchone()

        if not row or not row["verify_token_sealed"]:
            return None

        try:
            return keyring.unseal_secret(
                row["verify_token_sealed"],
                database_manager.company_key(int(company_id)),
                int(company_id),
                "verify_token",
            )
        except CorruptedKeyMaterial:
            logger.error(
                "The webhook secret for company %s account %s could not be "
                "unsealed; refusing the delivery rather than trusting it",
                company_id,
                account_id,
            )

            return None

    def app_secret_for_routing_id(
        self,
        *,
        channel: str,
        page_id: str | None = None,
        instagram_business_id: str | None = None,
        phone_number_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the receiving account's own app secret, unsealed.

        Most companies are served by the platform's single Meta app and store
        nothing here. A customer large enough to bring their own Meta app signs
        with their own secret, and this is the only way to check it.

        Returns ``None`` when no active account matches, when that account has
        no app secret of its own, or when the stored value cannot be unsealed —
        all of which the caller must treat as "not verified by this secret",
        never as "verified".
        """
        normalized = str(channel or "").strip().lower()

        # One source of truth for how a routing id maps to a company; a second
        # implementation here would eventually disagree with the one that
        # decides whose inbox a message lands in.
        company_id = database_manager.resolve_company_for_channel(
            channel=normalized,
            page_id=page_id,
            phone_number_id=phone_number_id,
            instagram_business_id=instagram_business_id,
        )

        if company_id is None:
            return None

        values = [
            str(value)
            for value in (page_id, instagram_business_id, phone_number_id)
            if value
        ]

        if not values:
            return None

        placeholders = ", ".join("?" for _ in values)
        # The routing id may be recorded on any of these columns, exactly as
        # resolve_company_for_channel accepts it on any of them.
        clause = " OR ".join(
            f"{column} IN ({placeholders})"
            for column in (
                "page_id",
                "instagram_business_id",
                "phone_number_id",
                "external_account_id",
            )
        )

        with database_manager.control() as conn:
            row = conn.execute(
                f"""
                SELECT id, app_secret_sealed
                FROM channel_accounts
                WHERE company_id = ?
                  AND status = 'active'
                  AND ({clause})
                ORDER BY id ASC
                LIMIT 1
                """,
                [company_id, *values, *values, *values, *values],
            ).fetchone()

        if not row or not row["app_secret_sealed"]:
            return None

        try:
            app_secret = keyring.unseal_secret(
                row["app_secret_sealed"],
                database_manager.company_key(company_id),
                company_id,
                # The same context this field was sealed under — see
                # SECRET_FIELDS. A different one will not open the value.
                "app_secret",
            )
        except CorruptedKeyMaterial:
            logger.error(
                "App secret for company %s account %s could not be unsealed; "
                "refusing to verify against it rather than accepting the request",
                company_id,
                row["id"],
            )
            return None

        if not app_secret:
            return None

        return {
            "app_secret": app_secret,
            "company_id": int(company_id),
            "account_id": int(row["id"]),
        }


channel_account_service = ChannelAccountService()
