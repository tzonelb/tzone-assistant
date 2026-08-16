"""Per-company reply policy: how each company answers, resolved per channel.

Departments and the welcome already belonged to the company. The mechanism did
not. ``core/response_policy.py`` read ``config/response_policy.json`` — one file
for the whole platform — and that file decided for every business whether a
welcome is sent and how often, whether the assistant may answer with no
knowledge match, how confident a match has to be, how many knowledge items reach
the model and whether buttons are shown. One company could not loosen
``allow_ai_free_reply`` or tighten ``minimum_match_confidence`` without doing it
to everybody.

What is pinned down here:

* two companies resolve their own policy and never see each other's;
* a channel override beats the company default, which beats the shipped
  default, and clearing an override really goes back to inheriting;
* a company that has chosen nothing resolves exactly the shipped defaults, so
  nothing changes for anybody until somebody edits something;
* a typo is refused rather than stored — a stored typo reads back like a
  decision that was applied and changes nothing at all.

Everything runs against real, freshly provisioned, encrypted per-company
databases from ``tests/conftest.py``.
"""

from __future__ import annotations

import sys

import pytest

import database.manager as manager_module


@pytest.fixture()
def service(platform, monkeypatch):
    """Point every copy of the ``database_manager`` singleton at the test platform.

    Services do ``from database.manager import database_manager``, so each module
    holds its own reference; the whole chain is imported before the sweep so a
    module imported later cannot bind the real singleton and escape it.
    """
    import backend.api.routes.ai_teaching  # noqa: F401
    import backend.services.bot_profile_service  # noqa: F401
    import backend.services.company_settings_service  # noqa: F401
    import backend.services.reply_policy_service  # noqa: F401
    import core.engine  # noqa: F401
    import core.response_policy  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound: set[str] = set()
    for name, module in list(sys.modules.items()):
        if module is None or module is manager_module:
            continue
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.add(name)

    assert "backend.services.company_settings_service" in rebound

    from backend.services.reply_policy_service import reply_policy_service

    return reply_policy_service


@pytest.fixture()
def policy():
    from core.response_policy import response_policy

    return response_policy


def _shipped(channel: str) -> dict:
    from core.response_policy import response_policy

    return response_policy.shipped_channel_policy(channel)


# ----------------------------------------------------------------------
# Isolation between companies
# ----------------------------------------------------------------------


def test_each_company_resolves_its_own_policy_and_never_the_others(
    service, policy, alpha, beta
):
    """The whole defect in one test. The mechanism came from a shared file, so
    one company's decision was every company's decision."""
    service.update_company_default(
        company_id=alpha["id"],
        values={"allow_ai_free_reply": True, "maximum_knowledge_results": 5},
    )
    service.update_company_default(
        company_id=beta["id"],
        values={"allow_ai_free_reply": False, "maximum_knowledge_results": 1},
    )

    alpha_policy = policy.get_channel_policy("messenger", company_id=alpha["id"])
    beta_policy = policy.get_channel_policy("messenger", company_id=beta["id"])

    assert alpha_policy["allow_ai_free_reply"] is True
    assert alpha_policy["maximum_knowledge_results"] == 5

    assert beta_policy["allow_ai_free_reply"] is False
    assert beta_policy["maximum_knowledge_results"] == 1


def test_one_companys_channel_override_does_not_touch_another(
    service, policy, alpha, beta
):
    """Overrides are stored per channel inside one company's own database. A
    channel key shared across companies is the leak this replaces."""
    service.update_channel(
        company_id=alpha["id"],
        channel="whatsapp",
        values={"show_buttons": False},
    )

    assert (
        policy.get_channel_policy("whatsapp", company_id=alpha["id"])["show_buttons"]
        is False
    )
    assert (
        policy.get_channel_policy("whatsapp", company_id=beta["id"])["show_buttons"]
        is _shipped("whatsapp")["show_buttons"]
    )
    assert service.stored(beta["id"]) == {"default": {}, "channels": {}}


# ----------------------------------------------------------------------
# Resolution order
# ----------------------------------------------------------------------


def test_the_channel_override_wins_over_the_company_default(service, policy, alpha):
    service.update_company_default(
        company_id=alpha["id"], values={"welcome_mode": "always"}
    )
    service.update_channel(
        company_id=alpha["id"], channel="telegram", values={"welcome_mode": "never"}
    )

    assert (
        policy.get_channel_policy("messenger", company_id=alpha["id"])["welcome_mode"]
        == "always"
    )
    assert (
        policy.get_channel_policy("telegram", company_id=alpha["id"])["welcome_mode"]
        == "never"
    )


def test_the_company_default_wins_over_the_shipped_default(service, policy, alpha):
    """The shipped values are the platform's starting point, not a decision this
    business made. An owner who switches something on must see it on every
    channel they have not spoken about — including one the platform happened to
    ship the other way."""
    assert _shipped("telegram")["reply_mode"] == "flow_only"

    service.update_company_default(
        company_id=alpha["id"], values={"reply_mode": "grounded_ai"}
    )

    assert (
        policy.get_channel_policy("telegram", company_id=alpha["id"])["reply_mode"]
        == "grounded_ai"
    )


def test_clearing_a_channel_key_falls_back_to_the_company_default(
    service, policy, alpha
):
    """A row that shows a value with no way back to inheriting is a control that
    only looks like a decision."""
    service.update_company_default(
        company_id=alpha["id"], values={"minimum_match_confidence": 0.8}
    )
    service.update_channel(
        company_id=alpha["id"],
        channel="instagram",
        values={"minimum_match_confidence": 0.3},
    )

    assert (
        policy.get_channel_policy("instagram", company_id=alpha["id"])[
            "minimum_match_confidence"
        ]
        == 0.3
    )

    service.update_channel(
        company_id=alpha["id"],
        channel="instagram",
        clear=["minimum_match_confidence"],
    )

    assert (
        policy.get_channel_policy("instagram", company_id=alpha["id"])[
            "minimum_match_confidence"
        ]
        == 0.8
    )

    # Nothing is left behind: the channel inherits rather than keeping an empty
    # override row that a later read would have to interpret.
    assert "instagram" not in service.stored(alpha["id"])["channels"]


def test_clearing_a_whole_channel_restores_inheritance(service, policy, alpha):
    service.update_company_default(
        company_id=alpha["id"], values={"show_buttons": False}
    )
    service.update_channel(
        company_id=alpha["id"],
        channel="messenger",
        values={"show_buttons": True, "welcome_mode": "never"},
    )

    assert (
        policy.get_channel_policy("messenger", company_id=alpha["id"])["show_buttons"]
        is True
    )

    service.clear_channel(company_id=alpha["id"], channel="messenger")

    resolved = policy.get_channel_policy("messenger", company_id=alpha["id"])

    assert resolved["show_buttons"] is False
    assert resolved["welcome_mode"] == _shipped("messenger")["welcome_mode"]


def test_clearing_a_company_default_key_falls_back_to_the_shipped_value(
    service, policy, alpha
):
    shipped = _shipped("messenger")["minimum_match_confidence"]

    service.update_company_default(
        company_id=alpha["id"], values={"minimum_match_confidence": 0.05}
    )
    service.update_company_default(
        company_id=alpha["id"], clear=["minimum_match_confidence"]
    )

    assert (
        policy.get_channel_policy("messenger", company_id=alpha["id"])[
            "minimum_match_confidence"
        ]
        == shipped
    )


def test_saving_one_scope_leaves_the_other_untouched(service, alpha):
    """Each save names only the part it changes. A save that restated the whole
    policy would quietly rewrite the scope the operator was not looking at —
    and one that restated it as empty would wipe it."""
    service.update_company_default(
        company_id=alpha["id"], values={"show_buttons": False}
    )
    service.update_channel(
        company_id=alpha["id"], channel="telegram", values={"welcome_mode": "never"}
    )

    stored = service.stored(alpha["id"])

    assert stored["default"] == {"show_buttons": False}
    assert stored["channels"] == {"telegram": {"welcome_mode": "never"}}

    service.update_company_default(
        company_id=alpha["id"], values={"fallback_to_human": False}
    )

    stored = service.stored(alpha["id"])

    assert stored["channels"] == {"telegram": {"welcome_mode": "never"}}
    assert stored["default"] == {
        "show_buttons": False,
        "fallback_to_human": False,
    }


# ----------------------------------------------------------------------
# A company that has chosen nothing
# ----------------------------------------------------------------------


def test_a_company_that_set_nothing_resolves_exactly_the_shipped_defaults(
    service, policy, alpha
):
    """Until somebody edits something, this change alters no behaviour at all."""
    from backend.services.reply_policy_service import POLICY_CHANNELS

    for channel in POLICY_CHANNELS:
        assert policy.get_channel_policy(
            channel, company_id=alpha["id"]
        ) == _shipped(channel)


def test_a_message_with_no_company_still_resolves_and_does_not_raise(service, policy):
    """The neutral path is a real path: a message that could not be attributed
    to a company must still be answered, on the shipped values."""
    assert policy.get_channel_policy("messenger") == _shipped("messenger")
    assert policy.get_channel_policy("messenger", company_id=None) == _shipped(
        "messenger"
    )


def test_an_unreadable_policy_leaves_the_shipped_defaults_rather_than_no_reply(
    service, policy, alpha, monkeypatch
):
    """This runs on the customer reply path. A settings read that explodes must
    cost the company its preference, not the customer their answer."""
    from backend.services import reply_policy_service as module

    def boom(*args, **kwargs):
        raise RuntimeError("database is sealed")

    monkeypatch.setattr(module.reply_policy_service, "stored", boom)

    assert policy.get_channel_policy("messenger", company_id=alpha["id"]) == _shipped(
        "messenger"
    )


# ----------------------------------------------------------------------
# Refusing typos
# ----------------------------------------------------------------------


def test_an_unknown_key_is_refused_rather_than_stored(service, alpha):
    """A stored typo reads back exactly like a decision that was applied and
    changes nothing. The operator believes they tightened the assistant and
    they have not."""
    from backend.services.reply_policy_service import ReplyPolicyError

    with pytest.raises(ReplyPolicyError) as refused:
        service.update_company_default(
            company_id=alpha["id"], values={"allow_ai_free_replies": True}
        )

    assert "allow_ai_free_replies" in str(refused.value)
    assert service.stored(alpha["id"]) == {"default": {}, "channels": {}}


def test_an_invalid_welcome_mode_is_refused(service, alpha):
    from backend.services.reply_policy_service import ReplyPolicyError

    with pytest.raises(ReplyPolicyError) as refused:
        service.update_company_default(
            company_id=alpha["id"], values={"welcome_mode": "sometimes"}
        )

    assert "welcome_mode" in str(refused.value)
    assert service.stored(alpha["id"])["default"] == {}


def test_a_confidence_out_of_range_is_refused(service, alpha):
    from backend.services.reply_policy_service import ReplyPolicyError

    for value in (1.4, -0.2, "high"):
        with pytest.raises(ReplyPolicyError):
            service.update_company_default(
                company_id=alpha["id"],
                values={"minimum_match_confidence": value},
            )

    assert service.stored(alpha["id"])["default"] == {}

    service.update_company_default(
        company_id=alpha["id"], values={"minimum_match_confidence": 1}
    )
    assert service.stored(alpha["id"])["default"]["minimum_match_confidence"] == 1.0


def test_an_out_of_range_knowledge_count_and_a_fake_boolean_are_refused(
    service, alpha
):
    """``bool("false")`` is ``True``: a switch that reads as off on the screen
    and is on in the engine is worse than a rejected save."""
    from backend.services.reply_policy_service import ReplyPolicyError

    with pytest.raises(ReplyPolicyError):
        service.update_company_default(
            company_id=alpha["id"], values={"maximum_knowledge_results": 40}
        )

    with pytest.raises(ReplyPolicyError):
        service.update_company_default(
            company_id=alpha["id"], values={"maximum_knowledge_results": 0}
        )

    with pytest.raises(ReplyPolicyError):
        service.update_company_default(
            company_id=alpha["id"], values={"show_buttons": "false"}
        )

    assert service.stored(alpha["id"])["default"] == {}


def test_an_unknown_channel_is_refused(service, alpha):
    """A policy under a channel name nothing resolves is a decision that never
    applies."""
    from backend.services.reply_policy_service import ReplyPolicyError

    with pytest.raises(ReplyPolicyError):
        service.update_channel(
            company_id=alpha["id"], channel="carrier_pigeon", values={"show_buttons": False}
        )

    assert service.stored(alpha["id"])["channels"] == {}


def test_the_generic_settings_route_cannot_smuggle_a_typo_in(service, alpha):
    """The reply policy is one section of company settings, so it is also
    writable through ``/api/company-settings``. That path is held to the same
    rules rather than being the way around them."""
    from backend.services.company_settings_service import company_settings_service
    from backend.services.reply_policy_service import ReplyPolicyError

    with pytest.raises(ReplyPolicyError):
        company_settings_service.update_section(
            company_id=alpha["id"],
            section="reply_policy",
            values={"default": {"welcome_mode": "occasionally"}},
            actor_user_id=None,
        )

    assert service.stored(alpha["id"]) == {"default": {}, "channels": {}}


def test_a_change_is_audited_inside_the_companys_own_database(
    service, platform, alpha
):
    """Reusing the settings section is what buys the audit; a policy change with
    no record of who made it is how an assistant quietly starts behaving
    differently."""
    service.update_company_default(
        company_id=alpha["id"], values={"show_buttons": False}, actor_user_id=7
    )

    with platform["manager"].tenant(alpha["id"]) as conn:
        row = conn.execute(
            """
            SELECT actor_user_id, new_value_json
            FROM company_setting_audit
            WHERE section = 'reply_policy'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert int(row["actor_user_id"]) == 7
    assert "show_buttons" in row["new_value_json"]


# ----------------------------------------------------------------------
# What the customer actually gets
# ----------------------------------------------------------------------


def test_switching_buttons_off_removes_them_for_that_company_only(
    service, policy, alpha, beta
):
    """The policy is only real if the reply changes. ``show_buttons`` is applied
    in ``compose_reply``, which is what every channel goes through."""
    service.update_company_default(
        company_id=alpha["id"], values={"show_buttons": False}
    )

    ai_result = {"language": "en", "reply": "We open at nine.", "buttons": ["Sales"]}

    _, alpha_buttons = policy.compose_reply(
        channel="messenger",
        user_session={},
        ai_result=dict(ai_result),
        company_id=alpha["id"],
    )
    _, beta_buttons = policy.compose_reply(
        channel="messenger",
        user_session={},
        ai_result=dict(ai_result),
        company_id=beta["id"],
    )

    assert alpha_buttons == []
    assert beta_buttons == ["Sales"]


def test_a_company_can_switch_its_welcome_mode_off_without_touching_anyone_else(
    service, policy, alpha, beta
):
    from backend.services.bot_profile_service import bot_profile_service

    for company, text in ((alpha, "Welcome to Alpha."), (beta, "Welcome to Beta.")):
        bot_profile_service.update_default(
            company_id=company["id"],
            values={"welcome_enabled": True, "welcome_message_en": text},
        )

    service.update_channel(
        company_id=alpha["id"], channel="messenger", values={"welcome_mode": "never"}
    )

    alpha_reply, _ = policy.compose_reply(
        channel="messenger",
        user_session={},
        ai_result={"language": "en", "reply": "We open at nine."},
        company_id=alpha["id"],
    )
    beta_reply, _ = policy.compose_reply(
        channel="messenger",
        user_session={},
        ai_result={"language": "en", "reply": "We open at nine."},
        company_id=beta["id"],
    )

    assert alpha_reply == "We open at nine."
    assert beta_reply.startswith("Welcome to Beta.")


# ----------------------------------------------------------------------
# The HTTP surface
# ----------------------------------------------------------------------


def _client(company_id: int, actor_user_id: int = 1):
    """An app carrying only this router, with the permission gate stubbed.

    The gate itself is exercised by the unauthenticated test below.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import ai_teaching

    app = FastAPI()
    app.include_router(ai_teaching.router)
    app.dependency_overrides[ai_teaching.view_context] = lambda: company_id
    app.dependency_overrides[ai_teaching.manage_context] = lambda: company_id
    app.dependency_overrides[ai_teaching.manage_actor] = lambda: {
        "company_id": company_id,
        "actor_user_id": actor_user_id,
    }

    return TestClient(app)


def test_the_screen_can_read_set_and_clear_a_policy(service, alpha):
    """A service that works behind a router that does not is a screen that
    cannot save anything."""
    client = _client(alpha["id"])

    loaded = client.get("/api/ai-teaching/reply-policy").json()

    # Nothing chosen yet, so what applies is exactly what the platform ships.
    assert loaded["default"]["overrides"] == {}
    assert loaded["default"]["values"] == loaded["shipped_default"]
    assert [row["channel"] for row in loaded["channels"]]
    assert loaded["fields"]

    saved = client.put(
        "/api/ai-teaching/reply-policy",
        json={"values": {"allow_ai_free_reply": True}},
    )
    assert saved.status_code == 200
    assert saved.json()["default"]["overrides"] == {"allow_ai_free_reply": True}

    channel_row = next(
        row for row in saved.json()["channels"] if row["channel"] == "telegram"
    )
    # Inherited, and visibly so: nothing was written under the channel.
    assert channel_row["overrides"] == {}
    assert channel_row["inherited"]["allow_ai_free_reply"] is True
    assert channel_row["values"]["allow_ai_free_reply"] is True

    overridden = client.put(
        "/api/ai-teaching/reply-policy/channels/telegram",
        json={"values": {"allow_ai_free_reply": False}},
    )
    telegram = next(
        row for row in overridden.json()["channels"] if row["channel"] == "telegram"
    )
    assert telegram["overrides"] == {"allow_ai_free_reply": False}
    assert telegram["values"]["allow_ai_free_reply"] is False
    assert telegram["inherited"]["allow_ai_free_reply"] is True

    cleared = client.delete("/api/ai-teaching/reply-policy/channels/telegram")
    telegram = next(
        row for row in cleared.json()["channels"] if row["channel"] == "telegram"
    )
    assert telegram["overrides"] == {}
    assert telegram["values"]["allow_ai_free_reply"] is True


def test_the_api_refuses_a_typo_with_a_reason(service, alpha):
    client = _client(alpha["id"])

    refused = client.put(
        "/api/ai-teaching/reply-policy",
        json={"values": {"welcome_mode": "sometimes"}},
    )

    assert refused.status_code == 400
    assert "welcome_mode" in refused.json()["detail"]

    unknown = client.put(
        "/api/ai-teaching/reply-policy/channels/messenger",
        json={"values": {"reply_speed": "fast"}},
    )

    assert unknown.status_code == 400
    assert client.get("/api/ai-teaching/reply-policy").json()["default"][
        "overrides"
    ] == {}


def test_the_policy_is_not_readable_or_writable_across_companies(service, alpha, beta):
    """The company comes from the caller's token and never from the request, so
    there is no company to name in the first place."""
    alpha_client = _client(alpha["id"])
    beta_client = _client(beta["id"])

    alpha_client.put(
        "/api/ai-teaching/reply-policy",
        json={"values": {"minimum_match_confidence": 0.91}},
    )
    beta_client.put(
        "/api/ai-teaching/reply-policy/channels/whatsapp",
        json={"values": {"show_buttons": False}},
    )

    alpha_view = alpha_client.get("/api/ai-teaching/reply-policy").json()
    beta_view = beta_client.get("/api/ai-teaching/reply-policy").json()

    assert alpha_view["default"]["overrides"] == {"minimum_match_confidence": 0.91}
    assert beta_view["default"]["overrides"] == {}

    alpha_whatsapp = next(
        row for row in alpha_view["channels"] if row["channel"] == "whatsapp"
    )
    beta_whatsapp = next(
        row for row in beta_view["channels"] if row["channel"] == "whatsapp"
    )

    assert alpha_whatsapp["overrides"] == {}
    assert beta_whatsapp["overrides"] == {"show_buttons": False}

    # And the same at the storage layer, which is where the isolation lives.
    assert service.stored(alpha["id"])["channels"] == {}
    assert service.stored(beta["id"])["default"] == {}


def test_the_policy_routes_refuse_a_caller_without_a_token(service, alpha):
    """These routes decide how an assistant answers every customer. An
    unauthenticated caller must never reach them."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import ai_teaching

    app = FastAPI()
    app.include_router(ai_teaching.router)
    client = TestClient(app)

    assert client.get("/api/ai-teaching/reply-policy").status_code in (401, 403)
    assert client.put(
        "/api/ai-teaching/reply-policy", json={"values": {"show_buttons": False}}
    ).status_code in (401, 403)
    assert client.put(
        "/api/ai-teaching/reply-policy/channels/messenger",
        json={"values": {"show_buttons": False}},
    ).status_code in (401, 403)
    assert client.delete(
        "/api/ai-teaching/reply-policy/channels/messenger"
    ).status_code in (401, 403)
