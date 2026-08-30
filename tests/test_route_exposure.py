"""Every route is authenticated unless it is on the list below.

Adding an endpoint is the easiest way to open a hole in this platform, because
nothing about a missing `Depends` looks wrong. The route works, the tests for
what it returns pass, and it is reachable by anybody who knows the path.

So the check is inverted: a route with no dependency at all fails this file
until somebody adds it to `PUBLIC_ROUTES` with the reason it is public. Four
routes are on that list today and each has to be — a login cannot require a
login.

The second test is the subtler one. A route may be authenticated and still
unauthorised: `Depends(get_current_user)` proves who is asking and says nothing
about whether they may. Those are listed too, with why the permission lives
somewhere else.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ROUTES_DIR = ROOT / "backend" / "api" / "routes"


# Reachable with no credentials, deliberately.
PUBLIC_ROUTES: dict[str, str] = {
    "auth.py:POST:/login": "A sign-in cannot require being signed in.",
    "auth.py:POST:/password/forgot": (
        "Asking for a reset link cannot require being signed in; the endpoint "
        "answers identically whether or not the address exists."
    ),
    "auth.py:POST:/password/reset/{token}": (
        "The token in the path is the credential. It is single-use, expiring, "
        "and stored only as a hash — the same shape as a session token."
    ),
    "platform.py:POST:/auth/login": "The console sign-in, same reason.",
    "media_uploads.py:GET:/{company_id}/{stored_name}": (
        "An attachment an employee sent to a customer. The channel — Meta, "
        "WhatsApp, Telegram — fetches this URL from its own servers with no "
        "session of ours, so a dependency here would stop every attachment "
        "from being delivered. The unguessable name is the credential: 32 hex "
        "characters from secrets.token_hex(16), checked against a strict "
        "pattern before the filesystem is touched, under a directory keyed by "
        "an integer company id."
    ),
    "health.py:GET:/": (
        "A liveness probe. It returns a constant and reads nothing, so there "
        "is nothing behind it to protect."
    ),
    # The telephony provider reporting on a call it is carrying. It holds no
    # session of ours, so a dependency here would stop every callback about
    # every call the platform placed. What stands in for the session is
    # Twilio's X-Twilio-Signature — an HMAC over the URL and every posted
    # field, keyed by TWILIO_AUTH_TOKEN — checked by `_verified_form` before
    # any field of the body is read, and answering 403 when it does not match.
    # With no token configured nothing can be verified, so nothing is: all four
    # reject everything.
    "dialer.py:POST:/voice": "A signed Twilio callback. See dialer.py.",
    "dialer.py:POST:/inbound": "A signed Twilio callback. See dialer.py.",
    "dialer.py:POST:/status": "A signed Twilio callback. See dialer.py.",
    "dialer.py:POST:/recording": "A signed Twilio callback. See dialer.py.",
}


# Authenticated with nothing but identity, and correctly so. On each of these
# the caller's identity *is* the authorisation: the route answers about the
# session's own user or the session's own company, and takes neither from a
# parameter. Anything that answers about somebody else belongs above, behind a
# permission.
IDENTITY_ONLY_ROUTES: dict[str, str] = {
    "auth.py:POST:/logout": "Ending your own session needs no permission.",
    "platform_ui.py:GET:/config": (
        "The caller's own workspace configuration, resolved from the session "
        "and never from a parameter. Every employee needs it to draw their "
        "sidebar, so a permission would make the app unusable for anyone "
        "without it."
    ),
    # Notifications are keyed on (company from the session, user from the
    # session), so a caller can only ever read and clear their own bell. The
    # router is also behind `require_module("notifications")` in `main.py`,
    # which this per-file scan cannot see.
    "notifications.py:GET:": "Your own notifications.",
    "notifications.py:GET:/summary": "Your own unread count.",
    "notifications.py:POST:/{notification_id}/read": "Your own notification.",
    "notifications.py:POST:/{notification_id}/unread": "Your own notification.",
    "notifications.py:POST:/read-all": "Your own notifications.",
    "notifications.py:DELETE:/clear-visible": "Your own notifications.",
    # A second factor is something only its holder can set up or remove, and
    # that is the whole point of it. A permission here would let an
    # administrator enrol or strip somebody else's — which would make it a
    # factor two people hold, and so not a second factor at all. Every one of
    # these acts on the session's own user id and takes none from a parameter.
    "auth.py:GET:/totp": "Your own second factor.",
    "auth.py:POST:/totp/begin": "Your own second factor.",
    "auth.py:POST:/totp/confirm": "Your own second factor.",
    "auth.py:DELETE:/totp": (
        "Your own second factor, and a current code is required to remove it — "
        "otherwise anybody at an unlocked screen could strip it in one click."
    ),
    # Two constant lists — the directions and outcomes the "log a call" form is
    # built from. They describe the software, not the company, and the screen
    # needs them to draw its dropdowns before it knows whether this employee
    # may read a single call.
    "calls.py:GET:/options": "Two constant lists; no company data.",
    # The Dialer's own state and its recent calls, both resolved from the
    # session's company and never from a parameter — the same shape as the
    # notification bell above. `/status` reports whether this deployment has a
    # phone line at all, which is a property of the server. Making a phone ring
    # is the part that is guarded: every write on this router takes
    # `dialer.use`. Both routers are also behind `require_module("dialer")` in
    # `main.py`, which this per-file scan cannot see.
    "dialer.py:GET:/status": "Whether this deployment has a phone line.",
    "dialer.py:GET:/calls": "Your own company's dialer history.",
}


# A dependency or a call that establishes the caller may do this, not merely
# who they are.
GUARDS = (
    "require_permission",
    "require_module",
    "get_platform_admin",
    # The permissive twin, used only by the enrolment routes. It still requires
    # a platform-scoped token belonging to a super admin — what it does not
    # require is the second factor those routes exist to set up.
    "get_platform_admin_enrolling",
    "get_user_changing_password",
    "_require_access_admin",
    "has_permission",
    # The Developer Center gates on the flag itself rather than on a named
    # permission: its contents are platform diagnostics, not a company feature
    # anybody could be granted.
    "is_super_admin",
)


def _signature_text(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    defaults = [
        ast.unparse(default) for default in fn.args.defaults if default is not None
    ] + [
        ast.unparse(default) for default in fn.args.kw_defaults if default is not None
    ]

    return " ".join(defaults)


def _module_functions(tree: ast.Module) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _is_guarded(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    functions: dict[str, ast.AST],
    seen: set[str] | None = None,
) -> bool:
    """Whether this route establishes authorisation, directly or through a helper.

    Routes in this codebase rarely name `require_permission` themselves. Most
    depend on a small local helper — `view_context`, `manage_context` — which
    depends on it, and a few call a guard in the body instead. All three count,
    so the resolver follows one dependency into the next rather than reading
    only the signature.
    """
    seen = seen or set()

    signature = _signature_text(fn)
    body = " ".join(
        ast.unparse(node)
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
    )

    if any(guard in signature or guard in body for guard in GUARDS):
        return True

    # Follow every local function this route depends on. `view_context` is not
    # a guard by name; what it depends on is.
    for name, node in functions.items():
        if name in seen or f"Depends({name})" not in signature:
            continue

        if _is_guarded(node, functions, seen | {name}):
            return True

    return False


def _routes():
    """Every route in the customer and console routers, with its dependencies."""
    for name in sorted(os.listdir(ROUTES_DIR)):
        if not name.endswith(".py") or name == "__init__.py":
            continue

        path = ROUTES_DIR / name
        tree = ast.parse(path.read_text())
        functions = _module_functions(tree)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            decorators = [
                item
                for item in node.decorator_list
                if isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and isinstance(item.func.value, ast.Name)
                and item.func.value.id.endswith("router")
            ]

            if not decorators:
                continue

            route = decorators[0]
            verb = route.func.attr.upper()
            url = ast.literal_eval(route.args[0]) if route.args else "?"

            decorator_args = [
                ast.unparse(keyword) for item in decorators for keyword in item.keywords
            ]

            yield {
                "key": f"{name}:{verb}:{url}",
                "file": name,
                "line": node.lineno,
                "dependencies": " ".join(
                    [_signature_text(node)] + decorator_args
                ),
                "guarded": _is_guarded(node, functions),
            }


def test_the_route_parser_finds_the_routes():
    """Without this, a change to how routes are declared would make every
    check below pass by finding nothing."""
    routes = list(_routes())

    assert len(routes) > 80, f"the parser found only {len(routes)} routes"
    assert any(route["key"] == "auth.py:POST:/login" for route in routes)


def test_no_route_is_reachable_without_a_dependency():
    """A missing `Depends` does not look wrong. The route works, its tests
    pass, and anybody who knows the path can call it."""
    exposed = [
        f"{route['file']}:{route['line']} {route['key']}"
        for route in _routes()
        if "Depends(" not in route["dependencies"]
        and route["key"] not in PUBLIC_ROUTES
    ]

    assert not exposed, (
        "Route(s) reachable with no credentials:\n"
        + "\n".join(exposed)
        + "\n\nAdd a dependency, or add it to PUBLIC_ROUTES with the reason."
    )


def test_the_exemption_lists_have_no_stale_entries():
    """An entry for a route that no longer exists is a hole somebody could
    later reopen under the same name without review."""
    live = {route["key"] for route in _routes()}

    assert not (set(PUBLIC_ROUTES) - live), (
        f"PUBLIC_ROUTES names routes that no longer exist: "
        f"{sorted(set(PUBLIC_ROUTES) - live)}"
    )
    assert not (set(IDENTITY_ONLY_ROUTES) - live), (
        f"IDENTITY_ONLY_ROUTES names routes that no longer exist: "
        f"{sorted(set(IDENTITY_ONLY_ROUTES) - live)}"
    )


def test_every_customer_route_checks_a_permission_or_is_listed():
    """Authenticated is not authorised.

    `Depends(get_current_user)` proves who is asking and says nothing about
    whether they may. A route that only identifies the caller has to say where
    its permission check actually happens.
    """
    identity_only = [
        f"{route['file']}:{route['line']} {route['key']}"
        for route in _routes()
        if "Depends(" in route["dependencies"]  # unauthenticated: the test above
        and not route["guarded"]
        and route["key"] not in IDENTITY_ONLY_ROUTES
    ]

    assert not identity_only, (
        "Route(s) that identify the caller but check no permission:\n"
        + "\n".join(identity_only)
        + "\n\nUse `require_permission`, or list it in IDENTITY_ONLY_ROUTES "
        "saying where the check happens instead."
    )
