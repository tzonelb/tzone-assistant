"""Every timestamp the platform writes is timezone-aware UTC.

The platform stores times in one format and one zone. A naive timestamp is not
merely untidy: it disagrees with every other record by whatever offset the host
happens to be set to, and nothing reading it can tell that it did.

`conversation_memory` is the case that made this worth a test. Its entries are
passed straight into the model's context (`core/engine.py`), so a naive local
clock there means the assistant reasons about "when" using the server's
timezone -- a shared, platform-level setting deciding something a customer sees.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime

import pytest


REPO = pathlib.Path(__file__).resolve().parent.parent

SEARCHED = ("backend", "channels", "core", "database", "tools")


def test_conversation_memory_stamps_an_aware_utc_time():
    from core.conversation_memory import ConversationMemory

    session: dict = {}
    ConversationMemory().append(session, "user", "مرحبا")

    stamped = session["conversation_history"][0]["time"]
    parsed = datetime.fromisoformat(stamped)

    assert parsed.tzinfo is not None, f"{stamped!r} carries no timezone"
    assert parsed.utcoffset().total_seconds() == 0, f"{stamped!r} is not UTC"


def test_conversation_memory_time_can_be_compared_with_a_stored_timestamp():
    """The point of the zone: this value has to line up with message records.

    `message_service` stamps with `utc_now_iso()`. If the two clocks disagree,
    the model sees a history that happened at a different time than the inbox
    says it did.
    """
    from core.conversation_memory import ConversationMemory
    from database.manager import utc_now_iso

    before = datetime.fromisoformat(utc_now_iso())
    session: dict = {}
    ConversationMemory().append(session, "assistant", "أهلا")
    after = datetime.fromisoformat(utc_now_iso())

    stamped = datetime.fromisoformat(session["conversation_history"][0]["time"])

    # One second of slack for the truncation to whole seconds.
    assert before.timestamp() - 1 <= stamped.timestamp() <= after.timestamp() + 1


def _naive_now_calls(path: pathlib.Path) -> list[int]:
    """Line numbers of `datetime.now()` / `datetime.utcnow()` with no zone."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[int] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        name = getattr(func, "attr", None)

        if name == "utcnow":
            found.append(node.lineno)
        elif name == "now" and not node.args and not node.keywords:
            found.append(node.lineno)

    return found


def test_no_module_stamps_a_naive_clock():
    offenders: list[str] = []

    for folder in SEARCHED:
        for path in sorted((REPO / folder).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue

            for line in _naive_now_calls(path):
                offenders.append(f"{path.relative_to(REPO)}:{line}")

    assert not offenders, (
        "these stamp a naive, server-local clock: " + ", ".join(offenders)
    )
