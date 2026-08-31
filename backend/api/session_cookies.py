"""Keeping the session token out of JavaScript's reach.

The token lived in `localStorage`, which means any script running on the page
can read it: one cross-site scripting hole anywhere in the app — in a dependency,
in a rendered customer name, in an error message — hands an attacker a working
session that outlives the page they stole it from.

An `httpOnly` cookie cannot be read by script at all. XSS can still *use* the
session by making requests from the page, but it can no longer take the
credential away, which is the difference between an incident that ends when the
tab closes and one that ends when the token expires.

### Why the Authorization header still works

Removing it would break the CLI, every test, and any integration a customer
builds. The cookie is an additional path, not a replacement: a request with a
bearer token is authenticated exactly as before.

### CSRF, and only where it is needed

A cookie is attached by the browser automatically, which is the whole point and
also the whole problem: a form on another site can make the browser send it. So
cookie-authenticated writes carry a double-submit token — a second cookie the
script *can* read, echoed back in a header. An attacker on another origin can
make the browser send the session cookie but cannot read it to copy the value
into a header, because that is what the same-origin policy prevents.

The check applies **only** to requests that authenticated by cookie. A bearer
token is not attached automatically by anything, so such a request cannot be
forged cross-site — and demanding a CSRF header there would break every API
client for no gain.

`SameSite=Strict` is set as well, which stops most of this at the browser. The
double-submit token is the belt to its braces: SameSite is enforced by the
browser, and a security property enforced only by the client is a property the
server cannot rely on.
"""

from __future__ import annotations

import secrets

from fastapi import Request, Response


SESSION_COOKIE = "tzone_session"
CSRF_COOKIE = "tzone_csrf"
CSRF_HEADER = "X-CSRF-Token"

# Methods that cannot change anything, so a forged one achieves nothing. HEAD
# and OPTIONS are included because a browser sends them on its own.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def is_secure_request(request: Request) -> bool:
    """Whether this request arrived over https.

    Read from the forwarded protocol first, because in production the
    application speaks plain http to nginx and would otherwise never mark a
    cookie `Secure`. `X-Forwarded-Proto` is set by nginx and, like
    `X-Forwarded-For`, is only trustworthy because the proxy replaces whatever
    the client sent.
    """
    forwarded = request.headers.get("x-forwarded-proto", "")

    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"

    return request.url.scheme == "https"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def attach(
    response: Response,
    request: Request,
    *,
    token: str,
    expires_in: int,
) -> str:
    """Put the session in an httpOnly cookie and issue its CSRF partner.

    Returns the CSRF token so the caller can also put it in the response body:
    a client that reads it from the body does not have to parse cookies, and the
    cookie copy is what makes the double-submit comparison possible.
    """
    secure = is_secure_request(request)
    csrf = new_csrf_token()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=expires_in,
        httponly=True,
        # Strict rather than Lax. Lax would attach the cookie to a top-level
        # navigation from another site, and this application has no flow that
        # needs one — nothing here is reached by following a link from
        # somewhere else.
        samesite="strict",
        secure=secure,
        path="/",
    )

    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=expires_in,
        # Deliberately readable by script. It is not a credential: it proves
        # the request came from a page that could read this origin's cookies,
        # which is exactly what a cross-site attacker cannot do.
        httponly=False,
        samesite="strict",
        secure=secure,
        path="/",
    )

    return csrf


def clear(response: Response) -> None:
    """Remove both cookies at sign-out.

    The path must match the one they were set with, or the browser keeps the
    originals and the user stays signed in from its point of view.
    """
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def session_token(request: Request) -> str | None:
    """The bearer token for this request, from the header or the cookie.

    The header wins. A CLI or an integration that sends one is explicitly
    naming the session it means, and a stale cookie in the same browser must
    not override it.
    """
    header = request.headers.get("authorization", "")

    if header.lower().startswith("bearer "):
        return header[7:].strip() or None

    return request.cookies.get(SESSION_COOKIE)


def authenticated_by_cookie(request: Request) -> bool:
    """Whether this request's credential came from the cookie jar."""
    header = request.headers.get("authorization", "")

    if header.lower().startswith("bearer "):
        return False

    return bool(request.cookies.get(SESSION_COOKIE))
