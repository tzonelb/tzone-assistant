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


class SessionCookieMiddleware(BaseHTTPMiddleware):
    """Let an httpOnly cookie stand in for the Authorization header, safely.

    Two jobs, in one place because the order between them matters and two
    middlewares would leave it to registration order.

    **First, CSRF.** The session now lives in an `httpOnly` cookie, which the
    browser attaches automatically — that is the point, and it is also the
    opening: a form on another site can make the browser send it. So a
    cookie-authenticated write must echo a second token that the page can read
    and a cross-origin attacker cannot.

    **Then, the bridge.** When a request carries the session cookie and no
    `Authorization` header, the cookie is presented to the rest of the
    application as a bearer token. Every existing dependency keeps working
    unchanged, which is the point: four separate auth dependencies did not need
    to learn about cookies, and a change that touched all four would have been
    four chances to get one wrong.

    Three things this deliberately does not do:

    * **It does not touch bearer-token requests.** Nothing attaches an
      `Authorization` header automatically, so such a request cannot be forged
      from another origin. Demanding a CSRF header there would break every CLI
      and integration for no gain — and it is why the header always wins over
      the cookie below.
    * **It does not touch safe methods.** A forged GET achieves nothing, and
      HEAD and OPTIONS are sent by the browser on its own.
    * **It does not touch the webhooks.** A provider's callback carries no
      cookie and is authenticated by an HMAC over the raw body, which is a
      stronger proof than this one.

    A refusal names itself rather than answering with a generic 403: a client
    whose token has rotated needs to tell this apart from a permission failure,
    so it can reload rather than sign the user out.
    """

    # Reached by something other than the browser session. A cookie never
    # accompanies these, so the check would only ever refuse a request that was
    # authenticated some other way.
    EXEMPT_PREFIXES = ("/webhook", "/health")

    # Routes that *establish* a session rather than act on one.
    #
    # These must be exempt, and a test caught why: with a stale cookie still in
    # the jar, signing in again was refused as a CSRF failure. That is not an
    # edge case — it is a user whose session expired, or who wants to sign in as
    # somebody else, being told to reload a page that will fail the same way.
    # Requiring the *old* session's token to replace it is circular.
    #
    # It is safe because nothing here reads the existing session: whatever
    # cookie arrives is overwritten by the response. The residual concern is
    # login CSRF — forcing a victim's browser to sign in as the attacker so
    # their work lands in the attacker's account — and `SameSite=Strict` is what
    # answers that: the browser will not attach these cookies to a request
    # initiated by another site at all.
    SESSION_ESTABLISHING_PREFIXES = (
        "/api/auth/login",
        "/api/auth/password/reset",
        "/api/platform/auth/login",
    )

    async def dispatch(self, request, call_next):
        import hmac

        from backend.api.session_cookies import (
            CSRF_COOKIE,
            CSRF_HEADER,
            SAFE_METHODS,
            SESSION_COOKIE,
            authenticated_by_cookie,
        )

        path = request.url.path
        by_cookie = authenticated_by_cookie(request)

        if (
            by_cookie
            and request.method.upper() not in SAFE_METHODS
            and not path.startswith(self.EXEMPT_PREFIXES)
            and not path.startswith(self.SESSION_ESTABLISHING_PREFIXES)
        ):
            expected = request.cookies.get(CSRF_COOKIE)
            offered = request.headers.get(CSRF_HEADER)

            # `compare_digest` over `!=` because this runs on every write and
            # compares against a value the caller supplies. The timing leak is
            # small; avoiding it costs nothing.
            if (
                not expected
                or not offered
                or not hmac.compare_digest(str(expected), str(offered))
            ):
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "error": "csrf_token_invalid",
                            "message": (
                                "This request could not be verified. Reload the "
                                "page and try again."
                            ),
                        }
                    },
                )

        if by_cookie:
            token = request.cookies.get(SESSION_COOKIE)

            if token:
                # Header names are lower-case bytes in an ASGI scope. Appended
                # rather than replacing the list, because `by_cookie` is only
                # true when there was no Authorization header to overwrite.
                request.scope["headers"] = [
                    *request.scope["headers"],
                    (b"authorization", f"Bearer {token}".encode("latin-1")),
                ]

        return await call_next(request)
