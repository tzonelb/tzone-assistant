"""Publishing a post to a company's connected page.

Separate from messaging: a post goes to ``/{page-id}/feed`` (or ``/photos`` when
there is an image), using the company's own page token.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve
from backend.services.module_gate import module_gate
from backend.services.scheduler_service import scheduler_service
from config.settings import config


logger = logging.getLogger(__name__)

PUBLISH_TIMEOUT_SECONDS = 30


def publish_post(
    *,
    company_id: int,
    channel: str,
    body: str,
    media_url: str | None = None,
    link_url: str | None = None,
) -> dict[str, Any]:
    """Publish and return a normalised result. Never raises.

    The caller records the outcome and retries, so an exception escaping here
    would only turn a retryable failure into a lost post.
    """
    normalized_channel = str(channel or "messenger").strip().lower()

    try:
        credentials = resolve(company_id, normalized_channel)
    except MissingChannelCredentials as exc:
        logger.error("Cannot publish for company %s: %s", company_id, exc)
        return {"ok": False, "reason": "missing_credentials", "error": str(exc)}

    page_id = credentials.get("page_id") or credentials.get("instagram_business_id")

    if not page_id:
        return {"ok": False, "reason": "missing_page_id"}

    if media_url:
        endpoint = f"{page_id}/photos"
        payload: dict[str, Any] = {"caption": body, "url": media_url}
    else:
        endpoint = f"{page_id}/feed"
        payload = {"message": body}

        if link_url:
            payload["link"] = link_url

    url = f"https://graph.facebook.com/{config.META_API_VERSION}/{endpoint}"

    try:
        response = httpx.post(
            url,
            params={"access_token": credentials["access_token"]},
            data=payload,
            timeout=PUBLISH_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Publish failed for company %s: %s", company_id, type(exc).__name__
        )
        return {"ok": False, "reason": "network_error", "error": str(exc)}

    result_payload = response.json() if response.content else {}

    if not response.is_success:
        logger.warning(
            "Provider rejected a post for company %s with status %s",
            company_id,
            response.status_code,
        )

    return {
        "ok": response.is_success,
        "status_code": response.status_code,
        "provider_post_id": result_payload.get("post_id") or result_payload.get("id"),
        "response": result_payload,
    }


def publish_due_posts(company_id: int) -> int:
    """Publish every approved post whose time has come. Returns how many went out."""
    # Scheduler off means nothing is published on this company's behalf. This
    # is the one gate where the consequence is public: a post going out to a
    # company's followers from a module its team can no longer open, and cannot
    # cancel from inside the platform, is not a switch that was ignored — it is
    # the company posting to its own audience without an operator.
    #
    # Nothing is claimed, so the queue is intact if the module comes back on.
    if not module_gate.enabled(company_id, "scheduler"):
        return 0

    published = 0

    for post in scheduler_service.claim_due(company_id):
        result = publish_post(
            company_id=company_id,
            channel=post["channel"],
            body=post["body"],
            media_url=post.get("media_url"),
            link_url=post.get("link_url"),
        )

        if result.get("ok"):
            scheduler_service.mark_published(
                company_id=company_id,
                post_id=post["id"],
                provider_post_id=result.get("provider_post_id"),
            )
            published += 1
        else:
            scheduler_service.mark_failed(
                company_id=company_id,
                post_id=post["id"],
                error=str(result.get("error") or result.get("reason")),
            )

    return published
