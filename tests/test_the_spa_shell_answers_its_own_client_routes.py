"""The built single-page app is the fallback for any path the API does not own.

`main.py` mounts the built frontend last, so any GET that no API router claims
falls through to it -- and _SinglePageApp is supposed to answer with the app
shell (`index.html`) rather than a bare 404, because the path is a client-side
route the browser router will resolve once the shell has loaded.

`_API_PREFIXES` used to list "conversations" and "knowledge" as reserved
prefixes on the theory that those belong to the API -- but the backend's own
conversations router is mounted at bare `/conversations`, and its list
endpoint is `GET /conversations/` (with a trailing slash). A request for the
frontend's own `/conversations` page (no trailing slash, matching nothing in
that router) never reaches a real handler, so it falls through to this
fallback -- where the prefix check refused to serve the shell for exactly the
one class of request the docstring says it exists to serve. `/knowledge` is
the same story, redundant with "api/" besides. A direct visit, a bookmark, or
a browser refresh on either screen showed a bare `{"detail":"Not Found"}"`
JSON page instead of the app.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import main


client = TestClient(main.app, raise_server_exceptions=False)


def _looks_like_the_app_shell(response) -> bool:
    return response.status_code == 200 and "text/html" in response.headers.get(
        "content-type", ""
    )


def test_the_conversations_screen_serves_the_app_shell_on_a_direct_visit():
    """A refresh, bookmark, or shared link to /conversations must not show
    raw JSON -- the whole point of a client-side route."""
    response = client.get("/conversations")

    assert _looks_like_the_app_shell(response), response.text[:200]


def test_the_knowledge_screen_serves_the_app_shell_on_a_direct_visit():
    response = client.get("/knowledge")

    assert _looks_like_the_app_shell(response), response.text[:200]


def test_an_unmatched_api_path_still_answers_json_not_the_app_shell():
    """The fallback is for *client* routes. A wrong path under `/api/` is a
    caller's mistake, and answering it with an HTML page would only hide
    that mistake from an API client expecting JSON."""
    response = client.get("/api/this-endpoint-does-not-exist")

    assert response.status_code == 404
    assert "application/json" in response.headers.get("content-type", "")
