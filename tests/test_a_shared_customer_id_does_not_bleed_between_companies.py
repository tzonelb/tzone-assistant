"""One person who messages two businesses must not share a conversation.

The engine keeps live flow state — language, current menu state, department,
the half-finished ticket, the last thing the assistant said — in an in-memory
store in `core/session.py`. That store was keyed on the customer's external id
alone.

An external id is not a customer of one company. A WhatsApp number and a
Telegram chat id are the **person**, identical across every business they
message: the Telegram `chat_id` is the same to every bot, the WhatsApp sender
is the same phone to every number. So a person who wrote to company A's bot and
then to company B's bot landed on **one** session. Their language, their
department, their place in a flow, and the text of their last message and the
assistant's last reply bled from A into B — a cross-tenant leak of live
conversation state and content, and, under a flood of many customers across
many companies, exactly the "messages get mixed up between companies" failure.

`SessionManager.key` — company, channel and user together — existed and was
called from nowhere; every method keyed on `str(user_id)`. This holds the store
to that composite key, and holds it *fail-closed*: a bare id is refused rather
than quietly bucketed under company 0, so a call site that forgets to namespace
raises here instead of leaking.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.request import Request
from core.session import SessionManager, session


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_store():
    session.sessions.clear()
    session._touched.clear()
    yield
    session.sessions.clear()
    session._touched.clear()


def test_the_same_person_at_two_companies_gets_two_sessions():
    """The behaviour, end to end through the key the engine actually uses.

    Same channel, same external id, two companies. State set for one must be
    invisible to the other.
    """
    person = "441234567890"  # a WhatsApp number: the same to every business

    a = Request(channel="whatsapp", user_id=person, company_id=11)
    b = Request(channel="whatsapp", user_id=person, company_id=22)

    assert a.session_key != b.session_key

    session.update(a.session_key, "language", "ar")
    session.update(a.session_key, "current_department", "sales")
    session.update(a.session_key, "last_ai_reply", "A's private answer")

    b_session = session.get(b.session_key)

    # Company 22 has never seen this person; company 11's state is not theirs.
    assert b_session is None or (
        b_session.get("language") is None
        and b_session.get("current_department") is None
        and b_session.get("last_ai_reply") is None
    )

    # And company 11 keeps what it set, unchanged by anything company 22 does.
    session.update(b.session_key, "language", "en")
    assert session.get(a.session_key)["language"] == "ar"
    assert session.get(b.session_key)["language"] == "en"


def test_the_same_person_on_two_channels_of_one_company_also_separates():
    """The narrower collision the key was also meant to fix: one person on two
    channels of the same company is two conversations, not one."""
    a = Request(channel="whatsapp", user_id="p", company_id=5)
    b = Request(channel="telegram", user_id="p", company_id=5)

    session.update(a.session_key, "state", "await_order")
    assert session.get(b.session_key) is None


def test_a_bare_external_id_is_refused_rather_than_bucketed():
    """Fail closed. A raw id keyed under company 0 is how the leak happened, so
    the store refuses one outright."""
    for raw in ("441234567890", "preview-user", "demo-omar", "12345"):
        with pytest.raises(ValueError):
            session.get(raw)
        with pytest.raises(ValueError):
            session.create(raw)
        with pytest.raises(ValueError):
            session.reset(raw)


def test_a_namespaced_key_is_accepted():
    key = SessionManager.key("12345", "telegram", 7)

    assert session.get(key) is None  # accepted, simply absent
    session.update(key, "language", "ar")
    assert session.get(key)["language"] == "ar"


def test_the_engine_never_keys_a_session_on_the_raw_external_id():
    """The regression guard, at the source.

    Every `session.<method>(...)` in the engine must take `request.session_key`
    (or an identity threaded from it), never `request.user_id`. Read from the
    source because the failure is a single call site quietly reintroducing the
    raw id — which a behaviour test only catches if it happens to exercise that
    exact path.
    """
    source = (ROOT / "core/engine.py").read_text()
    tree = ast.parse(source)

    session_methods = {
        "create", "get", "update", "reset",
        "set_language", "push_state", "go_back", "go_home",
    }

    offenders = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func

        if not (isinstance(func, ast.Attribute) and func.attr in session_methods):
            continue

        if not (isinstance(func.value, ast.Name) and func.value.id == "session"):
            continue

        # The first positional argument is the session identity.
        if not node.args:
            continue

        first = node.args[0]

        if (
            isinstance(first, ast.Attribute)
            and first.attr == "user_id"
            and isinstance(first.value, ast.Name)
            and first.value.id == "request"
        ):
            offenders.append(f"line {node.lineno}: session.{func.attr}(request.user_id...)")

    assert not offenders, (
        "the engine keys a session on the raw external id, which collides "
        "across companies:\n  " + "\n  ".join(offenders)
    )
