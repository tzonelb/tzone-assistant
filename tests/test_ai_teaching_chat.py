"""TRAIN — the manager's teaching chat, and what it actually changes.

This screen makes one promise that is easy to fake: it tells a manager "Saved
as a new instruction". A chat that answers pleasantly and stores the answer in
a table nothing reads would pass every plausible smoke test and change no
customer reply for ever. So the tests here are about the promise rather than
the transport:

* a taught instruction reaches the string the assistant is actually built from;
* the transcript is the company's own, and is not a customer conversation;
* a model that is down loses the turn's answer, not the manager's message.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the teaching chat at the test platform's databases.

    The same sweep `test_ai_teaching.py` uses, and for the same reason: a
    module that did `from database.manager import database_manager` holds its
    own reference, and one left unrebound runs the test against the real
    process-wide singleton.
    """
    import sys

    import database.manager as manager_module

    import backend.api.routes.ai_teaching  # noqa: F401
    import backend.services.ai_teaching_chat_service  # noqa: F401
    import backend.services.bot_profile_service  # noqa: F401
    import core.prompt_builder  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.ai_teaching_chat_service" in rebound
    assert "backend.services.bot_profile_service" in rebound

    from backend.services.ai_teaching_chat_service import ai_teaching_chat_service

    return ai_teaching_chat_service


@pytest.fixture()
def model_configured(monkeypatch):
    """A key and the assistant switched on, so the service gets as far as the
    HTTP call the tests below stand in for."""
    from config.settings import config

    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

    return config


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _answers(service, monkeypatch, reply, instruction):
    """Stand in for the one HTTP call, the way the service was shaped to allow.

    Patching the single `_post_to_model` seam rather than httpx itself matters:
    the FastAPI test client is httpx too, so a global patch would break the
    route tests in the same run.
    """
    body = json.dumps({"reply": reply, "instruction": instruction})

    monkeypatch.setattr(
        type(service),
        "_post_to_model",
        staticmethod(lambda payload, headers: _FakeResponse({"output_text": body})),
    )


# ----------------------------------------------------------------------
# The promise
# ----------------------------------------------------------------------


def test_a_taught_instruction_reaches_the_assistants_prompt(
    service, alpha, monkeypatch, model_configured
):
    """The whole point. An instruction stored anywhere the prompt builder does
    not read is a screen reporting a change no customer will ever see."""
    from core.prompt_builder import prompt_builder

    _answers(service, monkeypatch, "Got it.", "Always greet customers in Arabic first")

    result = service.send_message(
        company_id=alpha["id"],
        actor_user_id=1,
        text="please always greet people in arabic before anything else",
    )

    assert result["instruction_saved"] is True
    assert result["instruction_text"] == "Always greet customers in Arabic first"

    prompt = prompt_builder.build_system_prompt("messenger", company_id=alpha["id"])
    assert "Always greet customers in Arabic first" in prompt


def test_the_same_instruction_twice_is_not_stored_twice(
    service, alpha, monkeypatch, model_configured
):
    """A duplicate costs tokens on every customer message for ever and teaches
    the model nothing, so the second one reports honestly that it saved
    nothing rather than silently appending."""
    from backend.services.bot_profile_service import bot_profile_service

    _answers(service, monkeypatch, "Got it.", "Never quote a price over chat")

    first = service.send_message(
        company_id=alpha["id"], actor_user_id=1, text="never quote prices"
    )
    second = service.send_message(
        company_id=alpha["id"], actor_user_id=1, text="never quote prices"
    )

    assert first["instruction_saved"] is True
    assert second["instruction_saved"] is False

    prompt = bot_profile_service.get_default(alpha["id"])["system_prompt"]
    assert prompt.count("Never quote a price over chat") == 1


def test_plain_chat_saves_no_instruction(
    service, alpha, monkeypatch, model_configured
):
    """"Never invent an instruction that wasn't actually given" has to hold at
    this layer too: a `null` from the model must not become a stored rule."""
    from backend.services.bot_profile_service import bot_profile_service

    before = bot_profile_service.get_default(alpha["id"])["system_prompt"]

    _answers(service, monkeypatch, "Sure — what would you like to change?", None)

    result = service.send_message(
        company_id=alpha["id"], actor_user_id=1, text="hello, are you there?"
    )

    assert result["instruction_saved"] is False
    assert bot_profile_service.get_default(alpha["id"])["system_prompt"] == before


# ----------------------------------------------------------------------
# The transcript
# ----------------------------------------------------------------------


def test_the_transcript_is_not_a_customer_conversation(
    service, platform, alpha, monkeypatch, model_configured
):
    """Nothing here was said to or by a customer. A turn written into
    `messages` would appear in the inbox, in exports, in analytics and in the
    retention sweep as though it had been."""
    _answers(service, monkeypatch, "Got it.", None)

    service.send_message(
        company_id=alpha["id"], actor_user_id=1, text="be brief with everyone"
    )

    with platform["manager"].tenant(alpha["id"]) as conn:
        assert (
            int(conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]) == 0
        )
        assert (
            int(
                conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
            )
            == 0
        )
        assert (
            int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_teaching_messages"
                ).fetchone()["n"]
            )
            == 2
        )


def test_one_companys_teaching_chat_is_not_anothers(
    service, alpha, beta, monkeypatch, model_configured
):
    """The transcript names how a business wants to be run. Two companies
    sharing one is the leak this platform's per-company files exist to make
    impossible; this checks the service actually opens the right one."""
    _answers(service, monkeypatch, "Got it.", None)

    service.send_message(
        company_id=alpha["id"], actor_user_id=1, text="alpha only: mention the branch"
    )

    assert [row["text"] for row in service.list_messages(company_id=beta["id"])] == []

    alpha_texts = [row["text"] for row in service.list_messages(company_id=alpha["id"])]
    assert "alpha only: mention the branch" in alpha_texts


def test_the_transcript_reads_back_oldest_first(
    service, alpha, monkeypatch, model_configured
):
    """The screen renders the list top to bottom as a chat log; reversed, the
    conversation reads backwards."""
    _answers(service, monkeypatch, "Got it.", None)

    service.send_message(company_id=alpha["id"], actor_user_id=1, text="first")
    service.send_message(company_id=alpha["id"], actor_user_id=1, text="second")

    texts = [row["text"] for row in service.list_messages(company_id=alpha["id"])]

    assert texts.index("first") < texts.index("second")
    assert [row["role"] for row in service.list_messages(company_id=alpha["id"])][0] == (
        "manager"
    )


# ----------------------------------------------------------------------
# When the model is not there
# ----------------------------------------------------------------------


def test_a_model_failure_keeps_what_the_manager_typed(
    service, platform, alpha, monkeypatch, model_configured
):
    """A timeout must not lose the manager's message, and must not answer with
    a 500 — the turn degrades to something they can read and retry."""

    def _explode(payload, headers):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(type(service), "_post_to_model", staticmethod(_explode))

    result = service.send_message(
        company_id=alpha["id"], actor_user_id=1, text="always confirm the address"
    )

    assert result["error"]
    assert result["instruction_saved"] is False
    assert result["manager_message"]["text"] == "always confirm the address"
    assert result["assistant_message"]["text"]

    with platform["manager"].tenant(alpha["id"]) as conn:
        stored = [
            row["text"]
            for row in conn.execute(
                "SELECT text FROM ai_teaching_messages ORDER BY id"
            ).fetchall()
        ]

    assert stored[0] == "always confirm the address"


def test_no_model_configured_is_refused_not_crashed(service, alpha, monkeypatch):
    """With no key the screen has to say so. The old failure mode here was a
    502 from an upstream that was never called."""
    from config.settings import config

    from backend.services.ai_teaching_chat_service import AITeachingChatError

    monkeypatch.setattr(config, "OPENAI_API_KEY", "")

    with pytest.raises(AITeachingChatError):
        service.send_message(company_id=alpha["id"], actor_user_id=1, text="hello")


def test_an_empty_message_is_refused(service, alpha, model_configured):
    with pytest.raises(Exception):
        service.send_message(company_id=alpha["id"], actor_user_id=1, text="   ")


# ----------------------------------------------------------------------
# The HTTP surface
# ----------------------------------------------------------------------


def _client(company_id: int):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import ai_teaching

    app = FastAPI()
    app.include_router(ai_teaching.router)
    app.dependency_overrides[ai_teaching.view_context] = lambda: company_id
    app.dependency_overrides[ai_teaching.manage_context] = lambda: company_id
    app.dependency_overrides[ai_teaching.manage_actor] = lambda: {
        "company_id": company_id,
        "actor_user_id": 1,
        "user": {"id": 1, "full_name": "Test Actor"},
    }

    return TestClient(app)


def test_the_routes_read_and_write_the_transcript(
    service, alpha, monkeypatch, model_configured
):
    """A service that works behind a router that does not is a screen whose
    Send button does nothing."""
    _answers(service, monkeypatch, "Understood.", "Offer the loyalty discount")

    client = _client(alpha["id"])

    empty = client.get("/api/ai-teaching/teaching-chat")
    assert empty.status_code == 200
    assert empty.json()["items"] == []

    sent = client.post(
        "/api/ai-teaching/teaching-chat",
        json={"text": "mention the loyalty discount when someone asks about price"},
    )
    assert sent.status_code == 201, sent.text
    body = sent.json()
    assert body["manager_message"]["role"] == "manager"
    assert body["assistant_message"]["text"] == "Understood."
    assert body["assistant_message"]["instruction_saved"] is True

    listed = client.get("/api/ai-teaching/teaching-chat")
    assert [row["role"] for row in listed.json()["items"]] == ["manager", "assistant"]


def test_the_routes_refuse_a_caller_without_a_token(service, alpha):
    """This chat edits what the assistant tells every customer and spends a
    model call doing it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import ai_teaching

    app = FastAPI()
    app.include_router(ai_teaching.router)
    client = TestClient(app)

    assert client.get("/api/ai-teaching/teaching-chat").status_code in (401, 403)
    assert (
        client.post(
            "/api/ai-teaching/teaching-chat", json={"text": "hello"}
        ).status_code
        in (401, 403)
    )
