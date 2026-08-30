"""Theme Studio: a draft nobody sees, a publish everybody does, and a limit.

Three properties, and the third is the one worth writing down.

* A draft is invisible. An administrator drags a slider forty times while
  deciding; none of those forty saves may reach a single workspace. Only
  publishing does, and publishing is numbered so the version before it is still
  there to go back to.

* A published theme reaches the workspaces beneath it, layer by layer, and a
  layer only overrides the keys it actually names. The alternative — storing a
  merged snapshot — silently reset every token the author had not touched back
  to the bundled default the moment their draft went live.

* **A theme cannot open a door.** `modules` in a theme decides what is drawn in
  the menu. The platform operator's own switch decides what a company may
  reach, and the two meet in `visible_modules`, which may narrow the operator's
  answer and may never widen it. Without that, styling a workspace would be a
  way into a module somebody had deliberately switched off — which is the
  difference between a design tool and a privilege escalation.
"""

from __future__ import annotations

import pytest


PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    """Point every service and mounted router at this test's databases."""
    import sys

    import database.manager as manager_module

    # Imported before the sweep: a module imported afterwards would bind this
    # test's temporary manager permanently and corrupt later test files.
    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.platform  # noqa: F401
    import backend.api.routes.platform_ui  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.platform_service  # noqa: F401
    import backend.services.platform_ui_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []

    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.platform_ui_service" in rebound

    from backend.services.platform_ui_service import platform_ui_service

    return platform_ui_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, platform as platform_routes, platform_ui

    app = FastAPI()
    app.include_router(platform_routes.router)
    app.include_router(platform_ui.router)
    app.include_router(auth.router)

    return TestClient(app)


def _make_user(email, *, is_super_admin=False) -> int:
    from backend.services.auth_service import auth_service

    return auth_service.create_user(
        email=email,
        password=PASSWORD,
        full_name="Test Person",
        is_super_admin=is_super_admin,
    )


def _employ(platform, company, user_id: int, *, role_code: str | None = None) -> None:
    from database.manager import utc_now_iso

    with platform["manager"].control() as conn:
        role_id = None

        if role_code:
            row = conn.execute(
                "SELECT id FROM roles WHERE company_id = ? AND code = ? LIMIT 1",
                (company["id"], role_code),
            ).fetchone()
            assert row, f"the fixture company has no {role_code} role"
            role_id = int(row["id"])

        conn.execute(
            """
            INSERT INTO company_users (
                company_id, user_id, role_id, status, created_at
            )
            VALUES (?, ?, ?, 'active', ?)
            """,
            (company["id"], user_id, role_id, utc_now_iso()),
        )
        conn.commit()


def _token(client, platform, company, email, **kwargs) -> str:
    user_id = _make_user(email, is_super_admin=kwargs.pop("is_super_admin", False))
    _employ(platform, company, user_id, **kwargs)

    response = client.post(
        "/api/auth/login",
        json={
            "workspace_code": company["workspace_code"],
            "company": company["name"],
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.text

    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin(client, platform, alpha) -> str:
    return _token(
        client, platform, alpha, "operator@platform.example.com", is_super_admin=True
    )


# ----------------------------------------------------------------------
# The draft is private until it is published
# ----------------------------------------------------------------------


def test_a_draft_changes_nothing_anybody_sees(client, platform, alpha):
    token = _admin(client, platform, alpha)

    before = client.get("/api/platform-ui/config", headers=_bearer(token))
    assert before.status_code == 200, before.text
    assert before.json()["version"] == 0

    created = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "tokens": {"color": {"accent": "#b68235"}}},
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "draft"

    after = client.get("/api/platform-ui/config", headers=_bearer(token))
    assert after.status_code == 200
    assert after.json()["tokens"]["color"]["accent"] != "#b68235"
    assert after.json()["version"] == 0


def test_publishing_reaches_the_workspace(client, platform, alpha):
    token = _admin(client, platform, alpha)

    theme_id = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "tokens": {"color": {"accent": "#b68235"}}},
    ).json()["id"]

    published = client.post(
        f"/api/platform-ui/themes/{theme_id}/publish",
        headers=_bearer(token),
        json={"reason": "Switch to the brand accent"},
    )
    assert published.status_code == 200, published.text
    assert published.json()["version"] == 1

    config = client.get("/api/platform-ui/config", headers=_bearer(token)).json()

    assert config["tokens"]["color"]["accent"] == "#b68235"
    assert config["version"] == 1
    # The keys this theme never mentioned still come from the defaults rather
    # than disappearing: a patch is not a snapshot.
    assert config["tokens"]["type"]["headingFont"] == "Inter"
    assert config["tokens"]["shape"]["radius"] == 16


def test_a_publish_is_recorded_with_its_reason(client, platform, alpha):
    """"The whole platform changed colour" is a question asked weeks later."""
    token = _admin(client, platform, alpha)

    theme_id = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "tokens": {"shape": {"radius": 4}}},
    ).json()["id"]

    client.post(
        f"/api/platform-ui/themes/{theme_id}/publish",
        headers=_bearer(token),
        json={"reason": "Squarer corners for the print brand"},
    )

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT * FROM audit_log WHERE action = 'ui_theme.published' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert row is not None
    assert "Squarer corners for the print brand" in row["data_json"]


def test_a_publish_needs_a_reason(client, platform, alpha):
    token = _admin(client, platform, alpha)

    theme_id = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "tokens": {"shape": {"radius": 4}}},
    ).json()["id"]

    refused = client.post(
        f"/api/platform-ui/themes/{theme_id}/publish",
        headers=_bearer(token),
        json={"reason": ""},
    )

    assert refused.status_code == 422, refused.text


# ----------------------------------------------------------------------
# Versions, and getting one back
# ----------------------------------------------------------------------


def test_the_previous_version_is_archived_and_restorable(client, platform, alpha):
    token = _admin(client, platform, alpha)

    def publish(accent: str) -> int:
        theme_id = client.post(
            "/api/platform-ui/themes",
            headers=_bearer(token),
            json={"scope_type": "platform", "tokens": {"color": {"accent": accent}}},
        ).json()["id"]

        response = client.post(
            f"/api/platform-ui/themes/{theme_id}/publish",
            headers=_bearer(token),
            json={"reason": f"accent {accent}"},
        )
        assert response.status_code == 200, response.text
        return theme_id

    first = publish("#b68235")
    publish("#1f8a52")

    themes = client.get(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        params={"scope_type": "platform"},
    ).json()["themes"]

    by_id = {theme["id"]: theme for theme in themes}

    assert by_id[first]["status"] == "archived"
    assert by_id[first]["version"] == 1

    config = client.get("/api/platform-ui/config", headers=_bearer(token)).json()
    assert config["tokens"]["color"]["accent"] == "#1f8a52"
    assert config["version"] == 2

    restored = client.post(
        f"/api/platform-ui/themes/{first}/restore", headers=_bearer(token), json={}
    )
    assert restored.status_code == 201, restored.text
    assert restored.json()["status"] == "draft"
    assert restored.json()["tokens"]["color"]["accent"] == "#b68235"

    # Restoring is a copy, not a revert: nothing has changed for anybody until
    # the restored draft is published in its turn.
    assert (
        client.get("/api/platform-ui/config", headers=_bearer(token)).json()["tokens"][
            "color"
        ]["accent"]
        == "#1f8a52"
    )


def test_only_a_draft_can_be_edited_or_published(client, platform, alpha):
    token = _admin(client, platform, alpha)

    theme_id = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "tokens": {"shape": {"radius": 8}}},
    ).json()["id"]

    client.post(
        f"/api/platform-ui/themes/{theme_id}/publish",
        headers=_bearer(token),
        json={"reason": "first"},
    )

    edited = client.patch(
        f"/api/platform-ui/themes/{theme_id}",
        headers=_bearer(token),
        json={"tokens": {"shape": {"radius": 2}}},
    )
    assert edited.status_code == 400, edited.text

    republished = client.post(
        f"/api/platform-ui/themes/{theme_id}/publish",
        headers=_bearer(token),
        json={"reason": "again"},
    )
    assert republished.status_code == 400, republished.text


def test_an_edit_merges_rather_than_replaces(client, platform, alpha):
    """Theme Studio saves one control at a time. Each save must keep the last."""
    token = _admin(client, platform, alpha)

    theme_id = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "tokens": {"color": {"accent": "#b68235"}}},
    ).json()["id"]

    updated = client.patch(
        f"/api/platform-ui/themes/{theme_id}",
        headers=_bearer(token),
        json={"tokens": {"shape": {"radius": 4}}},
    )
    assert updated.status_code == 200, updated.text

    tokens = updated.json()["tokens"]
    assert tokens["color"]["accent"] == "#b68235"
    assert tokens["shape"]["radius"] == 4


# ----------------------------------------------------------------------
# The limit: a theme decides what is drawn, never what may be reached
# ----------------------------------------------------------------------


def test_a_theme_can_hide_a_module(client, platform, alpha):
    token = _admin(client, platform, alpha)

    theme_id = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={
            "scope_type": "platform",
            "modules": {"catalogue": {"visible": False}},
        },
    ).json()["id"]

    client.post(
        f"/api/platform-ui/themes/{theme_id}/publish",
        headers=_bearer(token),
        json={"reason": "Catalogue is not part of the launch"},
    )

    modules = client.get("/api/platform-ui/config", headers=_bearer(token)).json()[
        "modules"
    ]

    assert modules["catalogue"] is False
    assert modules["conversations"] is True


def test_a_theme_cannot_switch_a_module_back_on(client, platform, alpha):
    """The property this file exists for.

    The operator switched `customers` off for this company. A theme that says
    the entry is visible must not put it back: `modules` in the workspace
    configuration is what `ModuleRoute` and `require_module` both read, so
    widening it here would be a way into a module somebody closed on purpose.
    """
    from backend.services.platform_service import platform_service

    platform_service.update_platform_config(alpha["id"], modules={"customers": False})

    token = _admin(client, platform, alpha)

    theme_id = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "modules": {"customers": {"visible": True}}},
    ).json()["id"]

    published = client.post(
        f"/api/platform-ui/themes/{theme_id}/publish",
        headers=_bearer(token),
        json={"reason": "Try to reopen customers"},
    )
    assert published.status_code == 200, published.text

    modules = client.get("/api/platform-ui/config", headers=_bearer(token)).json()[
        "modules"
    ]

    assert modules["customers"] is False


def test_a_theme_cannot_name_a_module_this_platform_does_not_have(
    client, platform, alpha
):
    token = _admin(client, platform, alpha)

    refused = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "modules": {"nuclear_launch": {"visible": True}}},
    )

    assert refused.status_code == 400, refused.text


# ----------------------------------------------------------------------
# Who may write which scope
# ----------------------------------------------------------------------


def test_an_employee_cannot_publish_to_every_workspace(client, platform, alpha):
    """A platform theme reaches every tenant. That is not a company's decision."""
    token = _token(client, platform, alpha, "clerk@alpha.example.com")

    refused = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "tokens": {"color": {"accent": "#b68235"}}},
    )

    assert refused.status_code == 403, refused.text

    listed = client.get(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        params={"scope_type": "platform"},
    )
    assert listed.status_code == 403, listed.text


def test_an_owner_may_write_their_own_company_scope(client, platform, alpha):
    token = _token(
        client, platform, alpha, "owner@alpha.example.com", role_code="owner"
    )

    created = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={
            "scope_type": "company",
            "scope_id": str(alpha["id"]),
            "tokens": {"color": {"accent": "#1f8a52"}},
        },
    )

    assert created.status_code == 201, created.text


def test_an_owner_cannot_write_another_company_scope(client, platform, alpha, beta):
    """The tenant boundary, in the one place a theme could have crossed it."""
    token = _token(
        client, platform, alpha, "owner2@alpha.example.com", role_code="owner"
    )

    refused = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={
            "scope_type": "company",
            "scope_id": str(beta["id"]),
            "tokens": {"color": {"accent": "#1f8a52"}},
        },
    )

    assert refused.status_code == 403, refused.text


def test_a_company_theme_reaches_only_that_company(client, platform, alpha, beta):
    token = _admin(client, platform, alpha)

    theme_id = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={
            "scope_type": "company",
            "scope_id": str(alpha["id"]),
            "tokens": {"color": {"accent": "#1f8a52"}},
        },
    ).json()["id"]

    client.post(
        f"/api/platform-ui/themes/{theme_id}/publish",
        headers=_bearer(token),
        json={"reason": "Alpha's own accent"},
    )

    from backend.services.platform_ui_service import platform_ui_service

    assert (
        platform_ui_service.resolve(company_id=alpha["id"])["tokens"]["color"]["accent"]
        == "#1f8a52"
    )
    assert (
        platform_ui_service.resolve(company_id=beta["id"])["tokens"]["color"]["accent"]
        != "#1f8a52"
    )


# ----------------------------------------------------------------------
# What the studio refuses to store
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "tokens",
    [
        {"color": {"accent": "not a colour"}},
        {"color": {"mode": "sepia"}},
        {"type": {"headingFont": "Comic Sans MS"}},
        {"type": {"baseSize": 400}},
        {"shape": {"radius": -4}},
        {"shape": {"cardFill": "yes"}},
        {"layout": {"railWidth": 100000}},
        {"layout": {"direction": "sideways"}},
        {"nonsense": {"key": 1}},
        {"color": {"unknownKey": 1}},
    ],
)
def test_a_token_that_cannot_render_is_refused(client, platform, alpha, tokens):
    """A theme that draws wrong is the failure this validation exists for."""
    token = _admin(client, platform, alpha)

    refused = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "tokens": tokens},
    )

    assert refused.status_code in (400, 422), refused.text


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_number_never_reaches_the_control_database(service, value):
    """Infinity and NaN are floats, and they serialise to JSON nothing reads back.

    They cannot be posted through the test client — its own encoder refuses
    them — so the guard is checked where a caller with a laxer encoder would
    actually reach it.
    """
    from backend.services.platform_ui_service import PlatformUiError

    with pytest.raises(PlatformUiError):
        service.create_draft(
            scope_type="platform",
            scope_id=None,
            tokens={"layout": {"railWidth": value}},
        )


def test_a_platform_theme_takes_no_scope_id(client, platform, alpha):
    token = _admin(client, platform, alpha)

    refused = client.post(
        "/api/platform-ui/themes",
        headers=_bearer(token),
        json={"scope_type": "platform", "scope_id": "7", "tokens": {}},
    )

    assert refused.status_code == 400, refused.text


def test_a_missing_theme_is_a_404_not_a_crash(client, platform, alpha):
    token = _admin(client, platform, alpha)

    response = client.patch(
        "/api/platform-ui/themes/999999",
        headers=_bearer(token),
        json={"tokens": {}},
    )

    assert response.status_code == 404, response.text
