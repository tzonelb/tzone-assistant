"""A preview runs the real model behind a blocking call. Unbounded, a burst of
previews from one company holds every worker thread for the model's round trip
and freezes login and the inbox for every other company. The endpoint caps how
many run at once and refuses the rest with 429 rather than queueing them.

Two properties, and the second is the one that would actually take a deployment
down: above the cap the request is refused, and a slot is *always* given back.
A counter that leaks one slot per failed preview needs only
``AI_PREVIEW_MAX_CONCURRENCY`` model errors before the endpoint refuses
everything, for everyone, until the process is restarted -- and nothing in the
logs would say why.

This used to be driven by three concurrent HTTP requests racing on a semaphore
with a five-second deadline. That harness was unsound in a way worth recording,
because it is easy to write again: a `TestClient` used outside a `with` block
starts a *new* blocking portal, meaning a new event loop in a new thread, for
every request. The endpoint's own comment explains that its check-and-increment
needs no lock because the event loop is single-threaded -- which is true of the
server and false of that test, so the harness violated the premise it was
checking. It also cost real time, depended on thread startup finishing inside an
arbitrary five seconds, and failed on a loaded CI runner while passing locally.
And for all that, it never once exercised the failure path above.

So the cap is now driven directly. There is nothing about "two at once" that the
counter can tell apart from "two"; concurrency was the mechanism, never the
property.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    from database.manager import DatabaseManager
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)
        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)
    return test_manager


def _client(company_id):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes import ai_teaching

    app = FastAPI()
    app.include_router(ai_teaching.router)
    app.dependency_overrides[ai_teaching.manage_context] = lambda: company_id
    return TestClient(app)


@pytest.fixture()
def route(monkeypatch):
    """The module, with its in-flight counter restored afterwards.

    It is a module global, so a test that leaves it raised refuses every
    preview in every test that follows -- the same failure this file exists to
    prevent, arriving inside the suite.
    """
    import backend.api.routes.ai_teaching as module

    original = module._preview_inflight
    yield module
    module._preview_inflight = original


def test_a_preview_is_refused_once_the_cap_is_reached(wired, alpha, route, monkeypatch):
    from config.settings import config
    from backend.services.bot_profile_service import bot_profile_service

    monkeypatch.setattr(config, "AI_PREVIEW_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(
        bot_profile_service,
        "preview_reply",
        lambda **_: {"reply": "ok", "buttons": []},
    )

    client = _client(alpha["id"])

    # One slot of two taken: still served.
    route._preview_inflight = 1
    assert client.post("/api/ai-teaching/dry-run", json={"message": "hi"}).status_code == 200

    # Both taken: refused, and refused rather than queued -- the response comes
    # back immediately instead of the caller waiting for a slot.
    route._preview_inflight = 2
    refused = client.post("/api/ai-teaching/dry-run", json={"message": "hi"})

    assert refused.status_code == 429, refused.text
    assert "too many previews" in refused.json()["detail"].lower()


def test_the_slot_is_given_back_when_the_preview_succeeds(wired, alpha, route, monkeypatch):
    from config.settings import config
    from backend.services.bot_profile_service import bot_profile_service

    monkeypatch.setattr(config, "AI_PREVIEW_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(
        bot_profile_service,
        "preview_reply",
        lambda **_: {"reply": "ok", "buttons": []},
    )

    client = _client(alpha["id"])
    route._preview_inflight = 0

    for _ in range(5):
        assert client.post("/api/ai-teaching/dry-run", json={"message": "hi"}).status_code == 200

    assert route._preview_inflight == 0


@pytest.mark.parametrize(
    "failure",
    [
        # The refusal the endpoint knows about, and turns into a 400.
        "known",
        # Anything else the model layer can raise -- a timeout, a transport
        # error, a bug. The endpoint does not catch these, and the slot has to
        # come back anyway.
        "unexpected",
    ],
)
def test_the_slot_is_given_back_when_the_preview_fails(
    wired, alpha, route, monkeypatch, failure
):
    from config.settings import config
    from backend.services.bot_profile_service import (
        BotProfileError,
        bot_profile_service,
    )

    monkeypatch.setattr(config, "AI_PREVIEW_MAX_CONCURRENCY", 2)

    def _raise(**_):
        raise (
            BotProfileError("no profile")
            if failure == "known"
            else TimeoutError("the model did not answer")
        )

    monkeypatch.setattr(bot_profile_service, "preview_reply", _raise)

    client = _client(alpha["id"])
    route._preview_inflight = 0

    for _ in range(3):
        try:
            response = client.post("/api/ai-teaching/dry-run", json={"message": "hi"})
        except TimeoutError:
            # An unhandled error propagates through TestClient rather than
            # becoming a 500. Either way the slot must already be released.
            pass
        else:
            assert response.status_code == 400, response.text

    assert route._preview_inflight == 0, (
        "A failed preview kept its slot. After "
        f"{config.AI_PREVIEW_MAX_CONCURRENCY} model errors the endpoint would "
        "refuse every preview, for every company, until the process restarts."
    )
