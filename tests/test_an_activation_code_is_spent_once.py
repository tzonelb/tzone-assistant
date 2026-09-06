"""The code that turns a demonstration into a business, and what it must refuse.

An activation code is the only thing standing between a self-service sign-up
and a workspace that can connect real channels, so the interesting cases are
all refusals: a code used twice, a code that expired, a code nobody minted, and
a code raced by two requests arriving together.

The last one is why redeeming is a single `UPDATE ... WHERE used_at IS NULL`
rather than a read followed by a write. A read-then-write leaves a window
exactly as wide as the database call between them, and "one trial code
activated four workspaces" is a defect found by an accountant months later
rather than by anyone watching.
"""

from __future__ import annotations

import sys

import pytest

# Before any fixture patches `database.manager.database_manager` -- a module
# first imported inside that window binds the test manager permanently. See
# tests/test_a_demonstration_cannot_reach_a_real_customer.py for the full
# account.
import backend.services.activation_service  # noqa: E402,F401
import backend.services.demo_gate  # noqa: E402,F401


@pytest.fixture()
def wired(platform, monkeypatch):
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


def _demo(company_id: int) -> None:
    from database.manager import database_manager
    from backend.services.demo_gate import demo_gate

    with database_manager.control() as conn:
        conn.execute(
            "UPDATE companies SET is_demo = 1 WHERE id = ?", (company_id,)
        )
        conn.commit()

    demo_gate.invalidate(company_id)


# ------------------------------------------------------------------- minting


def test_the_code_is_readable_once_and_stored_only_as_a_hash(wired):
    from database.manager import database_manager
    from backend.services.activation_service import activation_service

    minted = activation_service.mint(note="Trade show")

    assert minted["code"].startswith("TZA-")

    with database_manager.control() as conn:
        rows = conn.execute("SELECT * FROM activation_codes").fetchall()

    assert len(rows) == 1

    stored = dict(rows[0])

    # The code itself must appear in no column. Checked across the whole row
    # rather than against `code_hash` alone, so a later column that helpfully
    # keeps a readable copy is reported.
    for column, value in stored.items():
        assert minted["code"] not in str(value), (
            f"the readable code was stored in activation_codes.{column}"
        )

    assert len(stored["code_hash"]) == 64


def test_two_codes_are_not_the_same_code(wired):
    from backend.services.activation_service import activation_service

    codes = {activation_service.mint()["code"] for _ in range(25)}

    assert len(codes) == 25


# ----------------------------------------------------------------- redeeming


def test_redeeming_turns_the_workspace_into_a_real_one(wired, alpha):
    from backend.services.activation_service import activation_service
    from backend.services.demo_gate import demo_gate

    _demo(alpha["id"])
    assert demo_gate.is_demo(alpha["id"]) is True

    minted = activation_service.mint()
    result = activation_service.redeem(company_id=alpha["id"], code=minted["code"])

    assert result["company_id"] == alpha["id"]
    assert result["activated_at"]

    # The gate has to answer differently on the very next call, not in thirty
    # seconds: the owner who just typed the code is watching the screen.
    assert demo_gate.is_demo(alpha["id"]) is False


@pytest.mark.parametrize(
    "typed",
    [
        "  {code}  ",
        "{code}".lower(),
        "{code}",
    ],
    ids=["padded", "lowercase", "exact"],
)
def test_a_code_is_the_same_code_however_it_was_typed(wired, alpha, typed):
    """Pasted with spaces, or typed in lower case off a phone call."""
    from backend.services.activation_service import activation_service

    _demo(alpha["id"])
    minted = activation_service.mint()

    activation_service.redeem(
        company_id=alpha["id"], code=typed.format(code=minted["code"])
    )


def test_a_code_cannot_be_spent_twice(wired, alpha, beta):
    from backend.services.activation_service import (
        ActivationError,
        activation_service,
    )

    _demo(alpha["id"])
    _demo(beta["id"])

    minted = activation_service.mint()
    activation_service.redeem(company_id=alpha["id"], code=minted["code"])

    with pytest.raises(ActivationError):
        activation_service.redeem(company_id=beta["id"], code=minted["code"])

    from backend.services.demo_gate import demo_gate

    assert demo_gate.is_demo(beta["id"]) is True


def test_the_claim_is_one_statement_and_not_a_read_then_a_write():
    """Two requests handed the same unused code: only one may claim it.

    This is asserted against the source rather than by racing two redemptions,
    and the reason is worth stating because the first version of this test did
    race them and proved nothing. Two sequential calls cannot produce the
    interleaving: the first commits before the second reads, so a read-then-
    write implementation passes a sequential race exactly as a correct one
    does. Verified by writing that implementation -- SELECT the unused row,
    decide, then UPDATE it -- and watching all twelve tests here still pass.

    Threads would not settle it either. SQLite serialises writers, so a
    threaded version tests the driver's locking and lands wherever the timing
    falls -- which is how a test starts failing in CI and passing on a laptop.

    What is actually required is that the check and the claim are the same
    statement, so the database does the deciding. That is a property of the
    code, and it is checked as one.
    """
    import ast
    import inspect
    import textwrap

    from backend.services import activation_service as module

    # dedent, not cleandoc: a method's source is indented under its class, and
    # cleandoc leaves the first line flush while the rest keeps its indent,
    # which does not parse.
    source = textwrap.dedent(inspect.getsource(module.ActivationService.redeem))
    tree = ast.parse(source)

    statements = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]

    touching_codes = [
        " ".join(sql.split())
        for sql in statements
        if "activation_codes" in sql
    ]

    claiming = [
        sql
        for sql in touching_codes
        if sql.upper().startswith("UPDATE")
        and "USED_AT = ?" in sql.upper()
    ]

    assert len(claiming) == 1, (
        "Expected exactly one statement to claim a code, found "
        f"{len(claiming)}: {claiming}"
    )

    claim = claiming[0].upper()

    assert "USED_AT IS NULL" in claim, (
        "The claiming UPDATE does not carry `used_at IS NULL`, so it is not "
        "the thing deciding whether the code was already spent -- something "
        "read that first, and between the read and this write a second request "
        "carrying the same code reads it as unused too."
    )
    assert "EXPIRES_AT IS NULL OR EXPIRES_AT >" in claim, (
        "Expiry is decided outside the claiming statement, which is the same "
        "window in a different disguise."
    )

    # And nothing may look the code up beforehand in a way that invites the
    # decision to move back out of the UPDATE.
    reads = [sql for sql in touching_codes if sql.upper().startswith("SELECT")]

    for sql in reads:
        assert "USED_AT IS NULL" not in sql.upper(), (
            "A SELECT is filtering on `used_at IS NULL`, which means the "
            "decision is being taken before the write again: " + sql
        )


def test_an_expired_code_is_refused(wired, alpha):
    from backend.services.activation_service import (
        ActivationError,
        activation_service,
    )

    _demo(alpha["id"])
    minted = activation_service.mint(expires_at="2020-01-01T00:00:00+00:00")

    with pytest.raises(ActivationError):
        activation_service.redeem(company_id=alpha["id"], code=minted["code"])


def test_a_code_nobody_minted_is_refused(wired, alpha):
    from backend.services.activation_service import (
        ActivationError,
        activation_service,
    )

    _demo(alpha["id"])

    with pytest.raises(ActivationError):
        activation_service.redeem(
            company_id=alpha["id"], code="TZA-AAAA-BBBB-CCCC-DDDD"
        )


def test_every_refusal_reads_the_same(wired, alpha):
    """Telling a guesser which guess was once real is a hint they can use."""
    from backend.services.activation_service import (
        ActivationError,
        activation_service,
    )

    _demo(alpha["id"])

    spent = activation_service.mint()
    activation_service.redeem(company_id=alpha["id"], code=spent["code"])
    _demo(alpha["id"])

    expired = activation_service.mint(expires_at="2020-01-01T00:00:00+00:00")

    messages = set()

    for code in (spent["code"], expired["code"], "TZA-ZZZZ-ZZZZ-ZZZZ-ZZZZ"):
        try:
            activation_service.redeem(company_id=alpha["id"], code=code)
        except ActivationError as refusal:
            messages.add(str(refusal))

    assert len(messages) == 1, messages


def test_redeeming_on_an_already_live_workspace_does_not_burn_the_code(
    wired, alpha, beta
):
    """An owner who pastes a code into the wrong workspace must not lose it."""
    from backend.services.activation_service import (
        ActivationError,
        activation_service,
    )

    _demo(beta["id"])
    minted = activation_service.mint()

    # alpha is not a demonstration.
    with pytest.raises(ActivationError):
        activation_service.redeem(company_id=alpha["id"], code=minted["code"])

    # The code still works where it was meant to go.
    activation_service.redeem(company_id=beta["id"], code=minted["code"])


# --------------------------------------------------------- the issued register


def test_the_register_lists_codes_without_ever_reprinting_one(wired, alpha):
    """The console's list is a register, not a second copy of the codes.

    The plaintext leaves the process once, at minting. `list_codes` must not
    undo that by carrying a readable code back -- there is nothing to carry,
    only a hash was kept, and this proves the projection never reaches for it.
    """
    from backend.services.activation_service import activation_service

    a = activation_service.mint(note="one")
    b = activation_service.mint(note="two")

    listed = activation_service.list_codes()

    assert {row["note"] for row in listed} >= {"one", "two"}

    blob = repr(listed)
    for minted in (a, b):
        assert minted["code"] not in blob, "list_codes carried a readable code"
        # Nor the hash, which is a usable secret too: redeem hashes the input
        # and compares, so a leaked hash is a code that skips the hashing step.
        assert "code_hash" not in blob


def test_a_codes_status_follows_its_life(wired, alpha):
    """Newly minted reads `available`; once redeemed it reads `used`.

    Derived, not stored, so a code that quietly lapsed reads `expired` without
    a sweep having had to touch it first.
    """
    from backend.services.activation_service import activation_service

    minted = activation_service.mint()

    before = activation_service.list_codes()[0]
    assert before["status"] == "available"
    assert before["used_at"] is None

    _demo(alpha["id"])
    activation_service.redeem(company_id=alpha["id"], code=minted["code"])

    after = activation_service.list_codes()[0]
    assert after["status"] == "used"
    assert after["used_by_company_id"] == alpha["id"]


def test_an_expired_code_reads_expired_without_a_sweep(wired):
    from backend.services.activation_service import activation_service

    activation_service.mint(expires_at="2000-01-01T00:00:00+00:00")

    assert activation_service.list_codes()[0]["status"] == "expired"
