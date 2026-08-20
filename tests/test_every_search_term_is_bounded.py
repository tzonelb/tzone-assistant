"""Every free-text query parameter carries a length bound.

Seven of the eight `search` parameters on the platform declared `max_length`
and one did not, which is the shape a rule takes just before it stops being
one. The odd one out was `GET /api/customers`, whose term is interpolated into
a `LIKE '%…%'` pattern across seven columns, one of them behind a subquery.

Honest about what this is: no exploit was demonstrated for the unbounded
version. SQLite matched a pattern of twenty thousand wildcards against three
thousand seeded customers in twelve milliseconds, so the missing bound was not
a way to pin a core. It is worth holding anyway, because the bound is what
stops the question being reopened every time a new consumer of that term is
written — and the next one may not be as forgiving as `LIKE`.

The test walks the routers rather than naming the endpoints, so a search
parameter added tomorrow is covered the day it is added.
"""

from __future__ import annotations

import pytest

# Names that carry a caller's free text into a query. Not every string
# parameter: `channel` and `status` are enumerations validated elsewhere, and
# bounding them would say nothing.
FREE_TEXT = ("search", "q", "term", "query")


def _routers():
    from backend.api.routes import (
        activity, ai_teaching, analytics, appointments, catalogue, channels,
        comments, company_settings, conversations, customers, dashboard,
        knowledge, notifications, platform, roles, scheduler, team_chat,
        tickets,
    )

    modules = (
        activity, ai_teaching, analytics, appointments, catalogue, channels,
        comments, company_settings, conversations, customers, dashboard,
        knowledge, notifications, platform, roles, scheduler, team_chat,
        tickets,
    )

    for module in modules:
        for attribute in ("router", "tasks_router"):
            router = getattr(module, attribute, None)

            if router is not None:
                yield module.__name__, router


def _free_text_parameters():
    """Every free-text query parameter, read off the live routers.

    The dependant tree, not the source text: a scan of the source would have to
    guess at aliases and defaults, and this file exists because a rule was
    applied seven times out of eight by eye.
    """
    found = []

    for module_name, router in _routers():
        for route in router.routes:
            dependant = getattr(route, "dependant", None)

            if dependant is None:
                continue

            for parameter in dependant.query_params:
                if parameter.name not in FREE_TEXT:
                    continue

                found.append((module_name, route.path, parameter))

    return found


def test_the_sweep_finds_the_parameters_it_is_meant_to_check():
    """Coverage asserted, not assumed: a sweep that found nothing would pass."""
    found = _free_text_parameters()

    assert len(found) >= 8, (
        f"only {len(found)} free-text query parameters were found, so this "
        "file is no longer checking what it claims to"
    )


def test_every_free_text_query_parameter_has_a_maximum_length():
    unbounded = []

    for module_name, path, parameter in _free_text_parameters():
        metadata = getattr(parameter.field_info, "metadata", []) or []
        limits = [
            getattr(entry, "max_length", None)
            for entry in metadata
            if getattr(entry, "max_length", None) is not None
        ]

        if not limits:
            unbounded.append(f"{module_name} {path} ({parameter.name})")

    assert not unbounded, (
        "these carry a caller's free text into a query with no length bound: "
        + ", ".join(unbounded)
    )


@pytest.mark.parametrize("field", ["search"])
def test_an_over_long_term_is_refused_rather_than_run(field):
    """The bound has to be enforced, not merely declared."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import customers

    app = FastAPI()
    app.include_router(customers.router)

    # The permission guard runs before validation, so an unauthenticated
    # request is refused at 401 and never reaches the length check — which
    # would make this test pass without testing anything. Overriding it puts
    # validation on the path with no database and no session to build.
    app.dependency_overrides[customers.view_context] = lambda: (1, 1)

    client = TestClient(app, raise_server_exceptions=False)

    long_term = client.get("/api/customers", params={field: "a" * 5_000})

    assert long_term.status_code == 422, (
        f"a 5,000-character search term was not refused: "
        f"{long_term.status_code} {long_term.text[:200]}"
    )

    # And the control: a term inside the bound gets past validation, so the
    # assertion above is about the length and not about the override.
    short_term = client.get("/api/customers", params={field: "a" * 10})

    assert short_term.status_code != 422, (
        f"an ordinary search term was refused too: {short_term.text[:200]}"
    )
