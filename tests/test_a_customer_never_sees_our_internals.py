"""When the reply path breaks, the customer gets an apology, not the exception.

`Engine.handle` wraps everything in one `except Exception`. That handler used
to build the reply out of the failure itself — the literal text "ENGINE ERROR",
the exception class, and `str(error)` — and hand it to whoever was typing.

Two things wrong with that, and the second is the serious one.

It is a bad reply: somebody asked a shop a question and got a stack trace.

And `str(error)` is not always harmless. `DatabaseManager._open` raises
"Could not decrypt company_7.db. The key does not match this file." An
`IntegrityError` from SQLCipher names the table and column it failed on. A
`KeyError` names an internal key. All of it went out over Messenger, WhatsApp or
Telegram to a stranger — one of *the company's customers*, on a platform whose
whole design is that companies cannot see each other's internals.

The class already had `ERROR_TEXT` in both languages, and two other error paths
in the same file already used it. Only the outermost one did not.
"""

from __future__ import annotations

import pytest

from core.engine import Engine
from core.request import Request


LEAKY = "Could not decrypt company_7.db. The key does not match this file."


@pytest.fixture()
def engine_that_breaks(monkeypatch):
    """An engine whose first step inside `handle` raises.

    `is_reset_message` is the first call inside the try block. Breaking
    something deeper — the knowledge matcher, say — is absorbed by
    `handle_ai`'s own fallback and never reaches the outer handler, so it would
    leave the path this file is about untested.
    """
    engine = Engine()

    def explode(*args, **kwargs):
        raise RuntimeError(LEAKY)

    monkeypatch.setattr(engine, "is_reset_message", explode)

    return engine


def _ask(engine, message):
    return engine.handle(
        Request(
            channel="messenger",
            user_id="cust-internals",
            company_id=1,
            message=message,
        )
    )


def test_the_customer_gets_a_reply_at_all(engine_that_breaks):
    response = _ask(engine_that_breaks, "مرحبا")

    assert response is not None
    assert response.text.strip(), "a broken reply path answered with nothing"


@pytest.mark.parametrize(
    "forbidden",
    ["ENGINE ERROR", "RuntimeError", "company_7.db", "Traceback", LEAKY],
)
def test_the_reply_carries_none_of_our_internals(engine_that_breaks, forbidden):
    response = _ask(engine_that_breaks, "مرحبا")

    assert forbidden not in response.text, (
        f"a customer was shown {forbidden!r} — the engine is mailing its "
        "internals to whoever is typing"
    )


@pytest.mark.parametrize(
    "message, expected",
    [
        ("مرحبا كيفك", Engine.ERROR_TEXT["ar"]),
        ("hello there", Engine.ERROR_TEXT["en"]),
    ],
)
def test_the_apology_is_in_the_customers_language(
    engine_that_breaks, message, expected
):
    """The words already existed in both languages; the handler just has to
    choose between them the way the rest of the class does."""
    response = _ask(engine_that_breaks, message)

    assert response.text == expected


def test_the_language_is_resolved_defensively(engine_that_breaks, monkeypatch):
    """This handler catches everything, including failures from before the
    language was worked out — so resolving it must not raise in turn."""

    def also_explode(*args, **kwargs):
        raise ValueError("language detection is broken too")

    monkeypatch.setattr(engine_that_breaks, "detect_language", also_explode)

    response = _ask(engine_that_breaks, "مرحبا")

    assert response.text == Engine.ERROR_TEXT["ar"]


def test_a_working_engine_does_not_apologise(monkeypatch):
    """The control.

    Every assertion above is about the text of a failure. If `handle` returned
    the apology for ordinary messages too, they would all pass while the
    assistant answered nobody.
    """
    engine = Engine()

    monkeypatch.setattr(engine, "is_reset_message", lambda message: False)

    response = _ask(engine, "مرحبا")

    assert response is not None
    assert response.text not in (
        Engine.ERROR_TEXT["ar"],
        Engine.ERROR_TEXT["en"],
    ), "an ordinary message was answered with the failure apology"
