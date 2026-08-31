"""The language a company chose, which was stored and never consulted.

`ai_behavior.reply_language` sat in every company's database. A company could
choose Arabic, watch it save, and get replies in whatever language the customer
happened to write in.

What it replaces is *detection*, not the customer's own choice. A customer who
explicitly asks to switch language is handled before this is reached and still
wins — the platform has a feature for that, and silently overriding it would
make that feature lie about what it did.

The other half of this file is the retirement. `escalate_on_low_confidence`
offered to decide what happens when the assistant is not confident enough to
answer, which is what `fallback_to_human` in the reply policy already decides —
enforced, per channel and per department, on the screen where the rest of the
reply rules live. Two switches for one decision on two screens is worse than
one: an owner sets the one they found and cannot tell why it did nothing. It
was retired rather than implemented, because the decision already has an owner.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    import backend.services.company_settings_service  # noqa: F401
    import core.engine  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.company_settings_service" in rebound

    from core.engine import engine

    return engine


def _choose(company_id, value):
    from backend.services.company_settings_service import company_settings_service

    company_settings_service.update_section(
        company_id, "ai_behavior", {"reply_language": value}, None
    )


ARABIC = "مرحبا كيفك"
ENGLISH = "hello how are you"


# ------------------------------------------------------------------ the default


def test_auto_still_detects_from_the_message(wired, alpha):
    """The default, and the reason implementing this changed nothing for a
    company that has not chosen."""
    assert wired.resolve_language(ARABIC, alpha["id"]) == "ar"
    assert wired.resolve_language(ENGLISH, alpha["id"]) == "en"


def test_no_company_still_detects(wired):
    """The engine is called without a company on paths that have none. Reading
    a setting there would be reading nobody's."""
    assert wired.resolve_language(ENGLISH, None) == "en"


# --------------------------------------------------------------- a chosen one


def test_a_company_that_chose_arabic_gets_arabic_for_an_english_message(
    wired, alpha
):
    """The whole point. A business whose staff read only Arabic asked for this
    when it set the field."""
    _choose(alpha["id"], "ar")

    assert wired.resolve_language(ENGLISH, alpha["id"]) == "ar"


def test_a_company_that_chose_english_gets_english_for_an_arabic_message(
    wired, alpha
):
    _choose(alpha["id"], "en")

    assert wired.resolve_language(ARABIC, alpha["id"]) == "en"


def test_the_choice_reaches_only_the_company_that_made_it(wired, alpha, beta):
    _choose(alpha["id"], "ar")

    assert wired.resolve_language(ENGLISH, alpha["id"]) == "ar"
    assert wired.resolve_language(ENGLISH, beta["id"]) == "en"


def test_switching_back_to_auto_restores_detection(wired, alpha):
    """A one-way switch would pass every test above."""
    _choose(alpha["id"], "ar")
    _choose(alpha["id"], "auto")

    assert wired.resolve_language(ENGLISH, alpha["id"]) == "en"


# ------------------------------------------------------------------ bad values


@pytest.mark.parametrize("value", ["", "  ", "french", "arabic", None, 7])
def test_an_unrecognised_value_falls_back_to_detection(wired, alpha, value):
    """A typo is not an instruction. Treating one as a language would answer
    every customer in a language that does not exist."""
    _choose(alpha["id"], value)

    assert wired.resolve_language(ENGLISH, alpha["id"]) == "en"


@pytest.mark.parametrize("value", ["AR", "ar ", " Ar", "EN"])
def test_case_and_spacing_are_forgiven(wired, alpha, value):
    """Somebody typing "AR " meant Arabic. Refusing it and quietly detecting
    instead would be the same silent no-op this setting was."""
    _choose(alpha["id"], value)

    expected = "ar" if value.strip().lower() == "ar" else "en"

    assert wired.resolve_language(
        ARABIC if expected == "en" else ENGLISH, alpha["id"]
    ) == expected


def test_settings_that_cannot_be_read_fall_back_to_detection(
    wired, alpha, monkeypatch
):
    import backend.services.company_settings_service as settings_module

    def _broken(*args, **kwargs):
        raise RuntimeError("the tenant database is busy")

    monkeypatch.setattr(
        settings_module.company_settings_service, "get_section", _broken
    )

    assert wired.resolve_language(ARABIC, alpha["id"]) == "ar"


# --------------------------------------------------------------- the retirement


def test_the_retired_switch_is_gone_from_the_defaults():
    from database.schema_tenant import DEFAULT_SETTINGS

    assert "escalate_on_low_confidence" not in DEFAULT_SETTINGS["ai_behavior"]


def test_the_decision_it_duplicated_is_still_enforced():
    """Retiring a switch is only safe because the thing it duplicated works.
    If `fallback_to_human` ever stopped being enforced, retiring this would
    have quietly removed a guardrail rather than a duplicate."""
    from core import reply_decision

    assert reply_decision.decide(
        {"reply_mode": "grounded_ai", "fallback_to_human": True},
        {"confidence": 0.0},
        has_knowledge=False,
    ).escalate

    assert not reply_decision.decide(
        {"reply_mode": "grounded_ai", "fallback_to_human": False},
        {"confidence": 0.0},
        has_knowledge=False,
    ).escalate


def test_a_retired_key_already_stored_stops_being_served(wired, alpha):
    """The half that reaches companies already running.

    The seed keeps stored JSON as it is, so a key dropped from the defaults
    would live on in every database provisioned before the change — and the
    screen would go on offering the retired switch to everybody except new
    companies.
    """
    from backend.services.company_settings_service import company_settings_service
    from database.manager import database_manager, utc_now_iso
    import json

    with database_manager.tenant(alpha["id"]) as conn:
        conn.execute(
            """
            INSERT INTO company_settings (company_id, section, settings_json,
                                          updated_by_user_id, created_at, updated_at)
            VALUES (?, 'ai_behavior', ?, NULL, ?, ?)
            ON CONFLICT(company_id, section) DO UPDATE SET
                settings_json = excluded.settings_json
            """,
            (
                alpha["id"],
                json.dumps({"escalate_on_low_confidence": True, "enabled": True}),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        conn.commit()

    values = company_settings_service.get_section(alpha["id"], "ai_behavior")["values"]

    assert "escalate_on_low_confidence" not in values
    assert values["enabled"] is True, "pruning a retired key dropped a live one"
