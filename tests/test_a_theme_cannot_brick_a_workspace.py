"""A company's own theme must not be able to break that company's workspace.

`_validate_theme` checked only `accent` and `accent2`. Every other token was
stored exactly as it arrived, whatever it was, and that had two consequences a
company could inflict on itself with one request:

* A deeply nested value committed to the database and then failed the response
  serialiser. From that moment `GET /api/platform-ui/config` -- the call the app
  makes on sign-in to learn its modules, branding and layout -- answered 500 for
  that company. The Theme Studio screen could not issue the corrective write,
  because the app could no longer load its own configuration.
* An unbounded string sat in the *shared* control database, which the module
  gate re-reads and re-parses on every gated request.

Every token in `DEFAULT_THEME_TOKENS` is a scalar, so the shape is not a new
rule invented here -- it is the one the defaults already describe.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def service(platform, monkeypatch):
    import sys

    from database.manager import DatabaseManager
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)

        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from backend.services.platform_service import platform_service

    return platform_service


def _nested(depth: int):
    value: object = "x"

    for _ in range(depth):
        value = [value]

    return value


def test_a_deeply_nested_token_is_refused(service, alpha):
    """The payload that used to commit and then break every later read."""
    from backend.services.platform_service import PlatformError

    with pytest.raises(PlatformError):
        service.update_theme(alpha["id"], {"shape": {"cardFill": _nested(300)}})


def test_the_workspace_config_still_loads_after_the_refusal(service, alpha):
    """The refusal leaves nothing behind: the screen keeps working.

    This is the assertion that matters. A guard that raised *after* writing
    would pass the test above and still brick the workspace.
    """
    from backend.services.platform_service import PlatformError

    with pytest.raises(PlatformError):
        service.update_theme(alpha["id"], {"shape": {"cardFill": _nested(300)}})

    config = service.get_platform_config(alpha["id"])

    # It has to survive the round trip the endpoint performs, not merely exist.
    assert json.dumps(config)


def test_an_oversized_string_is_refused(service, alpha):
    from backend.services.platform_service import MAX_BRANDING_VALUE, PlatformError

    with pytest.raises(PlatformError):
        service.update_theme(
            alpha["id"], {"color": {"rail": "x" * (MAX_BRANDING_VALUE + 1)}}
        )


@pytest.mark.parametrize(
    ("group", "key", "value"),
    [
        ("color", "mode", {"arbitrary": ["json"]}),
        ("type", "baseSize", "not a number"),
        ("shape", "cardFill", "yes"),
        ("layout", "railWidth", float("inf")),
        ("layout", "railWidth", float("nan")),
    ],
)
def test_a_token_of_the_wrong_kind_is_refused(service, alpha, group, key, value):
    from backend.services.platform_service import PlatformError

    with pytest.raises(PlatformError):
        service.update_theme(alpha["id"], {group: {key: value}})


def test_a_real_theme_is_still_accepted(service, alpha):
    """The tokens a company actually sets keep working."""
    service.update_theme(
        alpha["id"],
        {
            "color": {"accent": "#1689e8", "mode": "dark"},
            "type": {"baseSize": 16, "headingScale": 1.2},
            "shape": {"radius": 12, "cardFill": False},
        },
    )

    # `theme` on the config is the stored-over-defaults resolution the
    # endpoint serves as `tokens`.
    theme = service.get_platform_config(alpha["id"])["theme"]

    assert theme["color"]["mode"] == "dark"
    assert theme["type"]["baseSize"] == 16
    assert theme["shape"]["cardFill"] is False
