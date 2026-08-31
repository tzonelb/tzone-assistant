"""A menu entry that leads to a 403 is a dead button with a tooltip.

The sidebar hides an entry when the employee lacks the permission it declares.
An entry that declares none is shown to everybody — including the employee
whose role deliberately withholds the screen. They click it, the screen mounts,
every request it makes is refused, and the result is a page of empty tables that
looks like a broken product rather than a restriction their manager chose.

That is what `catalogue` did: `/api/catalogue` has required `catalogue.view`
since it was written, and the role screen has offered the permission since the
control schema seeded it, but "Master Catalogue" was in everyone's menu. Eight
more entries were in the same state, so this is a class of defect and not a
typo — hence a sweep rather than a fix.

The check is one-directional on purpose. Declaring a permission the API does not
demand is a decision an owner may want (a screen kept out of a role's way), so
it is not reported. Demanding one the menu never mentions always is.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SIDEBAR = ROOT / "frontend/src/components/layout/SidebarV2.jsx"

# Which router serves each menu entry. This cannot be derived — the menu knows
# a browser path and the API knows a URL prefix, and nothing in either file ties
# `/test-ai` to `ai_teaching.py`. Adding a screen means adding a line here, and
# a screen missing from this map is reported below rather than skipped.
ROUTER_FOR_ENTRY = {
    "dashboard": "dashboard.py",
    "conversations": "conversations.py",
    "tasks": "tickets.py",
    "appointments": "appointments.py",
    "team_chat": "team_chat.py",
    "customers": "customers.py",
    "broadcast": "broadcasts.py",
    "calls": "calls.py",
    "dialer": "dialer.py",
    "test_ai": "ai_teaching.py",
    "saved_replies": "saved_replies.py",
    "publish": "scheduler.py",
    "comments": "comments.py",
    "catalogue": "catalogue.py",
    "analytics": "analytics.py",
    "company_settings": "company_settings.py",
    # Appearance is stored in the browser: UISettingsPage.jsx calls no API at
    # all, so there is no permission for it to declare.
    "settings": None,
    # `/api/notifications` guards on the session alone — every employee sees
    # their own notifications and nobody else's, so a permission would only
    # take away a screen that shows nothing to take.
    "notifications": None,
}

ENTRY = re.compile(
    r'\["(?P<key>[a-z_]+)",\s*"(?P<path>/[^"]*)",\s*"(?P<label>[^"]*)",'
    r'\s*\w+(?P<rest>[^\]]*)\]'
)

PERMISSION = re.compile(r'require_permission\(\s*"(?P<code>[a-z_.]+)"\s*\)')


def _menu() -> dict[str, set[str]]:
    """Each menu entry's key, mapped to the permissions it declares."""
    entries: dict[str, set[str]] = {}

    for match in ENTRY.finditer(SIDEBAR.read_text()):
        declared = set(re.findall(r'"([a-z_.]+)"', match.group("rest")))
        entries[match.group("key")] = declared

    return entries


def _required(router: str) -> set[str]:
    return {
        found.group("code")
        for found in PERMISSION.finditer(
            (ROOT / "backend/api/routes" / router).read_text()
        )
    }


def test_the_sweep_actually_reads_the_menu():
    """A regex that stops matching stops guarding."""
    entries = _menu()

    assert len(entries) >= 18, f"only {len(entries)} menu entries parsed"
    assert "catalogue" in entries


def test_every_screen_in_the_menu_is_mapped_to_its_router():
    unmapped = sorted(set(_menu()) - set(ROUTER_FOR_ENTRY))

    assert not unmapped, (
        "A new screen was added to the sidebar without recording which router "
        "serves it, so nothing checks whether its menu entry can be reached: "
        + ", ".join(unmapped)
    )


def test_the_menu_declares_the_permission_the_screen_will_be_asked_for():
    undeclared = []

    for key, declared in sorted(_menu().items()):
        router = ROUTER_FOR_ENTRY.get(key)

        if router is None:
            continue

        required = _required(router)

        if not required:
            continue

        # One entry, one screen, several endpoints: a screen whose reads need
        # `x.view` and whose writes need `x.manage` is reachable by anyone
        # holding either, so any overlap is enough.
        if declared & required:
            continue

        undeclared.append(
            f"{key} -> backend/api/routes/{router} requires "
            f"{' or '.join(sorted(required))}, menu declares "
            f"{sorted(declared) or 'nothing'}"
        )

    assert not undeclared, (
        "A menu entry is shown to an employee the API will refuse:\n  "
        + "\n  ".join(undeclared)
        + "\n\nAdd the permission as the fifth element of the entry in "
        "SidebarV2.jsx, so the entry is hidden from the roles that cannot use "
        "it instead of leading them to an empty screen."
    )
