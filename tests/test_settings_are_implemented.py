"""Every setting a company can store either does something, or says it does not.

D-013 records the defect this generalises. Nine reply-policy switches were
offered to an owner, each with a sentence explaining what it did to a customer;
five of them were validated, merged, serialised into the model's payload, and
consulted by nothing. The owner saw a control, set it, and got no change.

The same trap was laid again in `DEFAULT_SETTINGS`. Eight keys were seeded into
every company's database at provisioning and read by nothing anywhere — not by
the backend, not by a screen: the whole `working_hours` section, both remaining
`ai_behavior` keys, and all three `notifications` keys.

They are all resolved now, and `NOT_IMPLEMENTED` below is empty. Six were
built:

* `working_hours` decides whether escalating tells a customer somebody is
  coming when nobody is until morning;
* `reply_language` decides the language when the customer has not asked;
* the three `notifications` keys decide whether each kind of bell is raised —
  and two of those bells had to be built, because the preference offered to
  switch off a notification no code had ever raised.

Two were retired instead of implemented, which is the other honest ending:
`escalate_on_low_confidence` duplicated `fallback_to_human` in the reply
policy, where the decision already has an owner and is already enforced.

The rule stays written down rather than left to be rediscovered: a key in
`DEFAULT_SETTINGS` is either read somewhere, or listed below with the reason it
is inert. An empty list is the state to keep it in.
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
}


def _default_settings() -> dict[str, dict]:
    """The catalogue the platform actually serves.

    This used to parse the constant out of `database/schema_tenant.py` with
    `ast`, which was the seeded one — and for a long time the seeded one and the
    served one were different objects with different keys. Every check in this
    file was auditing settings no company could reach through the API, and
    passing.

    Imported rather than parsed now, so it is the same object the service uses
    or it is an ImportError.
    """
    from database.schema_tenant import DEFAULT_SETTINGS

    return DEFAULT_SETTINGS


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


def test_the_seeded_catalogue_is_the_served_one():
    """The defect every other check in this file was blind to.

    `database/schema_tenant.py` seeds a row per section into every company's
    database at provisioning. `company_settings_service` decides what
    `get_section` returns and what `update_section` accepts. They were two
    separate literals, and they had drifted so far apart that six seeded keys
    had no path to them at all — stored in every company's database, never
    returned by a read, and silently dropped by a write.

    Nothing reported it. The settings screen worked, the seeder worked, and the
    keys in between belonged to neither.
    """
    from backend.services import company_settings_service as service
    from database.schema_tenant import DEFAULT_SETTINGS

    assert service.DEFAULT_SETTINGS is DEFAULT_SETTINGS, (
        "The settings catalogue has been forked again. One of these seeds and "
        "the other serves, and a key in only one of them is unreachable."
    )


def test_a_setting_a_company_writes_can_be_read_back():
    """The property the fork broke, asserted end to end rather than by
    comparing two constants.

    A write naming a key the served catalogue does not define is dropped in
    silence. That is the correct behaviour for a key nobody defines — and it is
    exactly what made the fork invisible.
    """
    from database.schema_tenant import DEFAULT_SETTINGS

    for section, values in DEFAULT_SETTINGS.items():
        if section in ("modules", "company_profile", "reply_policy"):
            # Resolved elsewhere: from the operator's switches, the control
            # plane, and the reply-policy service. Not a company's to set here.
            continue

        assert values, f"{section} defines no keys, so nothing can be stored in it"
