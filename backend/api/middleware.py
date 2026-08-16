"""Security response headers, set by the application itself.

nginx already sets these, and in production nginx is what the browser talks to.
This exists because that sentence is a deployment assumption, not a property of
the system: a container without the reverse proxy, a `uvicorn main:app` on a
developer's laptop reachable from the network, a future second entry point —
each of those served the API and the SPA with no CSP, no HSTS, no frame
protection and no MIME-sniffing protection, and nothing in the code said so.

Defence in depth here is cheap. When nginx is in front, it sets the same header
names and nginx's `add_header` wins on the wire, so the two do not fight.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


# Kept in step with deploy/tzone-security-headers.conf. If you change one,
# change the other — a mismatch means the header a browser sees depends on
# whether the request happened to reach nginx.
BASE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

STRICT_TRANSPORT_SECURITY = "max-age=31536000; includeSubDomains"

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "media-src 'self' blob:; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

# The interactive API docs load Swagger UI and ReDoc from a CDN, which
# `script-src 'self'` forbids. Rather than widen the policy for every response
# to accommodate a surface that is off in production anyway, the CSP is skipped
# on exactly those paths. Everything else — including every API response — keeps
# the strict policy.
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, hsts: bool = True) -> None:
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request, call_next):
        response = await call_next(request)

        for header, value in BASE_HEADERS.items():
            response.headers.setdefault(header, value)

        # HSTS on a plain-HTTP response is meaningless and, on a development
        # machine, actively harmful: a browser that sees it refuses http://
        # for that host until the max-age expires.
        if self.hsts and request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", STRICT_TRANSPORT_SECURITY
            )

        if not request.url.path.startswith(_DOCS_PATHS):
            response.headers.setdefault(
                "Content-Security-Policy", CONTENT_SECURITY_POLICY
            )

        return response
