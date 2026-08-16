"""Resolving which credentials to send a message with.

Every outbound message belongs to a company, and must go out through that
company's own connected account. Using one global token would answer a
customer from whichever page happened to be configured in the environment —
on a multi-company server, that means replying to one company's customer from
another company's page.
"""

from __future__ import annotations

import logging
from typing import Any

from config.settings import config
from database.manager import database_manager


logger = logging.getLogger(__name__)


class MissingChannelCredentials(RuntimeError):
    """No usable credentials exist for this company and channel."""


def _single_company_fallback(company_id: int) -> bool:
    """Whether the environment token may stand in for a connected account.

    Only when this deployment serves exactly one company, and it is this one.
    ``default_company_id`` returns ``None`` as soon as a second company exists,
    which is what stops a shared token from leaking across tenants.
    """
    try:
        return database_manager.default_company_id() == int(company_id)
    except Exception:  # noqa: BLE001
        logger.exception("Could not determine whether this is a single-company install")
        return False


def resolve(company_id: int, channel: str) -> dict[str, Any]:
    """Return sending credentials for one company on one channel.

    Raises :class:`MissingChannelCredentials` rather than returning a partially
    filled result, so a caller can never send with someone else's token.
    """
    # Imported here: the service imports the database manager, and importing it
    # at module scope would pull the database into the channel layer's import
    # graph before configuration is ready.
    from backend.services.channel_account_service import channel_account_service

    company_id = int(company_id)
    normalized = str(channel or "").strip().lower()

    account = channel_account_service.credentials_for(
        company_id=company_id, channel=normalized
    )

    if account and account.get("access_token"):
        return account

    if not _single_company_fallback(company_id):
        raise MissingChannelCredentials(
            f"Company {company_id} has no connected {normalized} account with a "
            "valid access token. Connect one under Channels before sending."
        )

    # Single-company install that has not migrated to a connected account yet.
    if normalized in ("messenger", "instagram"):
        token = (
            config.META_INSTAGRAM_ACCESS_TOKEN
            if normalized == "instagram" and config.META_INSTAGRAM_ACCESS_TOKEN
            else config.META_PAGE_ACCESS_TOKEN
        )

        if not token:
            raise MissingChannelCredentials(
                f"No access token configured for {normalized}."
            )

        logger.warning(
            "Using the environment access token for %s. Connect a channel "
            "account for company %s before adding a second company.",
            normalized,
            company_id,
        )
        return {
            "id": None,
            "channel": normalized,
            "access_token": token,
            "page_id": config.FACEBOOK_PAGE_ID or None,
            "instagram_business_id": config.INSTAGRAM_BUSINESS_ID or None,
            "phone_number_id": None,
        }

    if normalized == "whatsapp":
        if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
            raise MissingChannelCredentials(
                "No WhatsApp access token and phone number id configured."
            )

        logger.warning(
            "Using the environment WhatsApp credentials for company %s. "
            "Connect a channel account before adding a second company.",
            company_id,
        )
        return {
            "id": None,
            "channel": normalized,
            "access_token": config.WHATSAPP_ACCESS_TOKEN,
            "phone_number_id": config.WHATSAPP_PHONE_NUMBER_ID,
            "page_id": None,
            "instagram_business_id": None,
        }

    raise MissingChannelCredentials(f"Channel '{channel}' cannot send messages.")
