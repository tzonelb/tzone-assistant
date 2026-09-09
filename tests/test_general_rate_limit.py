"""The API had no general rate limit of its own.

`deploy/nginx.conf` runs a `tzone_api` zone in front of every route and says so
in its own comment: "the application enforces its own ceilings
independently... a deployment that never sees nginx must still be bounded."
That was not true. Login has a database-backed lock, and a couple of routes
cap their own concurrency, but nothing bounded the rest of the API -- a
`uvicorn main:app` reachable directly, with no proxy in front of it, had no
rate limit anywhere outside `/api/auth/login`.

Drives the real app (`main.app`), like `test_body_size_middleware.py`, because
`GeneralRateLimitMiddleware` only runs as part of the real middleware stack.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client(master_key):
    import main
    from starlette.testclient import TestClient

    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _reset_buckets():
    from backend.api.middleware import GeneralRateLimitMiddleware

    GeneralRateLimitMiddleware._buckets.clear()
    yield
    GeneralRateLimitMiddleware._buckets.clear()


def test_a_burst_past_the_cap_is_refused_with_429(client, monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "API_RATE_LIMIT_BURST", 3)
    monkeypatch.setattr(config, "API_RATE_LIMIT_PER_MINUTE", 60)

    statuses = [client.get("/api/auth/me").status_code for _ in range(6)]

    assert statuses[:3] == [401, 401, 401], statuses
    assert 429 in statuses[3:], (
        "a burst of requests past API_RATE_LIMIT_BURST was never refused -- "
        "the API has no rate limit of its own, and depends entirely on nginx "
        "being in front of it"
    )


def test_the_429_carries_a_retry_after_header(client, monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "API_RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(config, "API_RATE_LIMIT_PER_MINUTE", 60)

    client.get("/api/auth/me")
    response = client.get("/api/auth/me")

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1


def test_health_checks_are_never_rate_limited(client, monkeypatch):
    """nginx itself exempts /health (`access_log off`) -- it is polled on a
    schedule the operator chose, not a client that needs throttling."""
    from config.settings import config

    monkeypatch.setattr(config, "API_RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(config, "API_RATE_LIMIT_PER_MINUTE", 60)

    statuses = [client.get("/health/").status_code for _ in range(10)]

    assert all(status == 200 for status in statuses), statuses


def test_a_new_window_refills_the_bucket(client, monkeypatch):
    import time

    from config.settings import config

    monkeypatch.setattr(config, "API_RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(config, "API_RATE_LIMIT_PER_MINUTE", 600)

    client.get("/api/auth/me")
    blocked = client.get("/api/auth/me")
    assert blocked.status_code == 429

    time.sleep(0.25)

    recovered = client.get("/api/auth/me")
    assert recovered.status_code == 401, (
        "the bucket never refilled -- a client that waits out the limit "
        "should be let through again"
    )
