"""A preview runs the real model behind a blocking call. Unbounded, a burst of
previews from one company holds every worker thread for the model's round trip
and freezes login and the inbox for every other company. The endpoint caps how
many run at once and refuses the rest with 429 rather than queueing them.
"""

from __future__ import annotations

import sys
import threading

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


def test_excess_previews_are_shed_with_429(wired, alpha, monkeypatch):
    from config.settings import config
    import backend.api.routes.ai_teaching as route
    from backend.services.bot_profile_service import bot_profile_service

    monkeypatch.setattr(config, "AI_PREVIEW_MAX_CONCURRENCY", 2)

    release = threading.Event()
    entered = threading.Semaphore(0)

    def _blocking_preview(**_kwargs):
        entered.release()          # signal a slot is occupied
        release.wait(timeout=10)   # hold the slot until the test lets go
        return {"reply": "ok", "buttons": []}

    monkeypatch.setattr(bot_profile_service, "preview_reply", _blocking_preview)

    client = _client(alpha["id"])
    results: list[int] = []

    def _fire():
        r = client.post("/api/ai-teaching/dry-run", json={"message": "hello"})
        results.append(r.status_code)

    # Occupy both slots.
    holders = [threading.Thread(target=_fire) for _ in range(2)]
    for t in holders:
        t.start()

    # Wait until both are actually inside the blocking call (slots taken).
    assert entered.acquire(timeout=5)
    assert entered.acquire(timeout=5)

    # A third preview, arriving while both slots are held, must be refused.
    third = client.post("/api/ai-teaching/dry-run", json={"message": "hello"})
    assert third.status_code == 429, third.text

    # Let the held previews finish; they succeed, and the counter frees up.
    release.set()
    for t in holders:
        t.join(timeout=10)
    assert results == [200, 200], results

    # With the slots free again, a new preview is accepted.
    assert route._preview_inflight == 0
    after = client.post("/api/ai-teaching/dry-run", json={"message": "hello"})
    assert after.status_code == 200, after.text
