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
from backend.services.company_gate import company_gate
from backend.services.subscription_gate import subscription_gate
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
    channel_account_id: int | None = None,
) -> dict[str, Any]:
    """Publish and return a normalised result. Never raises.

    The caller records the outcome and retries, so an exception escaping here
    would only turn a retryable failure into a lost post.

    ``channel_account_id`` is the account the post was scheduled against. It
    was stored on the row from the day the scheduler shipped and never read:
    the post went out through whichever account `resolve` picked, which is the
    lowest id. For a company with one page that is the same page. For a company
    with two, it published to the wrong audience — and unlike a setting that
    saves and does nothing, the result was public.
    """
    normalized_channel = str(channel or "messenger").strip().lower()

    try:
        credentials = resolve(company_id, normalized_channel, channel_account_id)
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

    # And the same for the bill. The paragraph above argues that a post must
    # not go out from a module the team can no longer open and cannot cancel
    # from inside the platform — every word of which is true of a company whose
    # subscription has lapsed, whose screens answer 402 and whose scheduler is
    # exactly as unreachable. The reasoning was done here and applied to the
    # gate next door; this extends it rather than repeating it.
    #
    # This is the consequence that is *public*. A paused company still posting
    # to its own followers next Thursday is the platform delivering the service
    # it has just stopped charging for, in front of an audience.
    if subscription_gate.lapsed(company_id):
        return 0

    # A suspended company posting to its own followers is the same publication
    # in front of the same audience, decided by an operator rather than a bill.
    if company_gate.suspended(company_id):
        return 0

    published = 0

    for post in scheduler_service.claim_due(company_id):
        result = publish_post(
            company_id=company_id,
            channel=post["channel"],
            body=post["body"],
            media_url=post.get("media_url"),
            link_url=post.get("link_url"),
            channel_account_id=post.get("channel_account_id"),
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
