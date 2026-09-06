"""How the permission catalogue is organised for a person to read and set.

The permissions themselves live in ``database/schema_control.py`` as a flat
list, ordered by code, which is right for the database and wrong for the Roles
screen: an owner setting up a role should not read thirty checkboxes in
alphabetical order (`analytics.view`, `appointments.view`, `catalogue.view`, …)
and have to assemble the shape of the product in their head. This module is the
one place that says which permissions belong together, in what order the groups
read, and which of them are things a phone cannot do.

**Grouping is presentation, not policy.** It is the same for every company
because it describes the product's own structure -- the inbox, the catalogue,
publishing -- not a decision any one owner makes. What each *role* may do is
still per-company and still lives in the role's permission codes; this only
decides how those codes are laid out to be chosen.

**`web_only` is the phone dimension of task #77.** Some of what an employee may
do belongs to a desk: composing and approving scheduled posts, connecting a
channel, editing roles, changing the plan. The mobile app is the inbox and the
things around a live conversation. A group marked ``web_only`` is shown on the
Roles screen with that said plainly, and its modules are hidden from the
navigation on a small screen -- so the permission still means what it says on a
laptop, and the phone simply does not offer the screens it would open.

A code that this file does not place is a bug, not a default: ``group_of``
raises for an unknown code and a test walks every permission in the catalogue
through it, so a permission added later without a home here fails loudly instead
of vanishing from the screen.
"""

from __future__ import annotations

from typing import Any


# The module prefix of a permission code (`conversations.view` -> `conversations`)
# is what places it. Every module the catalogue defines maps to exactly one
# group below; the groups are listed in the order they should read on the
# screen, top to bottom.
PERMISSION_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "key": "inbox",
        "label": "Inbox & customers",
        "description": "The shared inbox, replies, and the customer directory.",
        "web_only": False,
        "modules": ("conversations", "customers"),
    },
    {
        "key": "calls",
        "label": "Calls",
        "description": "The live phone line.",
        "web_only": False,
        "modules": ("dialer",),
    },
    {
        "key": "knowledge",
        "label": "Knowledge base",
        "description": "What the assistant answers from.",
        "web_only": False,
        "modules": ("knowledge",),
    },
    {
        "key": "catalogue",
        "label": "Catalogue",
        "description": "Products and categories.",
        "web_only": False,
        "modules": ("catalogue",),
    },
    {
        "key": "work",
        "label": "Tasks & appointments",
        "description": "Follow-ups and the appointment calendar.",
        "web_only": False,
        "modules": ("tasks", "appointments"),
    },
    {
        "key": "team",
        "label": "Team chat",
        "description": "Internal channels between staff.",
        "web_only": False,
        "modules": ("team_chat",),
    },
    {
        "key": "insights",
        "label": "Dashboard & analytics",
        "description": "The numbers on how the company is doing.",
        "web_only": False,
        "modules": ("dashboard", "analytics"),
    },
    {
        "key": "publishing",
        "label": "Publishing & comments",
        "description": "The scheduling calendar and replies to post comments.",
        # Composing and approving posts is desk work; the phone app does not
        # carry the publisher. This is the concrete web-only case task #77 names.
        "web_only": True,
        "modules": ("scheduler", "comments"),
    },
    {
        "key": "administration",
        "label": "Administration",
        "description": (
            "Connecting channels, team roles, company settings and the "
            "subscription — set up from a desk."
        ),
        "web_only": True,
        "modules": ("channels", "users", "settings", "subscriptions"),
    },
)


def _module_index() -> dict[str, dict[str, Any]]:
    """module name -> its group. Built once, and asserts no module is claimed
    by two groups -- a mistake that would put the same permission in two places
    on the screen."""
    index: dict[str, dict[str, Any]] = {}

    for group in PERMISSION_GROUPS:
        for module in group["modules"]:
            if module in index:
                raise ValueError(
                    f"module {module!r} is in two permission groups: "
                    f"{index[module]['key']} and {group['key']}"
                )

            index[module] = group

    return index


_MODULES = _module_index()


def module_of(code: str) -> str:
    """The module a permission code belongs to -- the part before the dot."""
    return str(code or "").split(".", 1)[0]


def group_of(code: str) -> dict[str, Any]:
    """The group a permission code belongs to, or raise for an unknown one.

    Raising rather than bucketing the stragglers into an "other" group is the
    point: a permission with no home here would otherwise disappear from a
    screen that is built from these groups, and nobody would see it go.
    """
    module = module_of(code)

    group = _MODULES.get(module)

    if group is None:
        raise KeyError(
            f"permission {code!r} (module {module!r}) has no group in "
            "permission_catalogue.py -- add its module to a group"
        )

    return group


def web_only_modules() -> frozenset[str]:
    """Every module whose screens a small-screen device should not offer."""
    return frozenset(
        module
        for group in PERMISSION_GROUPS
        if group["web_only"]
        for module in group["modules"]
    )


def group_summaries() -> list[dict[str, Any]]:
    """The groups as the API serves them: key, label, description, web_only, and
    the order they read. The modules are left out -- the screen groups the
    permissions it is given by their `group` field and needs the labels, not the
    routing detail."""
    return [
        {
            "key": group["key"],
            "label": group["label"],
            "description": group["description"],
            "web_only": bool(group["web_only"]),
            "order": order,
        }
        for order, group in enumerate(PERMISSION_GROUPS)
    ]


def annotate(permissions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the permissions with `group`, `group_order` and `web_only` added,
    sorted into group order and then by code within a group.

    The screen could derive all of this from the codes itself, but then the
    grouping would live in two places and drift; the server owns it and hands
    the screen something already in the right order.
    """
    order_of = {group["key"]: index for index, group in enumerate(PERMISSION_GROUPS)}

    annotated = []

    for permission in permissions:
        group = group_of(permission["code"])
        item = dict(permission)
        item["group"] = group["key"]
        item["group_order"] = order_of[group["key"]]
        item["web_only"] = bool(group["web_only"])
        annotated.append(item)

    annotated.sort(key=lambda item: (item["group_order"], item["code"]))

    return annotated
