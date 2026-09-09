"""The owner's own reply rules: stored per company, and actually consulted.

Two kinds of failure are guarded here. The first is a leak: one company's rules
reaching another company's assistant -- the same class of bug the per-tenant
database exists to prevent. The second is the quieter one this codebase keeps
running into -- a stored value that nothing reads. A rules screen whose rules
never reach the model is a notepad, so the wiring test asserts the instruction
text is in the exact system prompt the model is handed, not merely in the table.
"""

from __future__ import annotations

import sys

import pytest

import database.manager as manager_module


@pytest.fixture()
def bound(platform, monkeypatch):
    """Point every module's ``database_manager`` at the test platform.

    ``instruction_service`` imports the manager by value at module load, so it
    must be rebound like the rest -- otherwise ``for_prompt`` reads a real disk
    database, not the fixture's.
    """
    import backend.services.instruction_service as instruction_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    # Belt and suspenders: the identity loop above depends on the module's
    # ``database_manager`` still being the real singleton at fixture time, which
    # a prior test's teardown ordering can leave otherwise. This module is the
    # one every assertion here reads through, so bind it by name, not by luck.
    monkeypatch.setattr(instruction_module, "database_manager", test_manager)

    return test_manager


# --------------------------------------------------------------- the service

def test_a_rule_is_stored_and_read_back(bound, alpha):
    from backend.services.instruction_service import instruction_service

    instruction_service.create(
        company_id=alpha["id"], text="Never quote a price.", tags=[]
    )

    listed = instruction_service.list(alpha["id"])
    assert [item["text"] for item in listed] == ["Never quote a price."]


def test_one_companys_rules_never_reach_another(bound, alpha, beta):
    from backend.services.instruction_service import instruction_service

    instruction_service.create(
        company_id=alpha["id"], text="Alpha secret rule.", tags=[]
    )

    assert instruction_service.for_prompt(beta["id"]) == []
    assert instruction_service.for_prompt(alpha["id"]) == ["Alpha secret rule."]


def test_an_unscoped_rule_applies_everywhere(bound, alpha):
    from backend.services.instruction_service import instruction_service

    instruction_service.create(company_id=alpha["id"], text="Be brief.", tags=[])

    assert instruction_service.for_prompt(
        alpha["id"], department="sales", channel="messenger"
    ) == ["Be brief."]


def test_a_department_scoped_rule_only_applies_in_that_department(bound, alpha):
    from backend.services.instruction_service import instruction_service

    instruction_service.create(
        company_id=alpha["id"], text="Offer the warranty.", tags=["dept:sales"]
    )

    assert instruction_service.for_prompt(alpha["id"], department="sales") == [
        "Offer the warranty."
    ]
    assert instruction_service.for_prompt(alpha["id"], department="support") == []


def test_a_channel_scoped_rule_only_applies_on_that_channel(bound, alpha):
    from backend.services.instruction_service import instruction_service

    instruction_service.create(
        company_id=alpha["id"], text="Keep it formal.", tags=["channel:whatsapp"]
    )

    assert instruction_service.for_prompt(alpha["id"], channel="whatsapp") == [
        "Keep it formal."
    ]
    assert instruction_service.for_prompt(alpha["id"], channel="messenger") == []


def test_rules_keep_their_order(bound, alpha):
    from backend.services.instruction_service import instruction_service

    first = instruction_service.create(company_id=alpha["id"], text="First.", tags=[])
    second = instruction_service.create(company_id=alpha["id"], text="Second.", tags=[])

    instruction_service.reorder(
        company_id=alpha["id"], ordered_ids=[second["id"], first["id"]]
    )

    assert instruction_service.for_prompt(alpha["id"]) == ["Second.", "First."]


# ------------------------------------------------------------------ the wiring

@pytest.fixture()
def captured_prompt(monkeypatch):
    """Capture the exact system prompts the router hands the model.

    The network is replaced, not the router: the point is to prove the router
    itself puts the rule in the payload, so nothing between the table and the
    request is stubbed.
    """
    from config.settings import config
    import core.ai_router as ai_router_module

    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

    seen: dict = {}

    class _Response:
        status_code = 200

        def json(self):
            return {
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    '{"department":"unknown","reply":"ok",'
                                    '"language":"en","confidence":0.5}'
                                ),
                            }
                        ]
                    }
                ]
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            seen["payload"] = json
            return _Response()

    monkeypatch.setattr(ai_router_module.httpx, "Client", _Client)

    return seen


def _system_text(payload) -> str:
    return "\n".join(
        part["content"]
        for part in payload["input"]
        if part["role"] == "system"
    )


def test_a_companys_rule_reaches_the_model(bound, alpha, captured_prompt):
    from backend.services.instruction_service import instruction_service
    from core.ai_router import ai_router

    instruction_service.create(
        company_id=alpha["id"], text="Always greet by first name.", tags=[]
    )

    ai_router.route(
        message="hi",
        channel="messenger",
        user_id="u1",
        company_id=alpha["id"],
        department="sales",
    )

    assert "Always greet by first name." in _system_text(captured_prompt["payload"])


def test_another_companys_rule_does_not_reach_this_model_call(
    bound, alpha, beta, captured_prompt
):
    """The leak test at the wiring level: beta's rule must never appear in a
    prompt built for alpha, even though both live on the same platform."""
    from backend.services.instruction_service import instruction_service
    from core.ai_router import ai_router

    instruction_service.create(
        company_id=beta["id"], text="Beta private rule.", tags=[]
    )

    ai_router.route(
        message="hi",
        channel="messenger",
        user_id="u1",
        company_id=alpha["id"],
        department="sales",
    )

    assert "Beta private rule." not in _system_text(captured_prompt["payload"])


def test_no_rules_means_the_prompt_is_unchanged(bound, alpha, captured_prompt):
    """A company that set no rules pays no penalty: the guidance block only
    appears when there is something to say."""
    from core.ai_router import ai_router

    ai_router.route(
        message="hi",
        channel="messenger",
        user_id="u1",
        company_id=alpha["id"],
        department="sales",
    )

    assert "business owner has set these rules" not in _system_text(
        captured_prompt["payload"]
    )
