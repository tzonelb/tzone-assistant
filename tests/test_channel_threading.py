"""Tests that a reply is composed for the channel it is going out on.

`build_main_menu_response` takes a `channel`, defaulting to messenger for the
preview and the tests. Four call sites in `core/engine.py` had `request.channel`
in scope and left it out — so a company that wrote a different welcome for
WhatsApp, or turned the greeting off for one channel, got the messenger answer
on all of them.

The switch saved, the screen showed it, and the customer never saw it, which is
the same shape as every other defect in this codebase worth a test: a control
that appears to work.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _menu_calls():
    """Every `build_main_menu_response` call inside `core/engine.py`."""
    tree = ast.parse((ROOT / "core" / "engine.py").read_text())

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "build_main_menu_response"
        ):
            yield node


def test_the_engine_has_menu_call_sites_to_check():
    """Without this, a rename would make the test below pass by finding
    nothing."""
    assert len(list(_menu_calls())) >= 4


def test_every_menu_call_passes_the_channel():
    """The default exists for the preview, not for the live path. A call site
    that omits it silently answers as though every customer were on
    Messenger."""
    missing = [
        node.lineno
        for node in _menu_calls()
        if "channel" not in {keyword.arg for keyword in node.keywords}
    ]

    assert not missing, (
        "core/engine.py builds a menu without naming the channel at line(s) "
        f"{missing}. The company's welcome and buttons are per channel, so "
        "this answers a WhatsApp customer with the Messenger configuration."
    )


def test_every_menu_call_passes_the_account():
    """A company may run three accounts on one channel with different
    greetings. Passing the channel alone cannot tell them apart."""
    missing = [
        node.lineno
        for node in _menu_calls()
        if "channel_account_id" not in {keyword.arg for keyword in node.keywords}
    ]

    assert not missing, (
        f"core/engine.py builds a menu without the account at line(s) {missing}."
    )


def test_pinning_a_conversation_is_visible_on_its_timeline():
    """`set_pinned` writes `conversation_pinned`, and the timeline's allow-list
    left it out — so the row was written and then filtered away. Nobody could
    ever see it, and nothing said so."""
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )

    source = inspect.getsource(type(conversation_control_service))
    start = source.index("meaningful_types = {")
    allow_list = source[start : source.index("}", start)]

    for event in ("conversation_pinned", "conversation_unpinned"):
        assert f'"{event}"' in allow_list, (
            f"{event} is written but never displayed"
        )


@pytest.mark.parametrize(
    "event",
    [
        "conversation_starred",
        "conversation_unstarred",
        "conversation_pinned",
        "conversation_unpinned",
    ],
)
def test_each_written_event_is_also_readable(event):
    """The general rule the pinned defect broke: an event worth writing is an
    event somebody has to be able to see."""
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )

    source = inspect.getsource(type(conversation_control_service))

    assert source.count(f'"{event}"') >= 2, (
        f"{event} appears once, so it is either written and never shown or "
        "listed and never written"
    )
