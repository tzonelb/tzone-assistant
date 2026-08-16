"""Tests for the security headers the application sets itself.

nginx sets the same headers, and in production nginx is what the browser talks
to. These tests exist because that is a deployment assumption rather than a
property of the system: a container without the reverse proxy, or a
`uvicorn main:app` reachable from the network, served the API with no CSP and no
frame protection, and nothing in the code recorded that this was a gap.

The nginx half of the same defect — three `location` blocks that declared an
`add_header` of their own and therefore inherited none of the server-level
security headers, one of them being `/index.html` itself — cannot be tested from
Python. It is verified by `curl -I` against a real deployment; the manual step is
listed in docs/LAUNCH_READINESS.md.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.api.middleware import (
    BASE_HEADERS,
    CONTENT_SECURITY_POLICY,
    SecurityHeadersMiddleware,
)


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/anything")
    def anything() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/boom")
    def boom() -> dict[str, str]:
        raise ValueError("deliberate")

    @app.get("/refused")
    def refused() -> dict[str, str]:
        raise HTTPException(status_code=403, detail="No.")

    @app.get("/docs")
    def docs() -> dict[str, str]:
        return {"ok": "docs"}

    return TestClient(app, raise_server_exceptions=False)


def test_every_response_carries_the_base_headers(client):
    response = client.get("/anything")

    for header, value in BASE_HEADERS.items():
        assert response.headers[header] == value


def test_a_content_security_policy_is_set(client):
    """The header that actually blunts XSS, which is the vector that would
    steal a session in the first place."""
    response = client.get("/anything")

    assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_a_handled_error_response_carries_them_too(client):
    """A refusal is a normal response and must not be the one that goes out
    bare — 401s and 403s are the most common thing this API returns to a
    caller who should not be there."""
    response = client.get("/refused")

    assert response.status_code == 403
    for header, value in BASE_HEADERS.items():
        assert response.headers[header] == value


def test_an_unhandled_500_does_not_carry_them_and_this_is_the_known_boundary(
    client,
):
    """Documented limitation, verified rather than assumed.

    Starlette builds the stack as ServerErrorMiddleware -> user middleware ->
    ExceptionMiddleware -> router. A response synthesised by
    ServerErrorMiddleware for an unhandled exception is therefore produced
    *outside* every middleware an application can add, including this one.
    Registering an `Exception` handler does not change that — it is installed
    on ServerErrorMiddleware itself.

    Accepted because the exposure is small and covered: the body is a short
    generic error with no markup and no scripts, so CSP and X-Frame-Options
    have nothing to protect there; and in production nginx sets the headers on
    every response regardless of what the application did.

    This test exists so the next person to look does not spend an afternoon
    rediscovering it, and so that a Starlette release which changes the
    ordering announces itself here.
    """
    response = client.get("/boom")

    assert response.status_code == 500
    assert "X-Frame-Options" not in response.headers


def test_a_404_carries_them_too(client):
    response = client.get("/no-such-path")

    assert response.status_code == 404
    assert response.headers["X-Frame-Options"] == "DENY"


def test_the_docs_are_exempt_from_the_policy_only(client):
    """Swagger loads its bundle from a CDN, which `script-src 'self'` forbids.
    Widening the policy for every response to suit a surface that is off in
    production would be the wrong trade, so the docs paths lose the CSP and
    keep everything else."""
    response = client.get("/docs")

    assert "Content-Security-Policy" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_hsts_is_not_sent_over_plain_http(client):
    """A browser that sees HSTS on http:// refuses plain HTTP for that host
    until the max-age expires — which on a developer machine is a self-inflicted
    outage, not protection."""
    response = client.get("/anything")

    assert "Strict-Transport-Security" not in response.headers


def test_hsts_is_sent_over_https(client):
    response = client.get("https://testserver/anything")

    assert response.headers["Strict-Transport-Security"].startswith("max-age=")
