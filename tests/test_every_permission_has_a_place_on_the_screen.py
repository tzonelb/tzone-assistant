"""The Roles screen is built from groups, so a permission with no group vanishes.

The permissions live as a flat list in the schema; `permission_catalogue`
decides how they are grouped for a person to read and set. The failure this
file guards is silent: a permission added to the schema later, whose module
nobody adds to a group, simply does not appear on a screen that iterates the
groups -- an owner cannot grant or deny something they cannot see, and nothing
errors.

So the catalogue raises for an unplaced code rather than bucketing it, and this
walks every permission the schema defines through it. A new permission fails
here until it has a home.
"""

from __future__ import annotations

import pytest

from backend.services import permission_catalogue
from database.schema_control import DEFAULT_PERMISSIONS


ALL_CODES = [code for code, _name, _description in DEFAULT_PERMISSIONS]


def test_every_permission_the_schema_defines_has_a_group():
    unplaced = []

    for code in ALL_CODES:
        try:
            permission_catalogue.group_of(code)
        except KeyError:
            unplaced.append(code)

    assert not unplaced, (
        "These permissions have no group in permission_catalogue.py, so they "
        "would not appear on the Roles screen: " + ", ".join(sorted(unplaced))
    )


def test_no_module_is_claimed_by_two_groups():
    """A module in two groups puts its permissions in two places at once.

    Asserted by rebuilding the index, which raises on a collision -- so this
    test is the one that reads the failure out loud.
    """
    seen = {}
    for group in permission_catalogue.PERMISSION_GROUPS:
        for module in group["modules"]:
            assert module not in seen, (
                f"module {module!r} is in both {seen[module]!r} and "
                f"{group['key']!r}"
            )
            seen[module] = group["key"]


def test_annotate_returns_group_order_and_web_only_for_each():
    annotated = permission_catalogue.annotate(
        [{"code": code, "name": code, "description": ""} for code in ALL_CODES]
    )

    assert len(annotated) == len(ALL_CODES)

    for item in annotated:
        assert "group" in item
        assert "group_order" in item
        assert isinstance(item["web_only"], bool)

    # Sorted by group order, then code within a group: the order the screen
    # renders, so the screen does not have to sort.
    orders = [item["group_order"] for item in annotated]
    assert orders == sorted(orders)


def test_the_web_only_groups_are_the_ones_a_phone_should_not_offer():
    """Publishing and administration are desk work; the rest travel.

    Pinned so a later edit that quietly makes the whole product web-only (or
    drops the flag entirely) is caught -- the phone dimension of task #77 is a
    deliberate choice about two groups, not an accident of a default.
    """
    web_only = {
        group["key"]
        for group in permission_catalogue.PERMISSION_GROUPS
        if group["web_only"]
    }

    assert web_only == {"publishing", "administration"}

    # And the modules those groups cover are exactly what the navigation hides
    # on a small screen.
    assert permission_catalogue.web_only_modules() == frozenset(
        {"scheduler", "comments", "channels", "users", "settings", "subscriptions"}
    )


def test_an_unknown_permission_raises_rather_than_hides():
    with pytest.raises(KeyError):
        permission_catalogue.group_of("something.new")
