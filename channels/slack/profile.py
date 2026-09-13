"""Resolving a Slack sender's real name.

Unlike Telegram, Slack's `message` event carries only a user id (`U0123...`),
never a name -- so without this every customer would show up in the inbox as
a raw id instead of a person. `users.info` answers that, and is worth caching
for the same reason `channels/meta/profile.py` caches the Graph API: a
talkative customer would otherwise cost one extra Slack call per message.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

import httpx

from channels.credentials import MissingChannelCredentials, resolve
from config.settings import config


logger = logging.getLogger(__name__)


SLACK_USERS_INFO_URL = "https://slack.com/api/users.info"
TIMEOUT_SECONDS = 8

_PROFILE_TTL = timedelta(hours=12)
_PROFILE_CACHE: OrderedDict[str, tuple[datetime, str | None]] = OrderedDict()
_PROFILE_CACHE_LOCK = Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_limit() -> int:
    return max(1, int(config.PROFILE_CACHE_MAX_ENTRIES))


def _cached_name(key: str) -> tuple[bool, str | None]:
    """``(found, name)`` -- ``found`` is False on a genuine miss, distinct from
    a cached "Slack could not resolve a name for this id"."""
    with _PROFILE_CACHE_LOCK:
        cached = _PROFILE_CACHE.get(key)

        if cached is not None:
            _PROFILE_CACHE.move_to_end(key)

    if cached is None:
        return False, None

    cached_at, name = cached

    if _utc_now() - cached_at > _PROFILE_TTL:
        with _PROFILE_CACHE_LOCK:
            _PROFILE_CACHE.pop(key, None)
        return False, None

    return True, name


def _store_name(key: str, name: str | None) -> None:
    limit = _cache_limit()

    with _PROFILE_CACHE_LOCK:
        _PROFILE_CACHE[key] = (_utc_now(), name)
        _PROFILE_CACHE.move_to_end(key)

        while len(_PROFILE_CACHE) > limit:
            _PROFILE_CACHE.popitem(last=False)


def resolve_slack_display_name(*, user_id: str, company_id: int) -> str | None:
    """The Slack user's real or display name, or ``None`` if it cannot be
    resolved -- never raises, so a naming failure never blocks a message."""
    normalized_user_id = str(user_id or "").strip()

    if not normalized_user_id:
        return None

    cache_key = f"{int(company_id)}:{normalized_user_id}"
    found, cached_name = _cached_name(cache_key)

    if found:
        return cached_name

    try:
        token = resolve(int(company_id), "slack")["access_token"]
    except MissingChannelCredentials:
        return None

    try:
        response = httpx.get(
            SLACK_USERS_INFO_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"user": normalized_user_id},
            timeout=TIMEOUT_SECONDS,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not resolve a Slack user name: %s", exc)
        return None

    if not body.get("ok"):
        # Cached too -- a revoked scope or a bot-less token would otherwise
        # retry on every single message from the same person.
        _store_name(cache_key, None)
        return None

    user = body.get("user") or {}
    profile = user.get("profile") or {}
    name = (
        str(profile.get("real_name") or "").strip()
        or str(user.get("real_name") or "").strip()
        or str(user.get("name") or "").strip()
        or None
    )

    _store_name(cache_key, name)

    return name
