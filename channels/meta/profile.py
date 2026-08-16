from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve
from channels.meta.logger import log_meta_event
from config.settings import config


logger = logging.getLogger(__name__)


_PROFILE_TTL = timedelta(hours=12)

# An ``OrderedDict`` rather than a plain one because the cache is now bounded:
# entries are kept in least-recently-used order and the oldest is evicted at
# ``PROFILE_CACHE_MAX_ENTRIES``. The entry expiring after twelve hours was never
# a bound — every distinct sender id added one, and a flood of distinct ids grew
# the cache until the process ran out of memory.
_PROFILE_CACHE: OrderedDict[str, tuple[datetime, dict[str, Any]]] = OrderedDict()
_PROFILE_CACHE_LOCK = Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_limit() -> int:
    return max(1, int(config.PROFILE_CACHE_MAX_ENTRIES))


def _cached_profile(user_id: str) -> dict[str, Any] | None:
    key = str(user_id)

    with _PROFILE_CACHE_LOCK:
        cached = _PROFILE_CACHE.get(key)

        if cached is not None:
            # A hit is a use: it moves to the fresh end so the entries evicted
            # under pressure are the ones nobody is asking for.
            _PROFILE_CACHE.move_to_end(key)

    if not cached:
        return None

    cached_at, profile = cached

    if _utc_now() - cached_at > _PROFILE_TTL:
        with _PROFILE_CACHE_LOCK:
            _PROFILE_CACHE.pop(key, None)
        return None

    return dict(profile)


def _store_profile(user_id: str, profile: dict[str, Any]) -> None:
    key = str(user_id)
    limit = _cache_limit()

    with _PROFILE_CACHE_LOCK:
        _PROFILE_CACHE[key] = (_utc_now(), dict(profile))
        _PROFILE_CACHE.move_to_end(key)

        evicted = 0

        while len(_PROFILE_CACHE) > limit:
            _PROFILE_CACHE.popitem(last=False)
            evicted += 1

    if evicted:
        # Worth a line: a cache that is constantly evicting is either undersized
        # or being filled with ids that will never be looked up again, and the
        # second is what a flood looks like from here.
        logger.warning(
            "Profile cache is at its limit of %s entries; evicted %s "
            "least-recently-used entr%s",
            limit,
            evicted,
            "y" if evicted == 1 else "ies",
        )


def resolve_meta_profile(
    user_id: str,
    company_id: int,
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

    cache_key = f"{int(company_id)}:{normalized_user_id}"
    cached = _cached_profile(cache_key)

    if cached is not None:
        return cached

    if normalized_channel != "messenger":
        return {}

    try:
        access_token = resolve(company_id, normalized_channel)["access_token"]
    except MissingChannelCredentials:
        # A missing token is not an error here; the conversation simply shows
        # the channel default name until an account is connected.
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
                "access_token": access_token,
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

        _store_profile(cache_key, profile)

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
