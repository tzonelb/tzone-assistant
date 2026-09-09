"""CORS_ORIGINS=* must refuse to start, not silently reflect any origin.

Found reading Starlette's own CORSMiddleware source, not by clicking anything:
once allow_credentials=True (which main.py always sets), a "*" in
allow_origins does not send a literal wildcard -- browsers refuse to honour
that on a credentialed request -- it makes Starlette reflect whatever Origin
header the request actually carried, with
Access-Control-Allow-Credentials: true attached. For a cookie-authenticated
API that is not "CORS disabled", it is CORS actively telling every browser
that any site may make a credentialed request and read the response. Writes
stay behind the CSRF cookie regardless, but every GET a signed-in employee's
browser can reach -- their whole inbox included -- would be readable from any
page they happen to visit.

Tested directly against the small function main.py calls at import time,
not by reloading the whole module (which the rest of the suite has already
imported once and depends on staying as it is).
"""

from __future__ import annotations

import pytest

from main import forbid_wildcard_cors_with_credentials


def test_a_bare_wildcard_is_refused():
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        forbid_wildcard_cors_with_credentials(["*"])


def test_a_wildcard_alongside_real_origins_is_still_refused():
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        forbid_wildcard_cors_with_credentials(
            ["https://app.example.com", "*"]
        )


def test_real_origins_alone_are_accepted():
    forbid_wildcard_cors_with_credentials(
        ["https://app.example.com", "http://localhost:5173"]
    )


def test_an_empty_list_is_accepted():
    forbid_wildcard_cors_with_credentials([])
