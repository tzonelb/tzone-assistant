"""A path id larger than the database can hold is a 404, not a 500.

FastAPI accepts any integer for an `int` path parameter -- Python integers are
unbounded -- but binding one past 2**63-1 to SQLite raises OverflowError deep in
the query. Left unhandled it becomes a 500, and a 500 leaves past the security
headers (Starlette's ServerErrorMiddleware sits outside the app's middleware).
The installed handler turns it into the same not-found answer any absent id
gets. This holds the fix through the real error-handler wiring.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.api.errors import install_error_handlers

# Just past SQLite's signed 64-bit maximum.
OVERFLOW_ID = 2**63
HUGE_ID = 99999999999999999999


def _app():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/things/{thing_id}")
    def get_thing(thing_id: int):
        # Mimic what every real handler does: bind the id to a SQLite query.
        import sqlcipher3

        conn = sqlcipher3.connect(":memory:")
        conn.execute("CREATE TABLE things (id INTEGER)")
        row = conn.execute(
            "SELECT id FROM things WHERE id = ?", (thing_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return {"id": row[0]}

    return TestClient(app, raise_server_exceptions=False)


def test_an_overflow_id_is_a_404_not_a_500():
    client = _app()
    for bad in (OVERFLOW_ID, HUGE_ID):
        resp = client.get(f"/things/{bad}")
        assert resp.status_code == 404, f"id {bad} -> {resp.status_code}: {resp.text}"


def test_an_in_range_absent_id_still_answers_404():
    client = _app()
    resp = client.get("/things/12345")
    assert resp.status_code == 404, resp.text
