"""Every path a screen asks for is a path the API serves.

The other half of `test_route_exposure.py`. That file asks whether a route is
guarded; this one asks whether the route a screen calls exists at all.

The failure it guards against has no symptom until somebody clicks. The build
succeeds, the lint passes, every backend test passes, and the button returns
404 to one employee on one screen — which is the same shape as the defects this
audit kept finding, arriving from the client side: a control that is offered and
does nothing.

It is a real risk here rather than a theoretical one. Route prefixes have moved
during this work, and one of them moved twice; the API modules build their paths
by string concatenation from a `BASE` constant, so a prefix that changes on the
server produces no error anywhere on the way to the browser.

The check is deliberately loose in one direction and strict in the other. A
path the frontend never calls is not reported: this API is not only consumed by
this frontend, and an unused endpoint is not a defect. A path the frontend calls
and the API does not serve always is.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

FRONTEND = ROOT / "frontend" / "src"

# A template placeholder in a URL is one of two things, and which one *is*
# decidable from where it sits:
#
#   `${BASE}/${id}/approve`  — a path segment. Anything follows it.
#   `${BASE}${query}`        — a query string the caller built, including its
#                              own `?`. Nothing follows it, and no `/` precedes
#                              it.
#
# Getting this wrong is not academic. An earlier version tried both readings for
# every placeholder, and the query reading truncates the path at the first one —
# so `/api/scheduler/${id}/approve-it` was accepted because `/api/scheduler`
# exists. Every parameterised path in the frontend, which is most of them, was
# being checked against its own prefix. The test passed, reported 102 paths, and
# would have caught almost nothing.
PLACEHOLDER = "\x00"


def _served() -> list[re.Pattern[str]]:
    """Every path the application actually serves, as a matcher.

    Taken from the OpenAPI schema rather than from the source, so a route that
    exists but is never included in a router is correctly reported as missing.
    """
    os.environ.setdefault("TZONE_MASTER_KEY", _a_master_key())

    import main

    return [
        re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", path) + "$")
        for path in main.app.openapi()["paths"]
    ]


def _a_master_key() -> str:
    from backend.security.keyring import generate_master_key

    return generate_master_key()


# Most calls do not name the prefix. A module declares
#
#     const BASE = "/api/scheduler";
#
# and then requests `${BASE}/${postId}/approve`, so the string in the source
# starts with `${` and never contains `/api/` at all. Reading only the literals
# would leave the majority of the surface unchecked while looking thorough,
# which is the shape of defect this whole audit is about.
#
# So the constants are resolved first, and a template literal beginning with one
# of them is expanded before it is compared.
BASE_CONSTANT = re.compile(r'const\s+(\w+)\s*=\s*["\'`](/api/[^"\'`]*)["\'`]')

TEMPLATE_CALL = re.compile(r'`\$\{(\w+)\}([^`]*)`')


def _called() -> dict[str, str]:
    """Every path the frontend actually requests, and the file that requests it.

    Two earlier versions of this were vacuous, both found by mutation rather
    than by reading:

    * the first exempted any path with few enough slashes, which meant renaming
      `/api/tasks` to `/api/task-list` passed;
    * the second collected only strings starting with `/api/`, which meant every
      `${BASE}/...` call — the majority — was never looked at.
    """
    found: dict[str, str] = {}

    for path in FRONTEND.rglob("*.js"):
        source = path.read_text()
        where = str(path.relative_to(ROOT))
        prefixes = dict(BASE_CONSTANT.findall(source))

        # Literal paths, minus the constants themselves: a prefix is never
        # requested on its own, every call appends to it.
        for match in re.finditer(r'["\'`](/api/[^"\'`]*)', source):
            raw = match.group(1)

            if raw in prefixes.values():
                continue

            found.setdefault(raw, where)

        # Template literals built from one of this module's own constants.
        for name, rest in TEMPLATE_CALL.findall(source):
            if name not in prefixes:
                continue

            if not rest:
                continue

            found.setdefault(prefixes[name] + rest, where)

    return found


def _collapse(raw: str) -> str:
    """Replace every `${...}` with one placeholder, counting nested braces.

    `${createQueryString({ days })}` is one expression, not one that ends at the
    first `}`. A regex that stops there leaves ` })` in the path and reports a
    working call as broken.
    """
    out: list[str] = []
    index = 0

    while index < len(raw):
        if raw.startswith("${", index):
            depth = 1
            cursor = index + 2

            while cursor < len(raw) and depth:
                if raw[cursor] == "{":
                    depth += 1
                elif raw[cursor] == "}":
                    depth -= 1
                cursor += 1

            out.append(PLACEHOLDER)
            index = cursor
            continue

        out.append(raw[index])
        index += 1

    return "".join(out)


def _readings(raw: str) -> list[str]:
    """The path(s) this call could be asking for.

    Usually exactly one. A trailing placeholder with no `/` before it is the
    only ambiguous case — it may be a query string the caller assembled — so
    that one alone yields a second reading.
    """
    collapsed = _collapse(raw).split("?")[0]

    readings = [collapsed.replace(PLACEHOLDER, "value").rstrip("/")]

    if (
        collapsed.endswith(PLACEHOLDER)
        and not collapsed.endswith("/" + PLACEHOLDER)
    ):
        readings.append(collapsed[: -len(PLACEHOLDER)].rstrip("/"))

    return readings


def test_the_served_paths_can_be_read():
    """Without this, an application that failed to build its schema would make
    the check below pass by serving nothing to compare against."""
    assert len(_served()) > 100


def test_the_frontend_calls_can_be_read():
    calls = _called()

    assert len(calls) > 50
    assert any(path.endswith("client.js") for path in calls.values())


def test_every_path_a_screen_calls_is_served():
    served = _served()
    unmatched = []

    for raw, where in sorted(_called().items()):
        readings = _readings(raw)

        if any(
            pattern.match(reading) for pattern in served for reading in readings
        ):
            continue

        unmatched.append(f"{raw}  [{where}]")

    assert not unmatched, (
        "A screen calls a path the API does not serve:\n  "
        + "\n  ".join(unmatched)
        + "\n\nThe build succeeds and the button returns 404 to whoever clicks "
        "it. Fix the path, or add the route."
    )
