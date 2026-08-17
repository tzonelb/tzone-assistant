"""Tests that one company's script never reaches another company's customers.

This is the defect the platform already fixed twice on the AI path — the shared
`bot_profile.json` that put one company's persona in everybody's prompt (D-005),
and the shared branding and menu in the assistant's reply — and had not fixed on
the **flow** path.

`features/*/flow.json` holds T-ZONE's own IPTV support script: a language
picker, then a menu reading "📺 IPTV · 🛍️ Sales · 📞 Telecom Services ·
ℹ️ About T-ZONE". It was loaded once at import and served to anyone.

`config/automation_policy.json` is what made that reachable. It shipped WhatsApp
as `meta_agent_only` and Telegram as `flow_only`, so on those two channels
`should_auto_reply_with_ai` was False **for every company**, the assistant never
took priority, and the engine fell through to that flow. Running the engine for
an arbitrary company id returned T-ZONE's menu verbatim, on both channels.

It survived because it only ever showed on the two channels nobody was testing:
Messenger and Instagram shipped as `auto_reply`, so they never reached the flow
at all.
"""

from __future__ import annotations

import pytest


# Strings that belong to T-ZONE's own business and to nobody else's customers.
TZONE_MARKERS = ("T-ZONE", "IPTV", "Telecom Services", "خدمات الاتصالات")


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.company_settings_service  # noqa: F401
    import core.automation_policy  # noqa: F401
    import core.engine  # noqa: F401
    import core.flow_loader  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.company_settings_service" in rebound

    return test_manager


def _reply(company_id: int, channel: str, message: str = "hello"):
    from core.engine import Engine
    from core.request import Request

    return Engine().handle(
        Request(
            channel=channel,
            user_id=f"cust-{channel}-{company_id}",
            company_id=company_id,
            message=message,
        )
    )


def _leaked(response) -> list[str]:
    """Anything in this reply that belongs to somebody else's business."""
    haystack = " ".join(
        [str(getattr(response, "text", "") or "")]
        + [str(button) for button in (getattr(response, "buttons", None) or [])]
    )

    return [marker for marker in TZONE_MARKERS if marker in haystack]


# --------------------------------------------------------------- the live leak


@pytest.mark.parametrize("channel", ["whatsapp", "telegram", "messenger", "instagram"])
def test_no_company_is_answered_with_another_companys_menu(wired, alpha, channel):
    """The defect, on every channel.

    Before the fix, `whatsapp` and `telegram` returned "📺 IPTV · 🛍️ Sales ·
    ℹ️ About T-ZONE" for any company id at all.
    """
    response = _reply(alpha["id"], channel)

    assert _leaked(response) == [], (
        f"a {channel} customer of company {alpha['id']} was answered with "
        f"{_leaked(response)}"
    )


@pytest.mark.parametrize("channel", ["whatsapp", "telegram"])
def test_the_two_flow_channels_are_the_ones_that_leaked(wired, alpha, beta, channel):
    """Both companies, so this cannot pass by accident on a fixture that
    happens to own the shipped flows."""
    for company in (alpha, beta):
        assert _leaked(_reply(company["id"], channel)) == []


# ------------------------------------------------------------- the flow loader


def test_the_shipped_flows_are_not_served_to_a_company_on_a_shared_platform(
    wired, alpha, beta
):
    """Two companies exist, so there is no safe answer and the answer is none.

    `default_company_id` returns None as soon as a second company appears —
    the same test `channels/credentials.py` already uses to decide whether the
    environment's token may stand in for a connected account.
    """
    from core.flow_loader import flow_loader

    assert flow_loader.get_state("main_menu", company_id=alpha["id"]) is None
    assert flow_loader.get_state("main_menu", company_id=beta["id"]) is None


def test_the_shipped_flows_still_load_for_a_caller_with_no_company(wired):
    """The preview and the tooling read the opening state, and a caller with no
    company is not a customer of anyone."""
    from core.flow_loader import flow_loader

    assert flow_loader.get_state("main_menu") is not None


def test_an_unanswerable_ownership_question_serves_no_flow(wired, alpha, monkeypatch):
    """Fails closed, unlike most guards here. The other direction answers a
    customer with somebody else's menu; this one falls through to the
    assistant, which still replies — from the company's own knowledge."""
    import core.flow_loader as loader_module

    class Exploding:
        def default_company_id(self):
            raise RuntimeError("control plane unavailable")

    monkeypatch.setattr(
        loader_module, "flow_loader", loader_module.flow_loader, raising=True
    )
    monkeypatch.setattr(
        "database.manager.database_manager.default_company_id",
        lambda: (_ for _ in ()).throw(RuntimeError("control plane unavailable")),
        raising=False,
    )

    assert loader_module.flow_loader.get_state("main_menu", company_id=alpha["id"]) is None


# -------------------------------------------------------- the automation policy


def test_a_company_can_turn_the_assistant_on_for_its_own_telegram(wired, alpha):
    """The shipped file says Telegram is `flow_only` for the whole platform.
    That is now a default a company can override, and the override reaches only
    them."""
    from backend.services.company_settings_service import company_settings_service
    from core.automation_policy import automation_policy

    assert automation_policy.should_auto_reply_with_ai("telegram") is False

    company_settings_service.update_section(
        alpha["id"],
        "ai_behavior",
        {"channels": {"telegram": {"ai_enabled": True, "ai_mode": "auto_reply"}}},
        None,
    )

    assert (
        automation_policy.should_auto_reply_with_ai(
            "telegram", company_id=alpha["id"]
        )
        is True
    )


def test_one_company_channel_choice_does_not_reach_another(wired, alpha, beta):
    from backend.services.company_settings_service import company_settings_service
    from core.automation_policy import automation_policy

    company_settings_service.update_section(
        alpha["id"],
        "ai_behavior",
        {"channels": {"telegram": {"ai_enabled": True, "ai_mode": "auto_reply"}}},
        None,
    )

    assert automation_policy.should_auto_reply_with_ai(
        "telegram", company_id=alpha["id"]
    ) is True
    assert automation_policy.should_auto_reply_with_ai(
        "telegram", company_id=beta["id"]
    ) is False


def test_an_unreadable_company_falls_back_to_the_shipped_values(wired):
    """A settings read that could fail a reply would cost a customer their
    answer over a preference."""
    from core.automation_policy import automation_policy

    assert automation_policy.should_auto_reply_with_ai(
        "messenger", company_id=999_999
    ) is True


def test_an_unrecognised_mode_is_ignored_rather_than_silencing_the_channel(
    wired, alpha
):
    """A typo would otherwise make `is_ai_enabled` false and leave the company
    answering nobody on that channel."""
    from backend.services.company_settings_service import company_settings_service
    from core.automation_policy import automation_policy

    company_settings_service.update_section(
        alpha["id"],
        "ai_behavior",
        {"channels": {"messenger": {"ai_mode": "auto_repply"}}},
        None,
    )

    assert automation_policy.should_auto_reply_with_ai(
        "messenger", company_id=alpha["id"]
    ) is True


# ------------------------------------------------------------ the IPTV coupling


def test_telegram_is_no_longer_special_cased_into_an_iptv_flow():
    """`handle_start` branched on `channel == "telegram"` into
    `telegram_iptv_start` with the department forced to `iptv` — T-ZONE's own
    script and T-ZONE's own section, for every company on the platform.

    A channel is not a business, and nothing about Telegram implies IPTV.
    """
    import inspect

    from core.engine import Engine

    # Comments stripped: this file's own explanation of the defect names the
    # state it removed, and asserting against the raw source would match the
    # comment rather than the code.
    source = "\n".join(
        line
        for line in inspect.getsource(Engine.handle_start).splitlines()
        if not line.strip().startswith("#")
    )

    assert "telegram_iptv_start" not in source
    assert '"iptv"' not in source


def test_the_engine_does_not_fall_back_to_an_iptv_state_for_an_unknown_state():
    import inspect

    from core.engine import Engine

    source = "\n".join(
        line
        for line in inspect.getsource(Engine.handle).splitlines()
        if not line.strip().startswith("#")
    )

    # The default opening state is still the historic name, which is harmless
    # now that no company but the owner is served that flow. What must not come
    # back is the branch that *rendered* it for anyone who arrived on Telegram.
    assert 'if request.channel == "telegram":' not in source
