"""`get_current_user` and its three siblings must never run on the event loop.

Reproduced live: under a ~160-writer stress burst against a real single-worker
server, py-spy caught the process's *main thread* -- the asyncio event loop
itself -- parked forever inside `connection.close()`, reached synchronously
from `get_current_user` -> `get_user_from_token` -> `database_manager.control()`.
Every other thread was spinning on the GIL futex, unable to make any progress
at all. Not slow: dead. Only `kill -9` recovered it, because the thing that
would fire any timeout was the thing that was stuck.

`get_current_user`, through `require_permission`, is the one dependency every
protected route in the app depends on -- unlike an ordinary blocking database
call elsewhere in a service, blocking here runs before FastAPI has even
resolved the route, directly on the loop that schedules every other
connection on this single-worker server, including ones touching no database
at all.

This does not re-run the live reproduction -- that needs a real server, real
sockets, and genuine concurrency this process's own event loop can't fake. It
pins the specific property that fix relies on: the blocking call happens on a
worker thread, never on the thread running the event loop.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend.services import auth_service as auth_service_module
from backend.services.auth_service import (
    get_current_user,
    get_platform_admin,
    get_platform_admin_enrolling,
    get_user_changing_password,
)


DEPENDENCIES = [
    get_current_user,
    get_user_changing_password,
    get_platform_admin,
    get_platform_admin_enrolling,
]


@pytest.mark.parametrize("dependency", DEPENDENCIES, ids=[d.__name__ for d in DEPENDENCIES])
def test_the_token_lookup_runs_off_the_event_loop(monkeypatch, dependency):
    calling_threads: list[threading.Thread] = []

    def fake_get_user_from_token(token):
        calling_threads.append(threading.current_thread())
        return None

    monkeypatch.setattr(
        auth_service_module.auth_service, "get_user_from_token", fake_get_user_from_token
    )

    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token")

    caller_thread = threading.current_thread()

    with pytest.raises(HTTPException) as raised:
        asyncio.run(dependency(credentials))

    # An invalid token is refused with 401 -- expected, and irrelevant here.
    # What this test exists to check is *which thread* rejected it.
    assert raised.value.status_code == 401

    assert len(calling_threads) == 1, (
        "get_user_from_token was not called exactly once -- this test's own "
        "assumption about the dependency's shape is stale."
    )

    assert calling_threads[0] is not caller_thread, (
        f"{dependency.__name__} called auth_service.get_user_from_token on "
        "the same thread that is running the event loop. That is the exact "
        "shape of the live freeze this test exists to catch: a blocking "
        "database open/close reached synchronously from a dependency that "
        "runs before FastAPI has even resolved the route, on the one loop "
        "every other connection on this single-worker server also depends "
        "on to be scheduled at all. It must go through "
        "starlette.concurrency.run_in_threadpool (or equivalent), not be "
        "called directly."
    )
