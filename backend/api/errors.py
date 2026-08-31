"""The one error response the application did not write on purpose.

A validation failure is a 422 the code decided. It became a 500 it fell into
whenever the rejected body contained a number JSON cannot represent.

FastAPI's default handler for `RequestValidationError` echoes the offending
value back under `input`, so the caller can see what was refused, and then
encodes the whole error list as JSON. `1e309` in a request body parses to
`float("inf")`, and `json.dumps` refuses to write `inf` -- so encoding the
*error message* raised, inside the handler, after the request had already been
correctly rejected. One field, in one body, from any caller, on every route
that takes one.

A 500 here is worse than an ordinary bug. Starlette's `ServerErrorMiddleware`
sits outside every middleware this application installs, so an unhandled
exception is raised past the security headers: the one response nobody wrote on
purpose is also the one that leaves without them.

The fix is to make the error list encodable rather than to stop saying what was
refused. A non-finite number is replaced by its name, so the caller still sees
which value was rejected and reads something true about it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


_INFINITY = float("inf")


def json_safe(value: Any) -> Any:
    """Replace anything `json.dumps` cannot write, leaving the rest intact.

    Deliberately not a general sanitiser: it touches non-finite floats and
    nothing else, because everything else in a validation error is already
    encodable and rewriting it would change what the caller is told.
    """
    if isinstance(value, float):
        # `value != value` is the NaN test: NaN is the only value not equal to
        # itself, and `math.isnan` would be a second import for one comparison.
        if value != value:
            return "NaN"

        if value == _INFINITY:
            return "Infinity"

        if value == -_INFINITY:
            return "-Infinity"

        return value

    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]

    return value


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """The same 422 FastAPI would send, in a form that can always be sent."""
    return JSONResponse(
        # The literal rather than the constant: this Starlette renamed
        # `HTTP_422_UNPROCESSABLE_ENTITY` to `..._CONTENT` and deprecated the
        # old name, so either spelling ties this file to one version's
        # vocabulary for a number that has not changed since RFC 4918.
        status_code=422,
        content={"detail": json_safe(jsonable_encoder(exc.errors()))},
    )


async def overflow_error_handler(
    request: Request, exc: OverflowError
) -> JSONResponse:
    """A path id past the database's 64-bit integer range is a 404, not a 500.

    FastAPI accepts any integer for an `int` path parameter -- Python integers
    are unbounded -- but binding one larger than 2**63-1 to SQLite raises
    ``OverflowError`` deep in the query, which would otherwise leave as a 500
    past the security headers (see the module note above). No row can carry an
    id that large, so the honest answer is the same one an absent id already
    gets: not found.
    """
    return JSONResponse(status_code=404, content={"detail": "Not found."})


def install_error_handlers(app: FastAPI) -> None:
    """Register the handlers on an application.

    A function rather than a decorator at import time, so the same wiring can
    be applied to a test application and checked, instead of being a line in
    `main.py` that nothing verifies.
    """
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(OverflowError, overflow_error_handler)
