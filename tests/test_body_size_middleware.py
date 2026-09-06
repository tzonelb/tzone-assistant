"""The body-size middleware must cap large bodies WITHOUT eating small ones.

This exists because a regression shipped that no other test could see: the
per-route tests mount a single router on a bare app, so the production
middleware stack never runs against them. `BodySizeLimitMiddleware` wrapped the
request's receive channel but then called the wrapper instead of the original
stream, so every POST reached its endpoint with an empty body and FastAPI
answered "There was an error parsing the body" -- login included, i.e. nobody
could sign in.

So this drives the REAL app (`main.app`), with the whole middleware stack, and
asserts both halves: a normal JSON body parses and reaches the endpoint, and an
over-cap body is still refused with 413.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client(master_key):
    import main
    from starlette.testclient import TestClient

    return TestClient(main.app)


def test_a_normal_json_post_reaches_the_endpoint(client):
    """The body must parse and reach the route. It will fail authentication --
    there is no such company -- but that is the endpoint's own logic running,
    which only happens if the body arrived intact."""
    response = client.post(
        "/api/auth/login",
        json={"company": "acme", "email": "a@b.com", "password": "secret12345"},
    )

    assert response.status_code != 400, response.text
    assert "parsing the body" not in response.text
    # It got as far as the credentials check.
    assert response.status_code in (401, 429)


def test_an_oversized_body_is_still_refused(client, monkeypatch):
    from config.settings import config

    monkeypatch.setattr(config, "API_MAX_BODY_BYTES", 200)

    oversized = "x" * 5000
    response = client.post(
        "/api/auth/login",
        content=oversized,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_an_oversized_body_is_refused_even_when_content_length_lies(client, monkeypatch):
    """The declared length is only a claim; the streamed bytes are what count.

    Sent chunked (no Content-Length) so the up-front check cannot catch it and
    the streaming counter has to.
    """
    from config.settings import config

    monkeypatch.setattr(config, "API_MAX_BODY_BYTES", 200)

    def big_chunks():
        for _ in range(10):
            yield b"x" * 500

    response = client.post(
        "/api/auth/login",
        content=big_chunks(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
