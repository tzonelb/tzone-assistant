"""Every permission the Roles screen offers must decide something.

`require_permission` says it plainly in its own docstring:

    A role screen that lists permissions the API never checks is worse than no
    role screen at all, because it tells an administrator they have restricted
    someone when they have not.

That was true of `subscriptions.manage`. It was seeded into every company's
control database, described as "Change the plan and billing details", listed on
the Roles & Permissions screen next to the ones that work, and checked by no
endpoint anywhere. Nor could it be: a company cannot change its own plan by
design — plans and per-company overrides are set from the operator console. An
owner could grant it, revoke it, build a role around it, and nothing happened
either way.

It is the same defect as a setting that saves and decides nothing, one level
up and worse: a permission is a claim about what somebody is prevented from
doing, and an owner who believes a restriction is in place stops looking.

Retired from `DEFAULT_PERMISSIONS`, and the seed now deletes permissions that
are no longer in the catalogue so the retirement reaches companies that were
provisioned before it. This file is what stops the next one appearing.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SEARCH_ROOTS = ("backend", "core", "channels", "gateway", "tools")


# A permission that is deliberately not enforced by any endpoint, with the
# reason. Empty on purpose: there is no good reason for one, and an entry here
# should have to be argued for in review rather than added to make a test pass.
NOT_ENFORCED: dict[str, str] = {}


def _seeded() -> dict[str, str]:
    from database.schema_control import DEFAULT_PERMISSIONS

    return {code: name for code, name, _ in DEFAULT_PERMISSIONS}


def _enforced() -> set[str]:
    """Every permission code the API actually checks.

    Collected from the literal strings AND from the module constants some
    routers use — `team_chat.py` declares `PERMISSION = "team_chat.use"` and
    passes the constant, so a check that only read `require_permission("...")`
    would report a working permission as dead. A check that reports noise is
    one somebody eventually deletes.
    """
    found: set[str] = set()
    pattern = re.compile(r'"([a-z_]+\.[a-z_]+)"')

    for root in SEARCH_ROOTS:
        for path in (ROOT / root).rglob("*.py"):
            source = path.read_text()

            # Where the code is named directly.
            found |= set(
                re.findall(r'require_permission\(\s*"([^"]+)"', source)
            )
            found |= set(re.findall(r'permission_code\s*=\s*"([^"]+)"', source))

            # Where it is named through a constant, in a file that enforces at
            # all. Any dotted literal in such a file counts, which is loose —
            # but the failure it guards against is a permission nothing checks,
            # and being loose here can only hide a real problem in a file that
            # already enforces something, never invent one.
            if "require_permission" in source or "has_permission" in source:
                found |= set(pattern.findall(source))

    return found


def test_the_catalogue_can_be_read():
    """Without this, a rename would make every check below pass by finding no
    permissions at all."""
    seeded = _seeded()

    assert len(seeded) > 15
    assert "conversations.view" in seeded


def test_every_seeded_permission_is_enforced_somewhere():
    seeded = _seeded()
    enforced = _enforced()

    dead = sorted(
        code
        for code in seeded
        if code not in enforced and code not in NOT_ENFORCED
    )

    assert not dead, (
        "Permission(s) offered on the Roles screen that no endpoint checks:\n  "
        + "\n  ".join(f"{code} ({seeded[code]})" for code in dead)
        + "\n\nEnforce it, or retire it from DEFAULT_PERMISSIONS. Granting or "
        "revoking a permission that decides nothing tells an owner they have "
        "restricted somebody when they have not."
    )


def test_no_endpoint_requires_a_permission_that_is_not_seeded():
    """The mirror image, and the more dangerous direction.

    A permission code that no company's database holds can never be granted to
    anyone, so the endpoint guarding it is closed to every employee of every
    company — including the owner. A typo in a permission string is a silent
    outage on one screen.
    """
    seeded = set(_seeded())
    required: set[str] = set()

    for root in SEARCH_ROOTS:
        for path in (ROOT / root).rglob("*.py"):
            source = path.read_text()
            required |= set(
                re.findall(r'require_permission\(\s*"([^"]+)"', source)
            )

    unknown = sorted(required - seeded)

    assert not unknown, (
        "Endpoint(s) requiring a permission that is seeded nowhere, so nobody "
        f"can ever hold it: {unknown}"
    )


def test_the_exception_list_has_no_stale_entries():
    seeded = set(_seeded())
    stale = sorted(set(NOT_ENFORCED) - seeded)

    assert not stale, (
        f"NOT_ENFORCED names permissions that no longer exist: {stale}"
    )


def test_subscriptions_manage_guards_something():
    """This was `test_subscriptions_manage_is_gone`, and its own docstring said
    what would bring it back: "the permission has to guard the endpoint that
    makes it possible — not be seeded ahead of it."

    That endpoint now exists. `POST /api/activation/redeem` takes a workspace
    out of demonstration and puts it on a plan, which is precisely what this
    permission always claimed to cover, so it is seeded again -- and the test
    inverts rather than disappearing, because the condition it was protecting
    has not changed. A permission that decides nothing is still the defect;
    what changed is that this one decides something.

    It is granted to no default role. The owner holds everything in code, and
    whether a manager may spend the company's activation code is the owner's
    call on the Roles screen.
    """
    assert "subscriptions.manage" in _seeded()

    guarded = set()

    for root in SEARCH_ROOTS:
        for path in (ROOT / root).rglob("*.py"):
            guarded |= set(
                re.findall(
                    r'require_permission\(\s*"([^"]+)"', path.read_text()
                )
            )

    assert "subscriptions.manage" in guarded, (
        "subscriptions.manage is seeded again but no endpoint checks it, "
        "which is the state it was retired from."
    )


def test_a_retired_permission_is_removed_from_an_existing_database(tmp_path):
    """Retiring it from the catalogue is only half the job.

    The seed runs on every boot with `ON CONFLICT DO UPDATE`, so a permission
    dropped from the catalogue would simply stop being refreshed and keep its
    row for ever in every database provisioned before the change. Those are
    exactly the databases in production.
    """
    from database.manager import DatabaseManager, utc_now_iso

    manager = DatabaseManager(data_dir=tmp_path / "data")

    with manager.control() as conn:
        conn.execute(
            "INSERT INTO permissions (code, name, description, created_at)"
            " VALUES ('legacy.retired', 'Retired', 'Left over', ?)",
            (utc_now_iso(),),
        )
        conn.commit()

    # Re-open, which re-runs the seed the way a restart would.
    reopened = DatabaseManager(data_dir=tmp_path / "data")

    with reopened.control() as conn:
        row = conn.execute(
            "SELECT id FROM permissions WHERE code = 'legacy.retired'"
        ).fetchone()

    assert row is None, "A permission dropped from the catalogue survived a restart"


def test_the_permissions_that_are_enforced_survive_the_cleanup(tmp_path):
    """The other half. A delete that took the working permissions with it would
    lock every company out of everything."""
    from database.manager import DatabaseManager

    manager = DatabaseManager(data_dir=tmp_path / "data")
    DatabaseManager(data_dir=tmp_path / "data")

    with manager.control() as conn:
        codes = {
            row["code"]
            for row in conn.execute("SELECT code FROM permissions").fetchall()
        }

    assert codes == set(_seeded())
