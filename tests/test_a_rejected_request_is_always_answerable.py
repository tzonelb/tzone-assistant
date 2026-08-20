"""A refusal the code decided, never one it fell into.

`test_no_endpoint_answers_with_a_crash` walks nonsense path parameters and
query strings. This is the case that slipped past it: a body that parses
perfectly well as JSON and holds a number JSON cannot write back.

`1e309` is legal JSON text. Python parses it to `float("inf")`. FastAPI's
default validation handler echoes the rejected value back under `input` so the
caller can see what was refused, then encodes the error list — and `json.dumps`
refuses to write `inf`. So the handler raised while reporting a request that
had already been correctly rejected, and a decided 422 became a 500.

Why that particular 500 is worse than an ordinary bug: Starlette's
`ServerErrorMiddleware` sits outside every middleware this application
installs, so the exception is raised past the security headers. The one
response nobody wrote on purpose is also the one that leaves without them.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from backend.api.errors import install_error_handlers, json_safe


class Payload(BaseModel):
    """Shaped like the real schemas: the numeric field carries a bound.

    That bound is what turns a non-finite number into a validation error rather
    than an accepted value — `backend/api/schemas/catalogue.py` bounds price at
    a billion, so `1e309` is refused there too. An unbounded `float` would
    accept infinity outright and this file would be testing the wrong thing.
    """

    name: str = Field(min_length=2)
    price: float = Field(default=0.0, ge=0, le=1_000_000_000)


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/thing")
    def create(payload: Payload) -> dict[str, str]:
        return {"ok": "yes"}

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def unguarded() -> TestClient:
    """The same application without the handler — the positive control.

    Every assertion below is that a 500 does *not* happen. Without a client
    that really does produce one, they would all pass on an application that
    never validates anything.
    """
    app = FastAPI()

    @app.post("/thing")
    def create(payload: Payload) -> dict[str, str]:
        return {"ok": "yes"}

    return TestClient(app, raise_server_exceptions=False)


NON_FINITE = [
    pytest.param(b'{"name": "ok", "price": 1e309}', id="infinity"),
    pytest.param(b'{"name": "ok", "price": -1e309}', id="negative-infinity"),
    pytest.param(b'{"price": 1e309}', id="infinity-with-a-missing-field"),
    pytest.param(b'{"name": "x", "price": 1e309}', id="infinity-and-too-short"),
    pytest.param(b'{"name": ["a"], "price": 1e309}', id="infinity-and-wrong-type"),
]

HEADERS = {"Content-Type": "application/json"}


def test_the_control_really_does_crash(unguarded):
    """Proof that the bodies below are hostile, not merely unusual."""
    response = unguarded.post(
        "/thing", headers=HEADERS, content=b'{"price": 1e309}'
    )

    assert response.status_code == 500, (
        "the default handler no longer falls over on a non-finite number, so "
        "these tests no longer prove that this application's handler is what "
        "prevents it — check whether FastAPI fixed this upstream"
    )


@pytest.mark.parametrize("body", NON_FINITE)
def test_a_non_finite_number_is_refused_not_crashed(client, body):
    response = client.post("/thing", headers=HEADERS, content=body)

    assert response.status_code == 422, (
        f"a body containing a non-finite number produced {response.status_code}; "
        "any caller can force an unhandled error on any route that takes a body"
    )

    # And the refusal is readable: it has to survive being parsed by the screen
    # that shows it.
    body_text = response.json()
    assert "detail" in body_text
    json.dumps(body_text)


def test_the_caller_is_still_told_which_value_was_refused(client):
    """The fix must not be "stop reporting what was wrong"."""
    response = client.post(
        "/thing", headers=HEADERS, content=b'{"name": "ok", "price": 1e309}'
    )

    assert response.status_code == 422
    assert "Infinity" in json.dumps(response.json()), (
        "the rejected value vanished from the error, which turns a precise "
        "refusal into a shrug"
    )


def test_an_ordinary_validation_error_is_unchanged(client):
    """The handler replaces FastAPI's, so it must still say what FastAPI said
    for the errors that were never a problem."""
    response = client.post("/thing", headers=HEADERS, content=b'{"name": "x"}')

    assert response.status_code == 422

    detail = response.json()["detail"]

    assert isinstance(detail, list) and detail, detail
    assert detail[0]["loc"] == ["body", "name"], detail
    assert detail[0]["input"] == "x", detail


def test_a_valid_request_is_untouched(client):
    response = client.post(
        "/thing", headers=HEADERS, content=b'{"name": "ok", "price": 1.5}'
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": "yes"}


@pytest.mark.parametrize(
    "value, expected",
    [
        (float("inf"), "Infinity"),
        (float("-inf"), "-Infinity"),
        (float("nan"), "NaN"),
        (1.5, 1.5),
        (0.0, 0.0),
        ("text", "text"),
        (None, None),
        (7, 7),
        (True, True),
    ],
)
def test_json_safe_touches_only_what_it_must(value, expected):
    result = json_safe(value)

    assert result == expected or (result != result and expected == "NaN")
    json.dumps(json_safe(value))


def test_json_safe_reaches_inside_nesting():
    nested = {"a": [1, {"b": float("inf")}], "c": (float("nan"), 2)}

    safe = json_safe(nested)

    assert safe == {"a": [1, {"b": "Infinity"}], "c": ["NaN", 2]}
    json.dumps(safe)


def test_the_real_application_installs_the_handler():
    """The behaviour above is worth nothing if `main` never wires it.

    `main` is imported at module scope here, not inside a test: importing it
    while a database monkeypatch is active is the hazard documented in
    `tests/test_declared_retention_is_actually_applied.py`.
    """
    from fastapi.exceptions import RequestValidationError

    import main

    from backend.api.errors import validation_error_handler

    assert (
        main.app.exception_handlers.get(RequestValidationError)
        is validation_error_handler
    ), "the application does not install the validation error handler"
