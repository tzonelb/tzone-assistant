"""Every setting a company can store either does something, or says it does not.

D-013 records the defect this generalises. Nine reply-policy switches were
offered to an owner, each with a sentence explaining what it did to a customer;
five of them were validated, merged, serialised into the model's payload, and
consulted by nothing. The owner saw a control, set it, and got no change.

The same trap is still laid in `DEFAULT_SETTINGS`. Six keys are seeded into
every company's database at provisioning and read by nothing anywhere — not by
the backend, not by a screen:

* the whole `working_hours` section — a company sets its opening hours and the
  assistant answers at three in the morning exactly as it does at noon;
* `reply_language` — a company chooses a language and the reply is detected
  from the message regardless;
* `escalate_on_low_confidence` — a guardrail switch that guards nothing;
* all three `notifications` keys.

None of them is currently *misleading*, because no screen renders them. That is
the only reason they are not D-013 all over again — and it is a thin reason. The
next person to build a working-hours screen will find the section already in the
defaults, wire the form to it, and ship a feature that saves and does nothing.

So the rule is written down here rather than left to be rediscovered: a key in
`DEFAULT_SETTINGS` is either read somewhere, or listed below as not yet
implemented. Implementing one means deleting its line from this file, which is
the smallest possible reminder that the work is finished.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent

SEARCH_ROOTS = (
    "backend",
    "core",
    "channels",
    "gateway",
    "tools",
    "frontend/src",
)


# Seeded into every company's database and read by nothing. Each line is a
# feature that does not exist yet, not a defect in something that ships.
#
# Delete a line when the setting is implemented. Do not add one to make this
# test pass for a setting that a screen already offers — that is precisely the
# defect D-013 describes.
NOT_IMPLEMENTED: dict[str, str] = {
    "working_hours.enabled": (
        "No code consults opening hours. The assistant answers at three in the "
        "morning exactly as it does at noon."
    ),
    "working_hours.timezone": "Unused until working hours are enforced.",
    "working_hours.days": "Unused until working hours are enforced.",
    "ai_behavior.reply_language": (
        "The reply language is detected from the customer's message. This "
        "preference is stored and never consulted."
    ),
    "ai_behavior.escalate_on_low_confidence": (
        "Confidence already decides the reply through "
        "`minimum_match_confidence` in the reply policy, which is enforced. "
        "This second switch guards nothing."
    ),
    "notifications.notify_on_customer_message": (
        "Notifications are raised for every inbound message, gated only by the "
        "notifications module. This preference is not read."
    ),
    "notifications.notify_on_handover": "Not read.",
    "notifications.notify_on_ai_error": "Not read.",
}


def _default_settings() -> dict[str, dict]:
    source = (ROOT / "database" / "schema_tenant.py").read_text()
    start = source.index("DEFAULT_SETTINGS")
    snippet = source[start:]
    end = snippet.index("\n}\n") + 3

    return ast.literal_eval(ast.parse(snippet[:end]).body[0].value)


# Keys whose names are ordinary English words. A bare search for `enabled` or
# `days` matches half the frontend for reasons that have nothing to do with
# settings, so these are only counted when the section name appears in the same
# file. Without this the check reports noise, and a check that reports noise is
# one somebody eventually deletes.
GENERIC_KEYS = frozenset({"enabled", "timezone", "days"})


def _grep(needle: str, *, roots: tuple[str, ...], includes: tuple[str, ...]) -> set[str]:
    result = subprocess.run(
        ["grep", "-rl", needle, *(f"--include={pattern}" for pattern in includes), *roots],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    return {
        path
        for path in result.stdout.split()
        if "schema_tenant" not in path and "/dist/" not in path
    }


def _mentions(section: str, key: str, *, roots: tuple[str, ...], includes: tuple[str, ...]) -> list[str]:
    """Files that use this setting, as opposed to using its name by accident."""
    files = _grep(key, roots=roots, includes=includes)

    if key in GENERIC_KEYS:
        files &= _grep(section, roots=roots, includes=includes)

    return sorted(files)


def _readers(key: str, section: str = "") -> list[str]:
    """Files anywhere in the codebase that read this setting."""
    return _mentions(
        section,
        key,
        roots=SEARCH_ROOTS,
        includes=("*.py", "*.jsx", "*.js"),
    )


def test_the_defaults_can_be_read():
    """Without this, a change to how the schema is written would make every
    check below pass by finding no settings at all."""
    defaults = _default_settings()

    assert set(defaults) >= {"ai_behavior", "working_hours", "notifications"}


def test_every_setting_is_read_or_declared_unimplemented():
    """The generalisation of D-013.

    A control that saves and changes nothing is worse than no control: the
    owner believes the guardrail is on.
    """
    unaccounted = []

    for section, values in _default_settings().items():
        for key in values:
            qualified = f"{section}.{key}"

            if qualified in NOT_IMPLEMENTED:
                continue

            if not _readers(key, section):
                unaccounted.append(qualified)

    assert not unaccounted, (
        "Setting(s) a company can store that nothing reads:\n  "
        + "\n  ".join(unaccounted)
        + "\n\nImplement it, or add it to NOT_IMPLEMENTED saying why it is "
        "inert. Never add one that a screen already offers."
    )


def test_the_unimplemented_list_has_no_stale_entries():
    """An entry for a setting that has since been implemented is a note that
    tells the next reader the feature is missing when it is not."""
    defaults = _default_settings()
    known = {
        f"{section}.{key}" for section, values in defaults.items() for key in values
    }

    stale = sorted(set(NOT_IMPLEMENTED) - known)

    assert not stale, f"NOT_IMPLEMENTED names settings that no longer exist: {stale}"


def test_an_unimplemented_setting_is_not_offered_by_a_screen():
    """The line between "not built yet" and the D-013 defect.

    A setting nothing reads is harmless while nothing shows it. The moment a
    form offers it, the owner is being told it works.
    """
    offered = []

    for qualified in NOT_IMPLEMENTED:
        section, key = qualified.split(".", 1)

        shown = _mentions(
            section,
            key,
            roots=("frontend/src",),
            includes=("*.jsx", "*.js"),
        )

        if shown:
            offered.append(f"{qualified} in {shown}")

    assert not offered, (
        "A screen offers a setting the backend does not read:\n  "
        + "\n  ".join(offered)
        + "\n\nThat is the D-013 defect — a control that saves and decides "
        "nothing. Implement it or remove the field."
    )


@pytest.mark.parametrize(
    "key",
    ["enabled", "collect_message_delay_seconds", "return_to_ai_timeout_minutes"],
)
def test_the_settings_that_do_work_still_do(key):
    """The other half. If these ever stopped being read, the test above would
    go quiet rather than fail, because it only reports keys not on the list."""
    assert _readers(key, "ai_behavior"), f"{key} used to be read and no longer is"
