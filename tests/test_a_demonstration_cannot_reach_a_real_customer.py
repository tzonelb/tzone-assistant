"""A workspace anyone can create must not become a way to message strangers.

Sign-up is self-service, which is the point of a demonstration and also the
whole risk: an account created from a form, by anybody, that can connect a real
WhatsApp number is a spam relay running on the operator's infrastructure with
the operator's name on the abuse report. So a demo workspace is defined by what
it cannot reach.

The line is drawn at **connecting a channel**, one step earlier than the
obvious place, and this file exists to hold it there. Every outbound path --
a manual reply, a broadcast, a scheduled post, a comment reply, the assistant's
own answer -- resolves the company's channel credentials before it can send,
and refuses when there are none. So a workspace that cannot connect cannot send
by any route, including a route written next year by somebody who never read
`demo_gate.py`. Gating each sender instead would be six checks to remember and
a seventh that ships without one.

Two properties, and the second is the one a future change is likeliest to
break: a demo workspace is refused at every route that writes a channel
account, and it is refused at *all* of them -- an `update` that flips a
disabled account back to active is a way round a check that only guards
`create`, and that exact hole has already been found once on this platform in
the plan-limit code.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# Imported here, at module scope, and not inside the fixture or the client
# helper -- which is where it naturally wanted to go.
#
# A module imported for the first time *while* `database.manager.database_manager`
# is monkeypatched binds the test manager permanently: the import runs
# `from database.manager import database_manager`, gets the patched value, and
# monkeypatch never restores it because it was never patched, it was born that
# way. Pulling `channels` in inside the fixture therefore poisoned every
# service it imports for the rest of the session, and
# `test_activity_endpoints.py` -- which asserts on *which* modules its own
# fixture rebound -- failed in a batch and passed alone.
#
# Importing before any patching means these modules hold the real singleton,
# and the fixtures below rebind them the same way every other test file does.
import backend.api.routes.channels  # noqa: E402,F401
import backend.services.activity_service  # noqa: E402,F401
import backend.services.auth_service  # noqa: E402,F401
import backend.services.channel_account_service  # noqa: E402,F401
import backend.services.demo_gate  # noqa: E402,F401
import channels.credentials  # noqa: E402,F401


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def wired(platform, monkeypatch):
    """Point the modules this test reaches at the fixture's databases.

    Matched on identity with the global singleton -- `is original` -- rather
    than on "any DatabaseManager that is not mine". The broader condition
    rebinds modules another test's fixture had already rebound, and
    `test_activity_endpoints.py` asserts on the *contents* of its own rebound
    list, so the two files failed together and passed apart. Narrower is both
    correct and neighbourly.

    The gate's cache is a process-wide singleton keyed by company id, and every
    test database numbers its first company 1 -- so an answer cached here would
    be read by the next test's unrelated company 1. Dropped on the way in and
    on the way out.
    """
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    from backend.services.demo_gate import demo_gate

    demo_gate.invalidate()
    yield test_manager
    demo_gate.invalidate()


def _mark_demo(company_id: int, is_demo: bool) -> None:
    from database.manager import database_manager
    from backend.services.demo_gate import demo_gate

    with database_manager.control() as conn:
        conn.execute(
            "UPDATE companies SET is_demo = ? WHERE id = ?",
            (1 if is_demo else 0, company_id),
        )
        conn.commit()

    demo_gate.invalidate(company_id)


def _client(company_id: int, monkeypatch):
    """A client signed in as an owner of this company, with the gate live.

    Only the two things this file is not testing are stubbed -- who is signed
    in, and whether they hold `channels.manage`. `require_permission` still
    runs, `refuse_a_demonstration` still runs, and the demo gate still reads
    the real control database. Overriding `manage_context` itself would have
    been simpler and would have removed the thing under test.

    `require_permission(...)` builds a new function on every call, so it cannot
    be used as an override key -- the object in the route's signature is not
    the one a second call returns. `get_current_user` is a module-level
    function and is the same object everywhere, which is why the substitution
    happens there.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import channels
    from backend.services import auth_service as auth_module
    from backend.services.auth_service import get_current_user

    signed_in = {"id": 1, "is_super_admin": False, "active_company_id": company_id}

    monkeypatch.setattr(
        auth_module.auth_service, "has_permission", lambda **_: True
    )
    monkeypatch.setattr(
        auth_module.auth_service, "resolve_company_id", lambda _user: company_id
    )

    app = FastAPI()
    app.include_router(channels.router)
    app.dependency_overrides[get_current_user] = lambda: dict(signed_in)

    return TestClient(app)


# ------------------------------------------------------------------ the shape


def test_every_route_that_writes_a_channel_goes_through_the_same_door():
    """A fourth write route must not be able to arrive without the refusal.

    Read from the source rather than exercised, because the failure is a route
    that was never written to be exercised -- one added later with the bare
    permission dependency copied from an older route.
    """
    source = (ROOT / "backend/api/routes/channels.py").read_text()
    tree = ast.parse(source)

    writers = {"create_channel", "update_channel", "delete_channel"}
    seen = set()
    bare = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        arguments = node.args
        defaults = [ast.unparse(d) for d in arguments.defaults + [
            d for d in arguments.kw_defaults if d is not None
        ]]

        # Every route whose own dependency is the raw permission, rather than
        # the shared door, writes without the workspace check.
        if any('require_permission("channels.manage")' in d for d in defaults):
            if node.name != "manage_context":
                bare.append(node.name)

        if any("manage_context" in d for d in defaults):
            seen.add(node.name)

    assert not bare, (
        "A channel write route asks for `channels.manage` directly instead of "
        "going through `manage_context`, so it never consults the demo gate: "
        + ", ".join(sorted(bare))
    )

    assert writers <= seen, (
        "These write routes no longer go through `manage_context`: "
        + ", ".join(sorted(writers - seen))
    )


# ------------------------------------------------------------------ behaviour


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("post", "/api/channels", {"channel": "telegram", "name": "Sales", "access_token": "1:AA"}),
        ("patch", "/api/channels/1", {"status": "active"}),
        ("delete", "/api/channels/1", None),
    ],
)
def test_a_demonstration_cannot_write_a_channel_account(
    wired, alpha, monkeypatch, method, path, body
):
    _mark_demo(alpha["id"], True)

    client = _client(alpha["id"], monkeypatch)
    response = getattr(client, method)(path, **({"json": body} if body else {}))

    assert response.status_code == 403, response.text
    assert "demonstration" in response.json()["detail"].lower()
    assert "activation code" in response.json()["detail"].lower()


def test_the_refusal_lifts_the_moment_the_workspace_is_activated(
    wired, alpha, monkeypatch
):
    """The gate must not be a one-way door, and must not lag behind activation.

    An owner who has just typed their code is watching the screen, so the
    cached answer has to be dropped rather than waited out.
    """
    _mark_demo(alpha["id"], True)

    client = _client(alpha["id"], monkeypatch)
    refused = client.post(
        "/api/channels",
        json={"channel": "telegram", "name": "Sales", "access_token": "1:AA"},
    )

    assert refused.status_code == 403

    _mark_demo(alpha["id"], False)

    allowed = client.post(
        "/api/channels",
        json={"channel": "telegram", "name": "Sales", "access_token": "1:AA"},
    )

    # Anything but the demo refusal: the workspace is past this gate now, and
    # whether the credentials themselves are good is a different question this
    # test has no business asserting.
    assert allowed.status_code != 403, allowed.text


def test_reading_the_channels_screen_still_works_for_a_demonstration(
    wired, alpha, monkeypatch
):
    """A refusal the owner cannot see explained is a broken screen.

    The Channels screen has to open, so it can say why connecting is refused
    and where the activation code goes.
    """
    _mark_demo(alpha["id"], True)

    response = _client(alpha["id"], monkeypatch).get("/api/channels")

    assert response.status_code == 200, response.text


def test_an_unreadable_company_is_treated_as_a_demonstration(wired):
    """This gate fails closed, unlike the module and subscription gates.

    Those two fail open so an unreadable control plane cannot take paying
    companies off the air. This one answers a different question: the cost of
    guessing wrong here is a stranger connecting a real channel.
    """
    from backend.services.demo_gate import demo_gate

    assert demo_gate.is_demo(None) is True
    assert demo_gate.is_demo("not-a-company") is True
    assert demo_gate.is_demo(9_999_999) is True


# ---------------------------------------------------------- the deepest door


def test_a_demonstration_is_denied_the_single_company_environment_token(
    wired, alpha, monkeypatch
):
    """The promise this whole file rests on, tested at the one place it leaked.

    The docstring above says a workspace that cannot connect a channel cannot
    resolve credentials by any route. There was one exception, and it is the
    worst possible one: on an install that serves a single company and has an
    environment access token configured -- exactly the shape of a fresh server
    whose *first* company is a self-service demonstration --
    ``channels.credentials.resolve`` fell back to that platform-wide token when
    the company had no connected account of its own.

    A demo has no connected account, by construction. So the sole-company demo
    could send through the operator's own Meta page: a spam relay reaching real
    strangers, on the operator's name, without ever passing the channel gate.

    The resolver must refuse a demonstration the environment token, and
    ``is_demo`` fails closed so an unreadable flag refuses too.
    """
    import pytest as _pytest

    from channels.credentials import MissingChannelCredentials, resolve
    from backend.services.channel_account_service import channel_account_service
    from config.settings import config

    # Precondition: this demo has no connected account, so only the fallback
    # could hand it a token. If this ever seeds one, the test is testing nothing.
    assert not (
        channel_account_service.credentials_for(
            company_id=alpha["id"], channel="messenger"
        )
        or {}
    ).get("access_token"), "the demo already has a connected account; setup is wrong"

    # A fresh single-company install with the environment token configured.
    monkeypatch.setattr(wired, "default_company_id", lambda: alpha["id"])
    monkeypatch.setattr(config, "META_PAGE_ACCESS_TOKEN", "ENV-PAGE-TOKEN")

    _mark_demo(alpha["id"], True)

    with _pytest.raises(MissingChannelCredentials):
        resolve(alpha["id"], "messenger")


def test_a_real_single_company_still_gets_the_environment_token(
    wired, alpha, monkeypatch
):
    """The fix must not take the fallback away from the install it is for.

    A single real company that has not yet migrated to a connected account is
    exactly who the environment token is for. Once the same workspace is
    activated, the fallback returns -- because the refusal above is about the
    demonstration flag, not about being the only company.
    """
    from channels.credentials import resolve
    from config.settings import config

    monkeypatch.setattr(wired, "default_company_id", lambda: alpha["id"])
    monkeypatch.setattr(config, "META_PAGE_ACCESS_TOKEN", "ENV-PAGE-TOKEN")

    _mark_demo(alpha["id"], False)

    credentials = resolve(alpha["id"], "messenger")

    assert credentials["access_token"] == "ENV-PAGE-TOKEN"
