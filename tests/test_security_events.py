"""The events the platform declared and never raised.

`activity_service.Action` names 43 things worth recording, and the company
owner reads them through one unified log built for exactly that. An audit
counted how many of those names anything ever writes: twenty. The log looked
comprehensive and covered less than half the platform.

Two of the missing ones matter more than the rest.

**A rejected workspace code.** `authenticate` checks the code last — after the
email is matched, the account is confirmed active, the company is resolved, and
the password is verified. Reaching that branch means somebody is holding a
working password for one of this company's employees and is being stopped by
the workspace code alone. That is either an employee who forgot one of their
four credentials, or a compromised password one secret away from an open door,
and only the owner can tell those apart. It went to a log file on the server
and nowhere else.

**A refused webhook.** Forging a delivery is the one attack on this platform
that needs no account at all. The signature check was correct and fail-closed
from the start; what was missing was any record an operator could read.

The rest of this file covers the other events wired in the same pass, and ends
with the audit itself — a check that no future `Action` can be declared and
left unraised, which is the defect that produced all of this.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent

EMPLOYEE_PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.roles  # noqa: F401
    import backend.services.activity_service  # noqa: F401
    import backend.services.auth_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    # Without this the test writes to the real database and passes for the
    # wrong reason. It has happened twice in this suite.
    assert "backend.services.auth_service" in rebound
    assert "backend.services.activity_service" in rebound

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, roles

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(roles.router)

    return TestClient(app)


@pytest.fixture()
def employee(service, alpha):
    user_id = service.create_user(
        "employee@alpha.example.com", EMPLOYEE_PASSWORD, "Employee"
    )
    service.assign_user_to_company(user_id, alpha["id"], "agent")

    return user_id


def _entries(company_id, action=None):
    from backend.services.activity_service import activity_service

    result = activity_service.list_entries(company_id=company_id, limit=200)
    items = result["items"] if isinstance(result, dict) else result

    if action:
        items = [item for item in items if item["action"] == action]

    return items


# ------------------------------------------------- the rejected workspace code


def test_a_rejected_workspace_code_reaches_the_owners_log(
    client, alpha, employee
):
    from backend.services.activity_service import Action

    refused = client.post(
        "/api/auth/login",
        json={
            "workspace_code": "not-the-right-code",
            "company": alpha["name"],
            "email": "employee@alpha.example.com",
            "password": EMPLOYEE_PASSWORD,  # correct
        },
    )

    assert refused.status_code == 401

    entries = _entries(alpha["id"], Action.WORKSPACE_CODE_REJECTED)

    assert len(entries) == 1, (
        "A correct password stopped only by the workspace code left no trace "
        "in the owner's log"
    )
    assert entries[0]["severity"] == "warning"
    assert entries[0]["actor_user_id"] == employee


def test_a_wrong_password_does_not_raise_it(client, alpha, employee):
    """The event means something specific: the password was right. Raising it
    for an ordinary failed sign-in would make it noise, and noise is what an
    owner stops reading."""
    from backend.services.activity_service import Action

    client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "employee@alpha.example.com",
            "password": "WrongPassword12345",
        },
    )

    assert _entries(alpha["id"], Action.WORKSPACE_CODE_REJECTED) == []


def test_a_correct_sign_in_does_not_raise_it(client, alpha, employee):
    from backend.services.activity_service import Action

    ok = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "employee@alpha.example.com",
            "password": EMPLOYEE_PASSWORD,
        },
    )

    assert ok.status_code == 200, ok.text
    assert _entries(alpha["id"], Action.WORKSPACE_CODE_REJECTED) == []


def test_the_refusal_still_says_nothing_to_the_caller(client, alpha, employee):
    """Recording the reason must not start returning it.

    Withholding the reason from an attacker and withholding it from the owner
    are different things, and only the first was ever intended. This checks the
    first is still true.
    """
    wrong_code = client.post(
        "/api/auth/login",
        json={
            "workspace_code": "not-the-right-code",
            "company": alpha["name"],
            "email": "employee@alpha.example.com",
            "password": EMPLOYEE_PASSWORD,
        },
    )
    wrong_password = client.post(
        "/api/auth/login",
        json={
            "workspace_code": alpha["workspace_code"],
            "company": alpha["name"],
            "email": "employee@alpha.example.com",
            "password": "WrongPassword12345",
        },
    )

    assert wrong_code.status_code == wrong_password.status_code
    assert wrong_code.json() == wrong_password.json()


def test_it_reaches_only_the_company_it_happened_to(client, alpha, beta, employee):
    from backend.services.activity_service import Action

    client.post(
        "/api/auth/login",
        json={
            "workspace_code": "not-the-right-code",
            "company": alpha["name"],
            "email": "employee@alpha.example.com",
            "password": EMPLOYEE_PASSWORD,
        },
    )

    assert _entries(beta["id"], Action.WORKSPACE_CODE_REJECTED) == []


# --------------------------------------------------------- the refused webhook


def test_a_refused_webhook_is_recorded(monkeypatch):
    from backend.services.activity_service import Action
    import channels.webhook_security as security

    recorded: list[dict] = []

    class _Recorder:
        @staticmethod
        def record_unattributed(**fields):
            recorded.append(fields)

    monkeypatch.setattr(security, "_rejections_seen", {})
    monkeypatch.setitem(
        sys.modules,
        "backend.services.activity_service",
        type(
            "module",
            (),
            {"Action": Action, "activity_service": _Recorder()},
        ),
    )

    security.record_signature_rejection(
        source="meta", ip_address="203.0.113.9", reason="bad signature"
    )

    assert len(recorded) == 1
    assert recorded[0]["action"] == Action.WEBHOOK_SIGNATURE_REJECTED
    assert recorded[0]["ip_address"] == "203.0.113.9"


def test_a_flood_of_refused_webhooks_does_not_flood_the_log(monkeypatch):
    """Forging a delivery costs an attacker nothing. A write per attempt would
    make the audit trail the payload."""
    from backend.services.activity_service import Action
    import channels.webhook_security as security

    recorded: list[dict] = []

    class _Recorder:
        @staticmethod
        def record_unattributed(**fields):
            recorded.append(fields)

    monkeypatch.setattr(security, "_rejections_seen", {})
    monkeypatch.setitem(
        sys.modules,
        "backend.services.activity_service",
        type("module", (), {"Action": Action, "activity_service": _Recorder()}),
    )

    for _ in range(50):
        security.record_signature_rejection(
            source="meta", ip_address="203.0.113.9", reason="bad signature"
        )

    assert len(recorded) == 1, f"{len(recorded)} entries for one source in one minute"


def test_a_different_source_is_recorded_separately(monkeypatch):
    """Throttling per source, not globally — otherwise one noisy attacker hides
    every other one."""
    from backend.services.activity_service import Action
    import channels.webhook_security as security

    recorded: list[dict] = []

    class _Recorder:
        @staticmethod
        def record_unattributed(**fields):
            recorded.append(fields)

    monkeypatch.setattr(security, "_rejections_seen", {})
    monkeypatch.setitem(
        sys.modules,
        "backend.services.activity_service",
        type("module", (), {"Action": Action, "activity_service": _Recorder()}),
    )

    security.record_signature_rejection(
        source="meta", ip_address="203.0.113.9", reason="bad"
    )
    security.record_signature_rejection(
        source="whatsapp", ip_address="203.0.113.9", reason="bad"
    )
    security.record_signature_rejection(
        source="meta", ip_address="198.51.100.4", reason="bad"
    )

    assert len(recorded) == 3


def test_recording_never_breaks_the_refusal(monkeypatch):
    """The request is already being refused. A log entry must not be able to
    turn a correct 403 into a 500."""
    import channels.webhook_security as security

    class _Broken:
        @staticmethod
        def record_unattributed(**fields):
            raise RuntimeError("the control database is down")

    monkeypatch.setattr(security, "_rejections_seen", {})
    monkeypatch.setitem(
        sys.modules,
        "backend.services.activity_service",
        type("module", (), {"Action": object(), "activity_service": _Broken()}),
    )

    security.record_signature_rejection(
        source="meta", ip_address=None, reason="bad"
    )


# ---------------------------------------------------------------- the audit


def _declared_actions() -> list[str]:
    tree = ast.parse((ROOT / "backend/services/activity_service.py").read_text())

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Action":
            return [
                item.targets[0].id
                for item in node.body
                if isinstance(item, ast.Assign)
                and isinstance(item.targets[0], ast.Name)
            ]

    raise AssertionError("class Action is gone")


def test_the_action_names_can_be_read():
    """Without this, a rename would make the check below pass by finding no
    actions at all."""
    assert len(_declared_actions()) > 20


def test_every_declared_action_is_raised_somewhere():
    """The audit that produced this file, kept as a check.

    Twenty-three of forty-three action names were declared and written by
    nothing: settings changes, department changes, the assistant's own profile
    and reply policy, task and appointment edits, scheduled posts and their
    approval, comment replies, exports, plan refusals, permission denials,
    refused webhooks, rejected workspace codes.

    None of that is a crash or a wrong answer. It is worse in one specific way:
    the owner opens a log built to show them what happened in their company,
    reads it to the end, and concludes nothing else happened.

    Declaring a name is the cheap half. This is what makes the other half
    happen.
    """
    unraised = []

    for name in _declared_actions():
        found = subprocess.run(
            [
                "grep", "-rl", f"Action.{name}",
                "--include=*.py",
                "backend", "core", "channels", "gateway", "tools",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        writers = [
            path
            for path in found.stdout.split()
            # Where it is declared, not where it is raised.
            if "activity_service.py" not in path
        ]

        if not writers:
            unraised.append(name)

    assert not unraised, (
        "Action(s) declared and recorded by nothing:\n  "
        + "\n  ".join(unraised)
        + "\n\nAn event nobody raises makes the owner's log lie by omission. "
        "Raise it where it happens, or delete the name."
    )


# ------------------------------------------------------- the refused action


@pytest.fixture()
def denial_recorder(monkeypatch):
    """Capture what `require_permission` files, without a database."""
    import backend.services.activity_service as activity_module
    import backend.services.auth_service as auth_module

    written: list[dict] = []

    monkeypatch.setattr(auth_module, "_permission_denied_seen", {})
    monkeypatch.setattr(
        activity_module.activity_service,
        "record_for",
        lambda *args, **fields: written.append(fields),
    )

    class _Request:
        class url:
            path = "/api/anything"

        headers: dict[str, str] = {}
        client = None
        scope: dict = {"headers": []}

    return auth_module, written, _Request()


def test_a_refused_action_reaches_the_owners_log(denial_recorder):
    from backend.services.activity_service import Action

    auth_module, written, request = denial_recorder

    auth_module._record_permission_denied(
        current_user={"id": 7, "full_name": "Employee"},
        company_id=1,
        permission_code="users.view",
        request=request,
    )

    assert len(written) == 1
    assert written[0]["action"] == Action.PERMISSION_DENIED
    assert written[0]["after"]["permission"] == "users.view"


def test_hammering_a_forbidden_endpoint_does_not_flood_the_log(denial_recorder):
    """An authenticated employee can hammer a 403 as fast as the network
    allows. A write per attempt would turn the audit trail into the payload —
    unbounded writes into the company's own database, burying the entries an
    owner needs."""
    auth_module, written, request = denial_recorder

    for _ in range(200):
        auth_module._record_permission_denied(
            current_user={"id": 7, "full_name": "Employee"},
            company_id=1,
            permission_code="users.view",
            request=request,
        )

    assert len(written) == 1, f"{len(written)} entries for one employee in one minute"


def test_each_permission_is_throttled_on_its_own(denial_recorder):
    """Throttling the employee rather than the pair would hide the second thing
    they reached for, which is the part worth seeing."""
    auth_module, written, request = denial_recorder

    for code in ("users.view", "channels.view", "plan.view"):
        auth_module._record_permission_denied(
            current_user={"id": 7, "full_name": "Employee"},
            company_id=1,
            permission_code=code,
            request=request,
        )

    assert len(written) == 3


def test_each_employee_is_throttled_on_their_own(denial_recorder):
    auth_module, written, request = denial_recorder

    for user_id in (7, 8, 9):
        auth_module._record_permission_denied(
            current_user={"id": user_id, "full_name": "Employee"},
            company_id=1,
            permission_code="users.view",
            request=request,
        )

    assert len(written) == 3


def test_the_throttle_cannot_grow_without_bound(denial_recorder):
    """A long-running process would otherwise keep a key per employee per
    permission for ever."""
    auth_module, written, request = denial_recorder

    for user_id in range(11_000):
        auth_module._record_permission_denied(
            current_user={"id": user_id},
            company_id=1,
            permission_code="users.view",
            request=request,
        )

    assert len(auth_module._permission_denied_seen) <= 10_001


def test_recording_a_denial_never_breaks_the_refusal(monkeypatch, denial_recorder):
    """The 403 is already decided. Filing it must not turn a correct refusal
    into a server error."""
    import backend.services.activity_service as activity_module

    auth_module, _, request = denial_recorder

    def _broken(*args, **fields):
        raise RuntimeError("the tenant database is down")

    monkeypatch.setattr(activity_module.activity_service, "record_for", _broken)

    auth_module._record_permission_denied(
        current_user={"id": 7},
        company_id=1,
        permission_code="users.view",
        request=request,
    )


# --------------------------------------------------------- the refused write


def test_a_plan_refusal_reaches_the_owners_log(platform, monkeypatch, alpha):
    """The refusal already told one employee, on one screen, once. The owner is
    the person who can act on it — by raising the plan or by asking why the
    company needs a sixth of something."""
    import database.manager as manager_module

    import backend.services.activity_service  # noqa: F401
    import backend.services.plan_service  # noqa: F401

    original = manager_module.database_manager
    monkeypatch.setattr(manager_module, "database_manager", platform["manager"])

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", platform["manager"])
            rebound.append(module.__name__)

    assert "backend.services.plan_service" in rebound

    from backend.services.activity_service import Action
    from backend.services.plan_service import PlanLimitExceeded, plan_service

    monkeypatch.setattr(plan_service, "limit", lambda company_id, key: 5)

    with pytest.raises(PlanLimitExceeded):
        plan_service.check(alpha["id"], "max_users", used=5)

    entries = _entries(alpha["id"], Action.PLAN_LIMIT_HIT)

    assert len(entries) == 1
    assert entries[0]["severity"] == "warning"


def test_a_write_inside_the_allowance_records_nothing(
    platform, monkeypatch, alpha
):
    import database.manager as manager_module

    import backend.services.activity_service  # noqa: F401
    import backend.services.plan_service  # noqa: F401

    original = manager_module.database_manager
    monkeypatch.setattr(manager_module, "database_manager", platform["manager"])

    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", platform["manager"])

    from backend.services.activity_service import Action
    from backend.services.plan_service import plan_service

    monkeypatch.setattr(plan_service, "limit", lambda company_id, key: 5)

    plan_service.check(alpha["id"], "max_users", used=4)

    assert _entries(alpha["id"], Action.PLAN_LIMIT_HIT) == []


# ------------------------------------------------- reading versus changing


def test_editing_a_customer_is_recorded_as_well_as_opening_one():
    """Found by the audit that produced this file, and made visible by it.

    Opening a customer record was wired to the log before editing one was,
    which is the wrong way round — the owner could see who looked at a
    customer's phone number and not who changed it. The change did reach
    `customer_audit`, and no endpoint has ever read that table.
    """
    source = (ROOT / "backend/api/routes/customers.py").read_text()

    assert "Action.CUSTOMER_UPDATED" in source, (
        "Editing a customer is not recorded in the owner's log; only opening "
        "one is."
    )


def test_a_customers_contact_details_are_not_copied_into_the_log():
    """The values are what the customer gave this company. A log entry needs to
    say a phone number was changed, not what it was changed to — the customer
    record already holds that, and copying it makes a second store of personal
    data with its own retention."""
    import ast

    tree = ast.parse((ROOT / "backend/api/routes/customers.py").read_text())

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if getattr(node.func, "attr", None) != "record_for":
            continue

        for keyword in node.keywords:
            if keyword.arg not in ("before", "after"):
                continue

            logged = ast.dump(keyword.value)

            assert "values" not in logged or "sorted" in logged, (
                "A customer's own field values are being copied into the "
                "activity log"
            )
