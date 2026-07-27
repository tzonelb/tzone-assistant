import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests

from backend.services.channel_account_service import channel_account_service, ChannelAccountError
from config.settings import config
from database.database import db


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


STATE_TTL_MINUTES = 10

# Scopes needed to list pages, message on Messenger, and check/message
# on the linked Instagram professional account.
OAUTH_SCOPES = [
    "pages_show_list",
    "pages_messaging",
    "pages_read_engagement",
    "instagram_basic",
    "instagram_manage_messages",
    "business_management",
]


class FacebookOAuthError(Exception):
    pass


class FacebookOAuthService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facebook_oauth_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    state_token TEXT NOT NULL UNIQUE,
                    company_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                )
                """
            )
            conn.commit()

    def build_authorize_url(self, *, company_id: int) -> str:
        if not config.META_APP_ID:
            raise FacebookOAuthError(
                "META_APP_ID is not configured — set it in .env before connecting via Facebook Login."
            )

        state_token = secrets.token_urlsafe(24)
        now = utc_now()
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO facebook_oauth_states (state_token, company_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (state_token, company_id, now.isoformat(), (now + timedelta(minutes=STATE_TTL_MINUTES)).isoformat()),
            )
            conn.commit()

        params = {
            "client_id": config.META_APP_ID,
            "redirect_uri": config.META_OAUTH_REDIRECT_URI,
            "state": state_token,
            "scope": ",".join(OAUTH_SCOPES),
            "response_type": "code",
        }
        return f"https://www.facebook.com/{config.META_API_VERSION}/dialog/oauth?{urlencode(params)}"

    def _consume_state(self, *, state_token: str) -> int:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT id, company_id, expires_at, consumed_at FROM facebook_oauth_states WHERE state_token = ?",
                (state_token,),
            ).fetchone()
            if not row:
                raise FacebookOAuthError("Invalid or unknown OAuth state — please try connecting again.")
            if row["consumed_at"] is not None:
                raise FacebookOAuthError("This connection attempt was already used — please try again.")
            if datetime.fromisoformat(row["expires_at"]) < utc_now():
                raise FacebookOAuthError("This connection attempt expired — please try again.")

            conn.execute(
                "UPDATE facebook_oauth_states SET consumed_at = ? WHERE id = ?",
                (utc_now().isoformat(), row["id"]),
            )
            conn.commit()
        return row["company_id"]

    def handle_callback(self, *, code: str, state: str) -> dict[str, Any]:
        company_id = self._consume_state(state_token=state)

        # 1) Exchange the authorization code for a user access token.
        try:
            token_response = requests.get(
                f"https://graph.facebook.com/{config.META_API_VERSION}/oauth/access_token",
                params={
                    "client_id": config.META_APP_ID,
                    "client_secret": config.META_APP_SECRET,
                    "redirect_uri": config.META_OAUTH_REDIRECT_URI,
                    "code": code,
                },
                timeout=15,
            )
            token_data = token_response.json()
        except requests.RequestException as exc:
            raise FacebookOAuthError(f"Could not reach Meta to complete login: {exc}")

        if "error" in token_data or "access_token" not in token_data:
            raise FacebookOAuthError(
                f"Meta rejected the login: {token_data.get('error', {}).get('message', 'unknown error')}"
            )
        user_access_token = token_data["access_token"]

        # 2) List every Page this user manages, with a page access token for each.
        try:
            pages_response = requests.get(
                f"https://graph.facebook.com/{config.META_API_VERSION}/me/accounts",
                params={
                    "fields": "id,name,access_token,instagram_business_account",
                    "access_token": user_access_token,
                },
                timeout=15,
            )
            pages_data = pages_response.json()
        except requests.RequestException as exc:
            raise FacebookOAuthError(f"Could not list your Facebook Pages: {exc}")

        if "error" in pages_data:
            raise FacebookOAuthError(f"Meta rejected the request: {pages_data['error'].get('message', 'unknown error')}")

        pages = pages_data.get("data", [])
        if not pages:
            raise FacebookOAuthError(
                "No Facebook Pages found for this account — you need to be an admin of at least one Page."
            )

        connected = []
        errors = []
        for page in pages:
            page_id = page["id"]
            page_name = page.get("name", "Facebook Page")
            page_token = page.get("access_token")
            if not page_token:
                continue

            try:
                account = channel_account_service.connect_messenger(
                    company_id=company_id, page_id=page_id, access_token=page_token, name=page_name,
                )
                connected.append({"channel": "messenger", "name": page_name, "account_id": account["id"]})
            except ChannelAccountError as exc:
                errors.append(f"{page_name} (Messenger): {exc}")

            instagram_business = page.get("instagram_business_account")
            if instagram_business:
                try:
                    account = channel_account_service.connect_instagram(
                        company_id=company_id, page_id=page_id, access_token=page_token, name=page_name,
                    )
                    connected.append({"channel": "instagram", "name": page_name, "account_id": account["id"]})
                except ChannelAccountError as exc:
                    errors.append(f"{page_name} (Instagram): {exc}")

        return {"connected": connected, "errors": errors, "company_id": company_id}


facebook_oauth_service = FacebookOAuthService()
