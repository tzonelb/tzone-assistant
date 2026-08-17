"""Tests for AI TEACHING: the per-company assistant profile and its dry run.

Two failures are worth more than the rest and are tested directly:

* The prompt used to be built from one shared file, so every company's
  assistant spoke with the same identity, tone and instructions. Company A
  editing its bot edited everyone's.
* A "test your bot" button that quietly ran the live pipeline would send real
  messages to real customers, store rows in the company's inbox and move
  conversation state. The preview has to leave nothing behind.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the profile service at the test platform's databases."""
    import sys

    import database.manager as manager_module

    # Imported before the sweep below: a module that has not been imported yet
    # holds no reference to rebind, and would later import the real singleton.
    # The whole assistant chain is imported here, not just the service under
    # test — the dry run reaches the engine, the knowledge service and the
    # ticket service, and a module first imported *after* the sweep would bind
    # this test's temporary manager permanently and corrupt every test file
    # that runs afterwards.
    import backend.api.routes.ai_teaching  # noqa: F401
    import backend.services.bot_profile_service  # noqa: F401
    import backend.services.channel_account_service  # noqa: F401
    import core.prompt_builder  # noqa: F401
    import gateway.message_gateway  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    # Modules that did `from database.manager import database_manager` hold
    # their own reference and must be rebound too, or the test silently runs
    # against the process-wide singleton and proves nothing.
    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.bot_profile_service" in rebound
    assert "backend.services.channel_account_service" in rebound
    assert "backend.services.knowledge_service" in rebound
    assert "backend.services.auth_service" in rebound

    from backend.services.bot_profile_service import bot_profile_service

    return bot_profile_service


@pytest.fixture()
def prompt():
    from core.prompt_builder import prompt_builder

    return prompt_builder


def _counts(platform, company) -> dict[str, int]:
    """Everything a reply would normally write into a company's database."""
    tables = ("messages", "conversations", "pending_replies", "tickets")

    with platform["manager"].tenant(company["id"]) as conn:
        return {
            table: int(
                conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"]
            )
            for table in tables
        }


# ----------------------------------------------------------------------
# The profile itself
# ----------------------------------------------------------------------


def test_first_read_creates_a_working_profile(service, alpha):
    """A company that has never opened the screen has no row yet. Returning
    nothing would show an empty form and leave the assistant with no tone or
    instructions at all."""
    profile = service.get_default(alpha["id"])

    assert profile["id"]
    assert profile["is_default"] is True
    assert profile["tone"]
    assert profile["system_prompt"]
    assert profile["examples"] == []


def test_reading_twice_does_not_create_a_second_default(service, platform, alpha):
    """Two default rows would make which one the assistant reads depend on row
    order, so saving the screen could edit a profile nobody answers with."""
    first = service.get_default(alpha["id"])
    second = service.get_default(alpha["id"])

    assert first["id"] == second["id"]

    with platform["manager"].tenant(alpha["id"]) as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS total FROM bot_profiles WHERE is_default = 1"
        ).fetchone()["total"]

    assert int(total) == 1


def test_saving_the_screen_keeps_untouched_fields(service, alpha):
    """The form sends only what changed. A partial save that blanked the rest
    would silently wipe the instructions when someone renamed the profile."""
    service.update_default(
        company_id=alpha["id"],
        values={"system_prompt": "Always confirm the branch first."},
    )

    service.update_default(company_id=alpha["id"], values={"tone": "formal"})

    profile = service.get_default(alpha["id"])

    assert profile["tone"] == "formal"
    assert profile["system_prompt"] == "Always confirm the branch first."


def test_examples_round_trip(service, alpha):
    """Examples are stored as JSON text. Handing the raw column to the screen,
    or storing a list of anything, would break the editor on the next open."""
    service.update_default(
        company_id=alpha["id"],
        values={
            "examples": [
                {"customer": "Do you deliver?", "reply": "Yes, within Beirut."}
            ]
        },
    )

    profile = service.get_default(alpha["id"])

    assert profile["examples"] == [
        {"customer": "Do you deliver?", "reply": "Yes, within Beirut."}
    ]


def test_half_written_example_is_refused(service, alpha):
    """An example with no reply teaches the model nothing and still costs
    prompt space on every single customer message."""
    from backend.services.bot_profile_service import BotProfileError

    with pytest.raises(BotProfileError):
        service.update_default(
            company_id=alpha["id"],
            values={"examples": [{"customer": "Do you deliver?", "reply": "  "}]},
        )


def test_editing_one_company_cannot_reach_anothers_profile(service, alpha, beta):
    """Profiles live in per-company databases where ids restart at 1, so the
    same small id is valid in both companies. A write must land only in the
    caller's database, and an id it does not own must be refused rather than
    silently applied to whatever row shares that number."""
    from backend.services.bot_profile_service import BotProfileError

    service.update_default(company_id=alpha["id"], values={"tone": "formal"})

    beta_profile = service.get_default(beta["id"])
    service.update_profile(
        company_id=beta["id"],
        profile_id=beta_profile["id"],
        values={"tone": "casual"},
    )

    assert service.get_default(alpha["id"])["tone"] == "formal"

    extra = service.create_profile(
        company_id=alpha["id"], values={"name": "Second Voice"}
    )

    with pytest.raises(BotProfileError):
        service.update_profile(
            company_id=beta["id"],
            profile_id=extra["id"],
            values={"tone": "hijacked"},
        )


def test_deleting_the_default_is_refused(service, alpha):
    """The default is what every channel without its own profile falls back to.
    Deleting it would leave the assistant with no instructions at all."""
    from backend.services.bot_profile_service import BotProfileError

    profile = service.get_default(alpha["id"])

    with pytest.raises(BotProfileError):
        service.delete_profile(alpha["id"], profile["id"])


def test_extra_profile_answers_for_its_own_channel_account(service, alpha):
    """A company with two connected pages needs to speak differently on each.
    Without the binding, the second page silently uses the first page's tone."""
    from backend.services.channel_account_service import channel_account_service

    account = channel_account_service.create_account(
        company_id=alpha["id"],
        channel="messenger",
        name="Support Page",
        values={"page_id": "PAGE_SUPPORT", "access_token": "token"},
    )

    created = service.create_profile(
        company_id=alpha["id"],
        values={
            "name": "Support Page Voice",
            "tone": "technical",
            "channel_account_id": account["id"],
        },
    )

    bound = service.resolve_profile(alpha["id"], account["id"])
    unbound = service.resolve_profile(alpha["id"], None)

    assert bound["id"] == created["id"]
    assert bound["tone"] == "technical"
    assert unbound["is_default"] is True


def test_profile_cannot_be_bound_to_another_companys_account(service, alpha, beta):
    """Binding to a page this company does not own would tie its assistant to a
    competitor's connection and make later screens read the two as related."""
    from backend.services.bot_profile_service import BotProfileError
    from backend.services.channel_account_service import channel_account_service

    account = channel_account_service.create_account(
        company_id=beta["id"],
        channel="messenger",
        name="Beta Page",
        values={"page_id": "PAGE_BETA", "access_token": "token"},
    )

    with pytest.raises(BotProfileError):
        service.create_profile(
            company_id=alpha["id"],
            values={"name": "Stolen", "channel_account_id": account["id"]},
        )


# ----------------------------------------------------------------------
# The profile reaching the prompt
# ----------------------------------------------------------------------


def test_prompt_carries_this_companys_tone_and_instructions(service, prompt, alpha):
    """The prompt used to be read from a shared JSON file, so nothing an owner
    wrote ever reached the model. This is the whole feature."""
    service.update_default(
        company_id=alpha["id"],
        values={
            "tone": "formal",
            "system_prompt": "Never quote a price without the branch manager.",
            "examples": [
                {"customer": "Do you repair laptops?", "reply": "Yes, in two days."}
            ],
        },
    )

    text = prompt.build_system_prompt("messenger", company_id=alpha["id"])

    assert "formal" in text
    assert "Never quote a price without the branch manager." in text
    assert "Do you repair laptops?" in text


def test_one_companys_profile_does_not_change_anothers_prompt(
    service, prompt, alpha, beta
):
    """The defect this module exists to fix: one shared profile file meant a
    tone or instruction written by one company was spoken to every other
    company's customers."""
    service.update_default(
        company_id=alpha["id"],
        values={
            "tone": "formal",
            "system_prompt": "ALPHA ONLY: offer the loyalty discount.",
            "examples": [{"customer": "hello", "reply": "Alpha speaking."}],
        },
    )
    service.update_default(
        company_id=beta["id"],
        values={
            "tone": "casual",
            "system_prompt": "BETA ONLY: always ask for the order number.",
        },
    )

    alpha_prompt = prompt.build_system_prompt("messenger", company_id=alpha["id"])
    beta_prompt = prompt.build_system_prompt("messenger", company_id=beta["id"])

    assert "ALPHA ONLY" in alpha_prompt
    assert "ALPHA ONLY" not in beta_prompt
    assert "Alpha speaking." not in beta_prompt

    assert "BETA ONLY" in beta_prompt
    assert "BETA ONLY" not in alpha_prompt


def test_prompt_without_a_company_carries_no_company_instructions(
    service, prompt, alpha
):
    """A message that could not be routed to a company must not be answered out
    of some other company's profile — that is the leak, not a fallback."""
    service.update_default(
        company_id=alpha["id"],
        values={"system_prompt": "ALPHA ONLY: offer the loyalty discount."},
    )

    text = prompt.build_system_prompt("messenger")

    assert "ALPHA ONLY" not in text
    assert "Required JSON" in text


def test_scoped_company_reaches_a_caller_that_cannot_pass_one(
    service, prompt, alpha, beta
):
    """The live caller only has a channel. The scope is how the company travels
    through it; if it leaked past its block, the next company served by the same
    thread would answer with the previous one's instructions."""
    from core.prompt_builder import company_scope

    service.update_default(
        company_id=alpha["id"],
        values={"system_prompt": "ALPHA ONLY: offer the loyalty discount."},
    )
    service.update_default(
        company_id=beta["id"],
        values={"system_prompt": "BETA ONLY: always ask for the order number."},
    )

    with company_scope(alpha["id"]):
        scoped = prompt.build_system_prompt("messenger")

    after = prompt.build_system_prompt("messenger")

    assert "ALPHA ONLY" in scoped
    assert "ALPHA ONLY" not in after
    assert "BETA ONLY" not in after


# ----------------------------------------------------------------------
# The dry run
# ----------------------------------------------------------------------


def test_dry_run_returns_a_reply(service, alpha, monkeypatch):
    """With no model configured the router returns nothing and the engine falls
    back. An empty preview would read as a broken screen rather than as the
    answer a customer would actually receive today."""
    from config.settings import config

    monkeypatch.setattr(config, "AI_ENABLED", False)

    result = service.preview_reply(
        company_id=alpha["id"],
        message="Do you deliver to Tripoli?",
    )

    assert result["reply"].strip()
    assert result["delivered"] is False
    assert result["stored"] is False
    assert result["note"]


def test_dry_run_writes_nothing(service, platform, alpha, monkeypatch):
    """A preview that ran the delivery path would store a message in the
    company's inbox, queue a reply and eventually send it to a real customer —
    the exact thing an owner uses this screen to avoid."""
    from config.settings import config

    monkeypatch.setattr(config, "AI_ENABLED", False)

    before = _counts(platform, alpha)

    service.preview_reply(company_id=alpha["id"], message="مرحبا، بدي سعر")
    service.preview_reply(company_id=alpha["id"], message="Do you deliver?")

    assert _counts(platform, alpha) == before
    assert before["messages"] == 0
    assert before["conversations"] == 0


def test_dry_run_leaves_no_conversation_state_behind(service, alpha, monkeypatch):
    """The engine keeps live conversation state in memory keyed by user id. A
    preview that reused or kept a key would change the language, department or
    history of a conversation a customer is in the middle of."""
    from config.settings import config
    from core.session import session

    monkeypatch.setattr(config, "AI_ENABLED", False)

    before = set(session.sessions)

    service.preview_reply(company_id=alpha["id"], message="Do you deliver?")

    assert set(session.sessions) == before


def test_dry_run_never_calls_a_provider_with_ai_disabled(service, alpha, monkeypatch):
    """The preview must be usable on a server with no model key. Reaching the
    network there would make the screen fail instead of showing the fallback a
    customer would get."""
    from config.settings import config

    monkeypatch.setattr(config, "AI_ENABLED", False)

    import httpx

    def explode(*args, **kwargs):
        raise AssertionError("The dry run made an outbound HTTP request.")

    monkeypatch.setattr(httpx.Client, "post", explode)

    result = service.preview_reply(company_id=alpha["id"], message="Do you deliver?")

    assert result["model_available"] is False
    assert result["reply"].strip()


def test_dry_run_asks_the_model_with_this_companys_profile(
    service, alpha, beta, monkeypatch
):
    """The preview is only worth trusting if it is the real pipeline. If the
    profile did not reach the model here, the screen would show a reply the
    live assistant would never produce."""
    from config.settings import config
    from core.ai_router import ai_router
    from core.prompt_builder import prompt_builder

    service.update_default(
        company_id=alpha["id"],
        values={"system_prompt": "ALPHA ONLY: offer the loyalty discount."},
    )
    service.update_default(
        company_id=beta["id"],
        values={"system_prompt": "BETA ONLY: always ask for the order number."},
    )

    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

    # This test is about prompt isolation, not the guardrail, so the company is
    # given a policy that permits a reply with nothing matched. Under the
    # shipped policy (`grounded_ai`, no free reply) a company with an empty
    # knowledge base answers with the safe fallback and never calls the model —
    # which is correct, and is asserted in `test_reply_policy_enforcement.py`.
    from backend.services.reply_policy_service import reply_policy_service

    reply_policy_service.update_company_default(
        company_id=alpha["id"],
        values={"reply_mode": "knowledge_then_ai", "allow_ai_free_reply": True},
    )

    captured: dict[str, str] = {}

    def fake_call(**kwargs):
        # The real method builds the prompt exactly this way; nothing leaves
        # the process.
        captured["prompt"] = prompt_builder.build_system_prompt(kwargs["channel"])

        return {
            "department": "sales",
            "intent": "delivery",
            "topic": "delivery",
            "language": "en",
            "confidence": 0.9,
            "reply": "Yes, we deliver.",
            "buttons": [],
            "needs_human": False,
            "missing_information": [],
            "used_knowledge_ids": [],
            "notes": "",
        }

    monkeypatch.setattr(ai_router, "call_openai", fake_call)

    result = service.preview_reply(company_id=alpha["id"], message="Do you deliver?")

    assert "ALPHA ONLY" in captured["prompt"]
    assert "BETA ONLY" not in captured["prompt"]
    assert "Yes, we deliver." in result["reply"]


def test_dry_run_refuses_an_empty_message(service, alpha):
    """Running the assistant on nothing wastes a model call and shows a reply
    that answers no question anyone asked."""
    from backend.services.bot_profile_service import BotProfileError

    with pytest.raises(BotProfileError):
        service.preview_reply(company_id=alpha["id"], message="   ")


# ----------------------------------------------------------------------
# The HTTP surface
# ----------------------------------------------------------------------


def _client(company_id: int):
    """An app carrying only this router, with the permission gate stubbed.

    The permission dependency itself is exercised by the unauthenticated test
    below; overriding it here keeps the rest focused on the routes.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import ai_teaching

    app = FastAPI()
    app.include_router(ai_teaching.router)
    app.dependency_overrides[ai_teaching.view_context] = lambda: company_id
    app.dependency_overrides[ai_teaching.manage_context] = lambda: company_id

    return TestClient(app)


def test_endpoints_read_save_and_test_the_profile(service, alpha, monkeypatch):
    """The screen is only as real as its routes. A service that works behind a
    router that does not is a screen that cannot save anything."""
    from config.settings import config

    monkeypatch.setattr(config, "AI_ENABLED", False)

    client = _client(alpha["id"])

    loaded = client.get("/api/ai-teaching/profile")
    assert loaded.status_code == 200
    assert loaded.json()["profile"]["tone"]

    saved = client.put(
        "/api/ai-teaching/profile",
        json={
            "tone": "formal",
            "system_prompt": "ALPHA ONLY: confirm the branch first.",
            "examples": [{"customer": "Do you deliver?", "reply": "Yes."}],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["profile"]["tone"] == "formal"

    prompt = client.get("/api/ai-teaching/profile/prompt")
    assert "ALPHA ONLY: confirm the branch first." in prompt.json()["prompt"]

    tested = client.post(
        "/api/ai-teaching/dry-run",
        json={"message": "Do you deliver to Tripoli?", "channel": "messenger"},
    )
    assert tested.status_code == 200
    assert tested.json()["reply"].strip()
    assert tested.json()["stored"] is False


def test_endpoints_refuse_a_caller_without_a_token(service, alpha):
    """These routes edit what the assistant tells customers and can spend money
    on a model call. An unauthenticated caller must never reach either."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import ai_teaching

    app = FastAPI()
    app.include_router(ai_teaching.router)
    client = TestClient(app)

    assert client.get("/api/ai-teaching/profile").status_code in (401, 403)
    assert (
        client.post(
            "/api/ai-teaching/dry-run", json={"message": "hello"}
        ).status_code
        in (401, 403)
    )


def test_stored_examples_are_valid_json(service, platform, alpha):
    """The column is read back by the assistant on every message; storing
    anything unparseable there would silently drop every taught example."""
    service.update_default(
        company_id=alpha["id"],
        values={"examples": [{"customer": "شو الأسعار؟", "reply": "منبعتلك اللائحة."}]},
    )

    with platform["manager"].tenant(alpha["id"]) as conn:
        raw = conn.execute(
            "SELECT examples_json FROM bot_profiles WHERE company_id = ? AND is_default = 1",
            (alpha["id"],),
        ).fetchone()["examples_json"]

    assert json.loads(raw) == [
        {"customer": "شو الأسعار؟", "reply": "منبعتلك اللائحة."}
    ]
