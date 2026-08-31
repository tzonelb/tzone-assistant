"""Which company's data a request reads is decided by the session, not the caller.

This is the property the whole tenant design rests on. Every company's rows live
in its own SQLCipher file, so `database_manager.tenant(company_id)` cannot reach
another company's data no matter what row id is asked for — but only as long as
that `company_id` came from the session. A route that accepts one from a query
string, a path or a body hands the caller the choice of which file to open, and
the file-per-tenant design stops protecting anything.

Reviewing this by reading is unreliable, and this file exists because of that: a
handler written `company_id: int = Depends(view_context)` *looks* like it takes
a company from the caller and does not — FastAPI injects the value from a
dependency that reads the session. A sweep that only reads parameter names
cannot tell the two apart, and neither can a person skimming a diff. This one
inspects the default: an injected value is safe by construction, and anything
else has to be named below with the reason it is allowed.

Adding a route that reads `?company_id=` is not forbidden. Adding one without
recording why is.
"""

from __future__ import annotations

import inspect

import pytest


# A route may take a company from its caller only for one of these reasons.
#
# The platform console is the operator's, and addressing any company by id is
# its entire purpose: it runs on a platform-scope session that no company login
# can obtain (see `tests/test_platform_admin.py`), and a company session
# reaching these is refused before the handler runs.
_DASHBOARD = (
    "A member of more than one company may read any of them from the "
    "switcher, so these accept ?company_id=. `_authorized_company` then "
    "re-checks the permission against the company whose data actually leaves, "
    "not merely against the session's active one — the check that matters when "
    "a role grants dashboard.view in one company and withholds it in another."
)

ALLOWED_FROM_CALLER: dict[str, str] = {
    "GET:/api/media/{company_id}/{stored_name}": (
        "Deliberately public: the channel — Meta, WhatsApp, Telegram — fetches "
        "an attachment from its own servers with no session of ours. The "
        "credential is the unguessable stored name, 32 hex characters checked "
        "against a strict pattern before the filesystem is touched. Recorded "
        "with the same reasoning in tests/test_route_exposure.py."
    ),
    "GET:/api/dashboard/summary": _DASHBOARD,
    "GET:/api/dashboard/company": _DASHBOARD,
    "GET:/api/dashboard/channels": _DASHBOARD,
    "GET:/api/dashboard/subscription": _DASHBOARD,
    "GET:/api/dashboard/usage": _DASHBOARD,
}

_PLATFORM_CONSOLE = "The operator's console, on a platform-scope session."


def _walk(routes):
    """Every APIRoute in the app, including the ones inside included routers."""
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue

        # Starlette wraps an included router rather than flattening it, so
        # iterating app.routes alone finds almost nothing — the mistake that
        # made an earlier version of this sweep report zero routes and pass.
        original = getattr(route, "original_router", None)

        if original is not None:
            yield from _walk(original.routes)
            continue

        nested = getattr(route, "routes", None)

        if nested:
            yield from _walk(nested)


@pytest.fixture(scope="module")
def routes():
    import main

    seen = set()
    collected = []

    for route in _walk(main.app.routes):
        key = (route.path, tuple(sorted(route.methods)))

        if key in seen:
            continue

        seen.add(key)
        collected.append(route)

    return collected


def _company_parameters_from_the_caller(route):
    """Company-ish parameters this route reads from the request itself."""
    from fastapi import params

    found = []

    for name, parameter in inspect.signature(route.endpoint).parameters.items():
        if "company" not in name.lower():
            continue

        # Injected by a dependency -> the value comes from whatever that
        # dependency decided, which for every one of ours is the session.
        if isinstance(parameter.default, params.Depends):
            continue

        found.append(name)

    return found


def test_the_sweep_actually_finds_the_routes(routes):
    """A guard that silently stops looking stops guarding.

    The number is a floor, not a fixture: it only has to be large enough that a
    walk which quietly found nothing cannot pass.
    """
    assert len(routes) > 100, f"only {len(routes)} routes walked"


def test_no_route_takes_its_company_from_the_caller(routes):
    unrecorded = []

    for route in routes:
        parameters = _company_parameters_from_the_caller(route)

        if not parameters:
            continue

        # The operator's console addresses companies by id by design.
        if route.path.startswith("/api/platform"):
            continue

        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            key = f"{method}:{route.path}"

            if key not in ALLOWED_FROM_CALLER:
                unrecorded.append(f"{key}  (reads {', '.join(parameters)})")

    assert not unrecorded, (
        "A route decides which company's data to read from something the "
        "caller sent:\n  "
        + "\n  ".join(sorted(unrecorded))
        + "\n\nEvery company's rows live in its own encrypted file, so this is "
        "the one input that must never come from the request. Resolve it from "
        "the session with auth_service.resolve_company_id, or — if the route "
        "genuinely needs to accept one — re-check the permission against the "
        "requested company and record it in ALLOWED_FROM_CALLER with the "
        "reason."
    )


def test_the_dashboard_rechecks_the_permission_against_the_company_it_reads():
    """The allowance above is only sound while this holds.

    These five routes accept ?company_id=, so `require_permission` at the door —
    which checks the session's *active* company — is not the check that
    protects them.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent
        / "backend/api/routes/dashboard.py"
    ).read_text()

    assert "def _authorized_company(" in source

    # Every handler that takes the parameter has to go through it.
    handlers = source.count("company_id: int | None = Query(")
    guarded = source.count("_authorized_company(")

    assert guarded >= handlers, (
        f"{handlers} dashboard routes accept ?company_id= but only "
        f"{guarded - 1} re-check the permission against it."
    )
