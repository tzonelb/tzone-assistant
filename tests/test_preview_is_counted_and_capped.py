"""A model call the platform pays for, that nothing counted.

`POST /api/ai-teaching/dry-run` runs the assistant on a typed message and shows
the owner what a customer would get. Its own docstring says the important part:
"the model call itself is not suppressed — the point is to see the real reply."
So every press of that button costs the operator money.

`plan_service.record_usage` had exactly one caller in the whole repository —
`channels/meta/smart_reply.py`, the live reply path. The preview called the
model and recorded nothing. Two consequences, and the second is the one that
matters:

* The usage screen and the console both under-report what a company actually
  spends, because a whole category of model call is missing from them.
* Anyone holding `settings.manage` can script the endpoint. Nothing counts it,
  nothing caps it, and nothing on any screen moves — the first evidence is the
  invoice.

Found by asking who calls the counter, rather than by reading the endpoint. The
endpoint looks careful: it is guarded, it is documented at length, and every
line of that documentation is about what the preview must *not* touch — no
message stored, no reply queued, no conversation state changed. What it does
spend was not on the list.

The cap is a hard platform limit, not a plan allowance. A plan limit is the
operator's commercial decision about one customer; this is the platform
declining to let any single account spend without bound, which is not something
a larger plan should be able to buy. It sits far above real use — somebody
tuning their assistant tries a few dozen messages, not two thousand.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    from database.manager import DatabaseManager

    import database.manager as manager_module

    import backend.services.bot_profile_service  # noqa: F401
    import backend.services.plan_service  # noqa: F401

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    for name in (
        "backend.services.bot_profile_service",
        "backend.services.plan_service",
    ):
        assert getattr(sys.modules[name], "database_manager", None) is test_manager

    from backend.services.bot_profile_service import bot_profile_service

    return bot_profile_service


def _alpha(platform):
    return platform["companies"]["alpha"]["id"]


def _previews(platform, company_id):
    from backend.services.plan_service import plan_service

    return int(
        plan_service.usage_total(
            company_id=company_id, metric=plan_service.AI_PREVIEW_METRIC
        )
    )


def test_a_preview_is_counted(wired, platform):
    company_id = _alpha(platform)

    assert _previews(platform, company_id) == 0

    wired.preview_reply(company_id=company_id, message="Hello", channel="messenger")

    assert _previews(platform, company_id) == 1, (
        "the preview ran the assistant and recorded no usage — the model call "
        "is invisible to every screen and every invoice"
    )


def test_previews_accumulate(wired, platform):
    company_id = _alpha(platform)

    for _ in range(3):
        wired.preview_reply(
            company_id=company_id, message="Hello", channel="messenger"
        )

    assert _previews(platform, company_id) == 3


def test_a_preview_is_not_counted_as_a_customer_reply(wired, platform):
    """Folding previews into `ai_replies` would make every usage screen and
    every invoice count a company's own tests as conversations it had."""
    from backend.services.plan_service import plan_service

    company_id = _alpha(platform)

    wired.preview_reply(company_id=company_id, message="Hello", channel="messenger")

    replies = int(
        plan_service.usage_total(
            company_id=company_id, metric=plan_service.AI_REPLY_METRIC
        )
    )

    assert replies == 0, "a preview was counted as a reply to a customer"


def test_the_cap_stops_a_script(wired, platform, monkeypatch):
    """The abuse case, at a cap small enough to reach in a test."""
    from config.settings import config
    from backend.services.bot_profile_service import BotProfileError

    monkeypatch.setattr(config, "AI_PREVIEW_MAX_PER_PERIOD", 3, raising=False)

    company_id = _alpha(platform)

    for _ in range(3):
        wired.preview_reply(
            company_id=company_id, message="Hello", channel="messenger"
        )

    with pytest.raises(BotProfileError, match="previews have been used"):
        wired.preview_reply(
            company_id=company_id, message="Hello", channel="messenger"
        )


def test_one_companys_previews_do_not_spend_anothers(wired, platform, monkeypatch):
    """A shared counter would let any company switch off every other company's
    tuning screen."""
    from config.settings import config
    from backend.services.bot_profile_service import BotProfileError

    monkeypatch.setattr(config, "AI_PREVIEW_MAX_PER_PERIOD", 2, raising=False)

    alpha = platform["companies"]["alpha"]["id"]
    beta = platform["companies"]["beta"]["id"]

    for _ in range(2):
        wired.preview_reply(company_id=alpha, message="Hello", channel="messenger")

    with pytest.raises(BotProfileError):
        wired.preview_reply(company_id=alpha, message="Hello", channel="messenger")

    # Beta is untouched.
    wired.preview_reply(company_id=beta, message="Hello", channel="messenger")

    assert _previews(platform, beta) == 1


def test_the_cap_can_be_switched_off(wired, platform, monkeypatch):
    """An operator who would rather not have this cap sets it to zero. Leaving
    no way to do that would make a platform decision permanent."""
    from config.settings import config

    monkeypatch.setattr(config, "AI_PREVIEW_MAX_PER_PERIOD", 0, raising=False)

    company_id = _alpha(platform)

    for _ in range(5):
        wired.preview_reply(
            company_id=company_id, message="Hello", channel="messenger"
        )

    assert _previews(platform, company_id) == 5


def test_an_unreadable_counter_does_not_take_the_screen_away(
    wired, platform, monkeypatch
):
    """Fails open, deliberately.

    If the usage table cannot be read, the wrong answer is to refuse every
    preview on the platform. Some untracked model calls during an outage is a
    smaller harm than everybody's assistant becoming untestable because one
    query failed — and this is the direction every other guard in this codebase
    fails, except the two where failing open would be a security hole.
    """
    from backend.services.plan_service import plan_service

    def explode(**_kwargs):
        raise RuntimeError("usage table unavailable")

    monkeypatch.setattr(plan_service, "usage_total", explode)

    result = wired.preview_reply(
        company_id=_alpha(platform), message="Hello", channel="messenger"
    )

    assert result is not None
