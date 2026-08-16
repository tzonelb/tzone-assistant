from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

import httpx

from channels.meta.logger import log_meta_event
from config.settings import config


_PROFILE_TTL = timedelta(hours=12)
_PROFILE_CACHE: dict[str, tuple[datetime, dict[str, Any]]] = {}
_PROFILE_CACHE_LOCK = Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cached_profile(user_id: str) -> dict[str, Any] | None:
    with _PROFILE_CACHE_LOCK:
        cached = _PROFILE_CACHE.get(str(user_id))

    if not cached:
        return None

    cached_at, profile = cached

    if _utc_now() - cached_at > _PROFILE_TTL:
        with _PROFILE_CACHE_LOCK:
            _PROFILE_CACHE.pop(str(user_id), None)
        return None

    return dict(profile)


def _store_profile(user_id: str, profile: dict[str, Any]) -> None:
    with _PROFILE_CACHE_LOCK:
        _PROFILE_CACHE[str(user_id)] = (_utc_now(), dict(profile))


def resolve_meta_profile(
    user_id: str,
    channel: str = "messenger",
) -> dict[str, Any]:
    """Resolve a customer's official profile name without breaking webhook flow.

    Messenger PSIDs can be resolved with the connected Page access token.
    Unsupported channels or failed requests return an empty dictionary.
    """

    normalized_user_id = str(user_id or "").strip()
    normalized_channel = str(channel or "").strip().lower()

    if not normalized_user_id:
        return {}

    cached = _cached_profile(normalized_user_id)

    if cached is not None:
        return cached

    if (
        normalized_channel != "messenger"
        or not config.META_PAGE_ACCESS_TOKEN
    ):
        return {}

    url = (
        f"https://graph.facebook.com/"
        f"{config.META_API_VERSION}/"
        f"{normalized_user_id}"
    )

    try:
        response = httpx.get(
            url,
            params={
                "fields": "first_name,last_name,profile_pic",
                "access_token": config.META_PAGE_ACCESS_TOKEN,
            },
            timeout=8,
        )

        if not response.is_success:
            log_meta_event(
                "profile_resolve_failed",
                {
                    "channel": normalized_channel,
                    "user_id": normalized_user_id,
                    "status_code": response.status_code,
                    "response": (
                        response.json()
                        if response.content
                        else {}
                    ),
                },
            )
            return {}

        payload = response.json() if response.content else {}
        first_name = str(payload.get("first_name") or "").strip()
        last_name = str(payload.get("last_name") or "").strip()
        official_name = " ".join(
            value
            for value in (first_name, last_name)
            if value
        ).strip()

        profile = {
            "customer_name": official_name,
            "sender_name": official_name,
            "customer_profile_picture": payload.get("profile_pic"),
            "profile_source": "meta_graph",
        }

        profile = {
            key: value
            for key, value in profile.items()
            if value
        }

        if profile:
            _store_profile(normalized_user_id, profile)

        return profile

    except httpx.HTTPError as exc:
        log_meta_event(
            "profile_resolve_error",
            {
                "channel": normalized_channel,
                "user_id": normalized_user_id,
                "error": str(exc),
            },
        )
        return {}
