"""A module in the catalogue is a module the operator can actually switch off.

The decision this holds: **a module switched off is fully absent, as if it were
never installed.** Not a hidden link over a working API — the operator's switch
has to mean the same thing to the server as it does to the sidebar, or the
operator believes they have turned something off that anybody can still call.

`require_module` is applied at `include_router` in `main.py` rather than inside
each route file, which is the right place — a router added later cannot forget
it if the registration is where the gate lives. It also means the catalogue and
the gates are in two different files and nothing compares them. A module added
to `PLATFORM_MODULES` without a matching `_module(...)` on its router gets a
switch in the console that does nothing to the API.

One module has no router by design. `preferences` is the personal-settings
screen, and it is exempt only because its screen makes no request at all —
which this file checks rather than takes on trust. The moment that screen calls
an endpoint, "there is nothing to gate" stops being true and the exemption
fails, which is the point of writing it down here instead of in a comment.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


# A module with no API to guard, and the screen that has to stay silent for
# that to remain true. Adding an entry means asserting the screen makes no
# request; the test below verifies the assertion rather than believing it.
UI_ONLY: dict[str, str] = {
    "preferences": "frontend/src/pages/dashboard/UISettingsPage.jsx",
}


def _catalogue() -> tuple[str, ...]:
    os.environ.setdefault("TZONE_MASTER_KEY", _a_master_key())

    from backend.services.platform_service import PLATFORM_MODULES

    return PLATFORM_MODULES


def _a_master_key() -> str:
    from backend.security.keyring import generate_master_key

    return generate_master_key()


def _gated() -> set[str]:
    """Every module key passed to a router-gating helper in main.py.

    Two helpers, not one. `_module` carries the operator's module switch *and*
    the subscription check; `_module_unpaid_too` carries only the switch, and
    exactly one router uses it — the dashboard, which holds the subscription
    screen a paused company has to reach in order to un-pause itself.

    Both count as gated here, because the question this file asks is whether
    the operator's switch reaches the API. Whether the *bill* reaches it is a
    different question, asked in
    `tests/test_a_lapsed_subscription_stops_the_company.py`, which pins the
    exemption list to `["dashboard"]` so a second one cannot be added quietly.
    """
    source = (ROOT / "main.py").read_text()

    return set(re.findall(r'_module(?:_unpaid_too)?\("([a-z_]+)"\)', source))


def test_the_catalogue_and_the_gates_can_both_be_read():
    """Without this, renaming the helper would make every check below pass by
    comparing two empty sets."""
    assert len(_catalogue()) > 10
    assert len(_gated()) > 10


def test_every_module_in_the_catalogue_gates_a_router():
    catalogue = set(_catalogue())
    ungated = sorted(catalogue - _gated() - set(UI_ONLY))

    assert not ungated, (
        "Module(s) the console can switch off with no effect on the API:\n  "
        + "\n  ".join(ungated)
        + "\n\nPass the key to `_module(...)` on the router in main.py, or add "
        "it to UI_ONLY naming the screen that makes no request."
    )


def test_no_router_is_gated_on_a_module_that_does_not_exist():
    """The other direction, and the worse one.

    `require_module` validates its key at import time, so a typo here does not
    quietly disable a gate — it stops the application booting. This check turns
    that into a test failure with a name instead of a stack trace at deploy.
    """
    unknown = sorted(_gated() - set(_catalogue()))

    assert not unknown, f"main.py gates modules that are not in the catalogue: {unknown}"


def test_the_ui_only_exemption_names_real_modules():
    stale = sorted(set(UI_ONLY) - set(_catalogue()))

    assert not stale, f"UI_ONLY names modules that no longer exist: {stale}"


def test_a_ui_only_module_really_has_no_api_to_gate():
    """What makes the exemption honest.

    A module is exempt from server-side gating only while its screen asks the
    server for nothing. If that screen starts making requests, those requests
    are reachable with the module switched off — the operator's switch would
    hide the link and leave the endpoint open, which is exactly the failure the
    gates exist to prevent.
    """
    talkative = []

    for module, screen in UI_ONLY.items():
        source = (ROOT / screen).read_text()

        if re.search(r"/api/|apiRequest|fetch\(", source):
            talkative.append(f"{module} ({screen})")

    assert not talkative, (
        "A module exempted as UI-only has a screen that calls the server:\n  "
        + "\n  ".join(talkative)
        + "\n\nSwitching it off would hide the link and leave the endpoint "
        "reachable. Gate its router and remove the exemption."
    )
