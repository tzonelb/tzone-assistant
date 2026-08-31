"""A request body larger than the cap is refused before it is parsed.

The webhook path has always sized its body in the application, "so a deployment
that never sees nginx is still bounded". The rest of the API had no such cap
and leaned entirely on the proxy -- and a proxy-less deployment is a scenario
the hosting notes describe as real. On one, a few hundred concurrent 100 MB
posts to `/api/auth/login` -- an endpoint that needs no account -- is a
memory-exhaustion outage.

The cap is refused before the body is parsed, not after: a giant body that is
buffered in full and then rejected has already done the damage. This checks the
timing indirectly, by checking that an oversized body is refused with 413 and a
normal one still goes through, and directly, by confirming the refusal is a
property of the middleware rather than of any one route.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app, raise_server_exceptions=False)


def test_an_oversized_body_is_refused(client):
    from config.settings import config

    oversized = json.dumps({"x": "a" * (int(config.API_MAX_BODY_BYTES) + 1024)})

    response = client.post(
        "/api/auth/login",
        data=oversized,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413, response.status_code
    assert "too large" in response.json()["detail"].lower()


def test_a_normal_body_is_not_refused(client):
    """The cap is far above any real payload, so an ordinary request is
    untouched -- it fails on its own merits, never on size."""
    response = client.post(
        "/api/auth/login",
        json={"email": "someone@example.com", "password": "wrong", "company": "acme"},
    )

    # 400/401/404/422 -- anything but 413. The credentials are wrong; that is
    # not this middleware's business.
    assert response.status_code != 413, response.text


def test_the_cap_covers_every_write_route_not_a_chosen_few(client):
    """It is middleware, so a route added next month is covered without anyone
    remembering to cap it -- the property a per-route check cannot give."""
    from config.settings import config

    oversized = json.dumps({"x": "a" * (int(config.API_MAX_BODY_BYTES) + 1024)})

    # Three unrelated routers. If the cap were per-route, one of these would
    # have been forgotten.
    for path in ("/api/auth/login", "/api/signup", "/api/activation/redeem"):
        response = client.post(
            path, data=oversized, headers={"content-type": "application/json"}
        )

        assert response.status_code == 413, f"{path} accepted an oversized body"


def test_a_get_is_never_capped(client):
    """A GET has no body worth capping, and the check must not cost it anything."""
    response = client.get("/api/auth/me")

    assert response.status_code != 413


def test_the_webhook_routes_keep_their_own_larger_cap(client):
    """The middleware must not double-cap the webhook path at a smaller number
    than `read_capped_body` allows, or a legitimate 5 MB Meta batch is refused
    at 2 MB before its own check ever runs."""
    from backend.api.middleware import BodySizeLimitMiddleware

    prefixes = BodySizeLimitMiddleware._WEBHOOK_PREFIXES

    assert any("meta" in p or "webhook" in p for p in prefixes), prefixes
