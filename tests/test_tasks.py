"""Tests for the tasks module.

Tasks share the ``tickets`` table with the assistant's support escalations, so
every test here runs against two provisioned companies: a task list that leaks
between companies would hand one business its competitor's workload, its
deadlines and its customers' problem descriptions.

Time is the other thing worth testing. "Overdue" is decided by comparing stored
strings, which is only correct if every due date is written in one shape and if
a finished task is excluded — both of which are easy to break and silent when
broken.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def iso_in(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


@pytest.fixture()
def service(platform, monkeypatch):
    """Point the ticket service at the test platform's databases."""
    import sys

    import database.manager as manager_module

    # Imported before the sweep below: a module that has not been imported yet
    # holds no reference to rebind, and would later import the real singleton.
    import backend.services.ticket_service  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.api.routes.tickets  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    # Modules that did `from database.manager import database_manager` hold
    # their own reference and must be rebound too, or the test silently runs
    # against the process-wide singleton and proves nothing.
    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.ticket_service" in rebound
    assert "backend.services.auth_service" in rebound

    from backend.services.ticket_service import ticket_service

    return ticket_service


@pytest.fixture()
def employees(service, platform, alpha, beta):
    """One employee in each company, in the control database.

    Assignees are user ids from the shared control database; the tenant file
    cannot join to it. Creating a user on each side is what makes it possible to
    prove a name is never resolved across the boundary.
    """
    from database.manager import utc_now_iso

    created: dict[str, int] = {}

    with platform["manager"].control() as conn:
        now = utc_now_iso()

        for slug, company, email, full_name in (
            ("alpha", alpha, "rana@alpha.test", "Rana Alpha"),
            ("beta", beta, "omar@beta.test", "Omar Beta"),
        ):
            cursor = conn.execute(
                """
                INSERT INTO users (
                    email, full_name, status, is_super_admin, created_at, updated_at
                )
                VALUES (?, ?, 'active', 0, ?, ?)
                """,
                (email, full_name, now, now),
            )
            user_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO company_users (
                    company_id, user_id, status, created_at
                )
                VALUES (?, ?, 'active', ?)
                """,
                (company["id"], user_id, now),
            )
            created[slug] = user_id

        conn.commit()

    return created


def make_task(service, company, **values):
    payload = {"title": "Call the customer back"}
    payload.update(values)

    return service.create_task(
        company_id=company["id"],
        data=payload,
        created_by_user_id=values.pop("created_by_user_id", None),
    )


# ----------------------------------------------------------------------
# Tenant isolation
# ----------------------------------------------------------------------


def test_a_company_cannot_see_or_modify_another_companys_tasks(
    service, alpha, beta
):
    """Task ids are small sequential integers, so every company's task #1
    exists. Without the company id in the WHERE clause of every read and every
    write, Beta would list Alpha's workload and could edit, reassign, close or
    comment on it by guessing a number."""
    beta_task = make_task(service, beta, title="Beta delivery")
    collision = make_task(service, alpha, title="Alpha first")
    alpha_task = make_task(service, alpha, title="Alpha refund call")

    alpha_titles = sorted(
        item["title"] for item in service.list_tasks(company_id=alpha["id"])["items"]
    )
    beta_titles = [
        item["title"] for item in service.list_tasks(company_id=beta["id"])["items"]
    ]

    assert alpha_titles == ["Alpha first", "Alpha refund call"]
    assert beta_titles == ["Beta delivery"]

    # The two companies really do number their tasks from one, which is what
    # makes the lookups below worth checking.
    assert collision["id"] == beta_task["id"]

    # Reading one by id across the boundary.
    with pytest.raises(KeyError):
        service.get_task(company_id=beta["id"], task_id=alpha_task["id"])

    # Editing, reassigning and closing it across the boundary.
    with pytest.raises(KeyError):
        service.update_task(
            company_id=beta["id"],
            task_id=alpha_task["id"],
            values={"title": "Hijacked"},
        )

    with pytest.raises(KeyError):
        service.assign_task(
            company_id=beta["id"], task_id=alpha_task["id"], assigned_user_id=99
        )

    with pytest.raises(KeyError):
        service.change_status(
            company_id=beta["id"], task_id=alpha_task["id"], status="closed"
        )

    with pytest.raises(KeyError):
        service.add_comment(
            company_id=beta["id"],
            task_id=alpha_task["id"],
            author_user_id=None,
            body="Reading your notes",
        )

    unchanged = service.get_task(company_id=alpha["id"], task_id=alpha_task["id"])
    assert unchanged["title"] == "Alpha refund call"
    assert unchanged["status"] == "open"
    assert unchanged["assigned_user_id"] is None
    assert service.list_comments(company_id=alpha["id"], task_id=alpha_task["id"]) == []

    # And on the id the two companies share, Beta's write lands on Beta's own
    # row rather than reaching into Alpha's database.
    service.update_task(
        company_id=beta["id"],
        task_id=beta_task["id"],
        values={"title": "Beta renamed"},
    )

    assert (
        service.get_task(company_id=alpha["id"], task_id=collision["id"])["title"]
        == "Alpha first"
    )


def test_another_companys_comments_are_not_listed(service, alpha, beta):
    """Comments carry their own company id. If the thread were fetched by
    ticket id alone, two companies whose task ids collide would read each
    other's internal notes."""
    beta_task = make_task(service, beta, title="Beta task")
    make_task(service, alpha, title="Alpha filler")
    alpha_task = make_task(service, alpha, title="Alpha task")

    service.add_comment(
        company_id=alpha["id"],
        task_id=alpha_task["id"],
        author_user_id=None,
        body="Alpha internal note",
    )
    service.add_comment(
        company_id=beta["id"],
        task_id=beta_task["id"],
        author_user_id=None,
        body="Beta internal note",
    )

    alpha_bodies = [
        comment["body"]
        for comment in service.list_comments(
            company_id=alpha["id"], task_id=alpha_task["id"]
        )
    ]

    assert alpha_bodies == ["Alpha internal note"]

    with pytest.raises(KeyError):
        service.list_comments(company_id=beta["id"], task_id=alpha_task["id"])


def test_counts_are_scoped_to_one_company(service, alpha, beta):
    """The header tiles are the first thing anyone reads. A count that sums
    both companies' rows reports work that does not exist and makes the
    overdue number impossible to act on."""
    make_task(service, alpha, title="Alpha open")
    make_task(service, beta, title="Beta open")
    make_task(service, beta, title="Beta second", status="closed")

    assert service.task_counts(company_id=alpha["id"])["total"] == 1
    assert service.task_counts(company_id=beta["id"])["total"] == 2
    assert service.task_counts(company_id=alpha["id"])["closed"] == 0


# ----------------------------------------------------------------------
# Overdue
# ----------------------------------------------------------------------


def test_overdue_filter_returns_only_late_unfinished_tasks(service, alpha):
    """Overdue is the whole point of a deadline. Three separate mistakes are
    possible here — including tasks with no due date, including tasks due in
    the future, and including tasks that were finished late — and each one
    turns the overdue list into noise nobody reads."""
    late = make_task(service, alpha, title="Late", due_date=iso_in(days=-2))
    make_task(service, alpha, title="Future", due_date=iso_in(days=2))
    make_task(service, alpha, title="No deadline")
    make_task(
        service,
        alpha,
        title="Finished late",
        due_date=iso_in(days=-3),
        status="resolved",
    )

    overdue = service.list_tasks(company_id=alpha["id"], overdue=True)

    assert [item["title"] for item in overdue["items"]] == ["Late"]
    assert overdue["total"] == 1
    assert overdue["items"][0]["id"] == late["id"]
    assert overdue["items"][0]["is_overdue"] is True

    not_overdue = service.list_tasks(company_id=alpha["id"], overdue=False)

    assert sorted(item["title"] for item in not_overdue["items"]) == [
        "Finished late",
        "Future",
        "No deadline",
    ]

    assert service.task_counts(company_id=alpha["id"])["overdue"] == 1


def test_a_task_due_today_is_not_overdue_until_the_day_ends(service, alpha):
    """A bare `2026-04-20` sorts before `2026-04-20T09:00`, so storing the date
    as typed would mark a task due today as late from one minute past midnight.
    The date is normalized to the end of its day instead."""
    today = datetime.now(timezone.utc).date().isoformat()

    task = make_task(service, alpha, title="Due today", due_date=today)

    assert task["due_date"].startswith(f"{today}T23:59:59")
    assert task["is_overdue"] is False
    assert service.list_tasks(company_id=alpha["id"], overdue=True)["total"] == 0


def test_resolving_a_late_task_clears_it_from_overdue(service, alpha):
    """A finished task keeps its due date. If the overdue filter looked only at
    the date, every task ever completed late would stay in the overdue list
    forever and the count would only ever grow."""
    task = make_task(service, alpha, title="Late", due_date=iso_in(days=-1))

    assert service.get_task(company_id=alpha["id"], task_id=task["id"])["is_overdue"]

    resolved = service.change_status(
        company_id=alpha["id"], task_id=task["id"], status="resolved"
    )

    assert resolved["is_overdue"] is False
    assert service.task_counts(company_id=alpha["id"])["overdue"] == 0


def test_a_naive_due_date_is_stored_as_utc(service, alpha):
    """Due dates are compared as strings. One row written without a timezone
    offset sorts against `+00:00` rows by its bare digits, so it would compare
    as later than every timestamped row and never come up as overdue."""
    task = make_task(
        service, alpha, title="No offset", due_date="2020-01-02T03:04:05"
    )

    assert task["due_date"] == "2020-01-02T03:04:05+00:00"
    assert task["is_overdue"] is True


def test_an_unparseable_due_date_is_refused(service, alpha):
    """Accepting free text would put a value in the column that no comparison
    can order, quietly dropping the task out of every deadline filter."""
    with pytest.raises(ValueError):
        make_task(service, alpha, title="Broken", due_date="next tuesday")


# ----------------------------------------------------------------------
# Creating, editing and assigning
# ----------------------------------------------------------------------


def test_a_task_needs_a_title(service, alpha):
    """A ticket opened by the assistant is identified by its conversation, but a
    task with no title is a blank row in a list: nobody can tell what it is and
    nobody will ever close it."""
    with pytest.raises(ValueError):
        make_task(service, alpha, title="   ")


def test_created_task_keeps_every_field_it_was_given(service, alpha, employees):
    """Every one of these columns was added to the table for this screen. A
    create path that silently drops one produces a task with no deadline, no
    owner or no type, and the loss is only noticed later."""
    task = service.create_task(
        company_id=alpha["id"],
        data={
            "title": "Install the receiver",
            "problem": "Customer is home after 5pm.",
            "task_type": "maintenance",
            "priority": "high",
            "due_date": "2030-06-01",
            "assigned_user_id": employees["alpha"],
            "department": "field",
        },
        created_by_user_id=employees["alpha"],
    )

    assert task["title"] == "Install the receiver"
    assert task["problem"] == "Customer is home after 5pm."
    assert task["task_type"] == "maintenance"
    assert task["priority"] == "high"
    assert task["due_date"].startswith("2030-06-01T23:59:59")
    assert task["assigned_user_id"] == employees["alpha"]
    assert task["created_by_user_id"] == employees["alpha"]
    assert task["department"] == "field"
    assert task["status"] == "open"


def test_an_unknown_status_or_type_is_refused(service, alpha):
    """A status the filters do not know about makes a task invisible: it matches
    no status filter and is never counted in a tile, so it disappears from the
    screen while still sitting in the table."""
    task = make_task(service, alpha)

    with pytest.raises(ValueError):
        service.change_status(
            company_id=alpha["id"], task_id=task["id"], status="archived"
        )

    with pytest.raises(ValueError):
        service.update_task(
            company_id=alpha["id"],
            task_id=task["id"],
            values={"task_type": "whatever"},
        )

    with pytest.raises(ValueError):
        service.update_task(
            company_id=alpha["id"], task_id=task["id"], values={"priority": "asap"}
        )


def test_a_partial_edit_leaves_the_other_fields_alone(service, alpha, employees):
    """The form sends only what changed. An update that rebuilt the whole row
    would wipe the assignee or the deadline of anyone editing a single field."""
    task = service.create_task(
        company_id=alpha["id"],
        data={
            "title": "Original",
            "priority": "urgent",
            "due_date": "2030-06-01",
            "assigned_user_id": employees["alpha"],
        },
        created_by_user_id=employees["alpha"],
    )

    updated = service.update_task(
        company_id=alpha["id"],
        task_id=task["id"],
        values={"title": "Renamed"},
    )

    assert updated["title"] == "Renamed"
    assert updated["priority"] == "urgent"
    assert updated["assigned_user_id"] == employees["alpha"]
    assert updated["due_date"] == task["due_date"]


def test_clearing_the_deadline_and_the_assignee_is_possible(
    service, alpha, employees
):
    """An explicit null has to reach the column. If empty values were treated as
    "unchanged", a task could never be un-assigned or have a wrong deadline
    removed once one was set."""
    task = service.create_task(
        company_id=alpha["id"],
        data={
            "title": "Temporary",
            "due_date": "2030-06-01",
            "assigned_user_id": employees["alpha"],
        },
        created_by_user_id=None,
    )

    cleared = service.update_task(
        company_id=alpha["id"],
        task_id=task["id"],
        values={"due_date": None, "assigned_user_id": None},
    )

    assert cleared["due_date"] is None
    assert cleared["assigned_user_id"] is None
    assert cleared["is_overdue"] is False


def test_closing_stamps_a_time_and_reopening_clears_it(service, alpha):
    """`closed_at` is what "how long did this take" is measured from. If it were
    left behind on reopening, a task that is open again would still report as
    finished; if it were overwritten on every save, the original completion time
    would be lost."""
    task = make_task(service, alpha)

    closed = service.change_status(
        company_id=alpha["id"], task_id=task["id"], status="closed"
    )
    assert closed["closed_at"]

    edited = service.update_task(
        company_id=alpha["id"], task_id=task["id"], values={"title": "Still closed"}
    )
    assert edited["closed_at"] == closed["closed_at"]

    reopened = service.change_status(
        company_id=alpha["id"], task_id=task["id"], status="open"
    )
    assert reopened["closed_at"] is None


def test_assigning_replaces_the_previous_owner(service, alpha, employees):
    """Two owners on one task means neither does it. Assignment overwrites
    rather than accumulating, and the assignee filter has to follow it."""
    task = make_task(service, alpha)

    assigned = service.assign_task(
        company_id=alpha["id"],
        task_id=task["id"],
        assigned_user_id=employees["alpha"],
    )
    assert assigned["assigned_user_id"] == employees["alpha"]

    mine = service.list_tasks(
        company_id=alpha["id"], assigned_user_id=employees["alpha"]
    )
    assert [item["id"] for item in mine["items"]] == [task["id"]]

    cleared = service.assign_task(
        company_id=alpha["id"], task_id=task["id"], assigned_user_id=None
    )
    assert cleared["assigned_user_id"] is None

    assert (
        service.list_tasks(
            company_id=alpha["id"], assigned_user_id=employees["alpha"]
        )["total"]
        == 0
    )
    assert service.list_tasks(company_id=alpha["id"], unassigned=True)["total"] == 1


# ----------------------------------------------------------------------
# Filtering and searching
# ----------------------------------------------------------------------


def test_my_tasks_returns_only_what_is_assigned_to_me(service, alpha, employees):
    """"My tasks" is the view an employee works from all day. If it fell back to
    every task when the filter did not match, somebody would start work that
    belongs to a colleague."""
    mine = make_task(service, alpha, title="Mine")
    service.assign_task(
        company_id=alpha["id"],
        task_id=mine["id"],
        assigned_user_id=employees["alpha"],
    )
    make_task(service, alpha, title="Somebody else's")

    result = service.list_tasks(
        company_id=alpha["id"], assigned_user_id=employees["alpha"]
    )

    assert [item["title"] for item in result["items"]] == ["Mine"]
    assert result["total"] == 1


def test_filters_and_counts_agree_with_the_list(service, alpha):
    """The tile shows a count and the list shows rows. When the two are built
    from different WHERE clauses they disagree, and the screen looks broken to
    the person who clicks the tile."""
    make_task(service, alpha, title="One", status="open")
    make_task(service, alpha, title="Two", status="in_progress")
    make_task(service, alpha, title="Three", status="in_progress")

    counts = service.task_counts(company_id=alpha["id"])
    listed = service.list_tasks(company_id=alpha["id"], status="in_progress")

    assert counts["open"] == 1
    assert counts["in_progress"] == 2
    assert counts["resolved"] == 0
    assert counts["closed"] == 0
    assert listed["total"] == counts["in_progress"]


def test_type_and_search_filters_narrow_the_list(service, alpha):
    """The search box is how a task is found once there are more than a screenful.
    Searching only the title would miss everything written in the details, which
    is where the customer's name and the actual problem end up."""
    make_task(
        service,
        alpha,
        title="Replace the router",
        task_type="maintenance",
        problem="Serial number QX-771",
    )
    make_task(service, alpha, title="Chase the invoice", task_type="follow_up")

    by_type = service.list_tasks(company_id=alpha["id"], task_type="maintenance")
    assert [item["title"] for item in by_type["items"]] == ["Replace the router"]

    by_title = service.list_tasks(company_id=alpha["id"], search="invoice")
    assert [item["title"] for item in by_title["items"]] == ["Chase the invoice"]

    by_details = service.list_tasks(company_id=alpha["id"], search="QX-771")
    assert [item["title"] for item in by_details["items"]] == ["Replace the router"]


def test_the_list_is_paginated_without_losing_the_total(service, alpha):
    """A page of rows with a page-sized total would tell the screen there is
    only ever one page, hiding every task past the first twenty."""
    for index in range(5):
        make_task(service, alpha, title=f"Task {index}")

    page = service.list_tasks(company_id=alpha["id"], limit=2, offset=0)

    assert len(page["items"]) == 2
    assert page["total"] == 5


# ----------------------------------------------------------------------
# Comments
# ----------------------------------------------------------------------


def test_comments_are_returned_oldest_first(service, alpha, employees):
    """A thread read out of order is a different conversation. The reply has to
    follow the message it answers."""
    task = make_task(service, alpha)

    for body in ("First", "Second", "Third"):
        service.add_comment(
            company_id=alpha["id"],
            task_id=task["id"],
            author_user_id=employees["alpha"],
            body=body,
        )

    bodies = [
        comment["body"]
        for comment in service.list_comments(
            company_id=alpha["id"], task_id=task["id"]
        )
    ]

    assert bodies == ["First", "Second", "Third"]


def test_an_empty_comment_is_refused(service, alpha):
    """A blank row in a thread carries no information and pushes the real
    comments off the screen."""
    task = make_task(service, alpha)

    with pytest.raises(ValueError):
        service.add_comment(
            company_id=alpha["id"],
            task_id=task["id"],
            author_user_id=None,
            body="   ",
        )


def test_commenting_moves_the_task_to_the_top_of_recent_activity(service, alpha):
    """A comment is work on the task. Leaving `updated_at` behind would let a
    task that is being actively discussed look untouched for weeks."""
    task = make_task(service, alpha)

    service.add_comment(
        company_id=alpha["id"],
        task_id=task["id"],
        author_user_id=None,
        body="Customer called back",
    )

    refreshed = service.get_task(company_id=alpha["id"], task_id=task["id"])
    assert refreshed["updated_at"] > task["updated_at"]


# ----------------------------------------------------------------------
# Employee names, which live in the other database
# ----------------------------------------------------------------------


def test_employee_names_are_resolved_from_the_control_database(
    service, alpha, employees
):
    """A tenant database is a separate encrypted file and cannot join to
    `users`, so a task row carries a bare integer. Without this resolution step
    the screen shows "User #4" where a name belongs."""
    from backend.api.routes.tickets import with_display_names

    task = service.create_task(
        company_id=alpha["id"],
        data={"title": "Named", "assigned_user_id": employees["alpha"]},
        created_by_user_id=employees["alpha"],
    )
    comment = service.add_comment(
        company_id=alpha["id"],
        task_id=task["id"],
        author_user_id=employees["alpha"],
        body="On it",
    )

    named_task, named_comment = with_display_names(alpha["id"], [task, comment])

    assert named_task["assigned_user_name"] == "Rana Alpha"
    assert named_task["created_by_user_name"] == "Rana Alpha"
    assert named_comment["author_name"] == "Rana Alpha"


def test_a_name_is_never_resolved_across_companies(service, alpha, beta, employees):
    """The control database holds every company's staff in one table. Resolving
    an id without scoping to the company would print an outsider's real name
    inside another company's screen."""
    from backend.api.routes.tickets import with_display_names

    stolen = {
        "assigned_user_id": employees["beta"],
        "created_by_user_id": employees["beta"],
    }

    named = with_display_names(alpha["id"], [stolen])[0]

    assert named["assigned_user_name"] is None
    assert named["created_by_user_name"] is None


def test_an_assignee_from_another_company_is_refused(service, alpha, employees):
    """User ids are sequential across the platform, so a typed-in id can easily
    land on a stranger. Alpha must not be able to park its work on a Beta
    employee, whose own task list would then show a company they have no account
    with."""
    from fastapi import HTTPException

    from backend.api.routes.tickets import require_company_employee

    assert (
        require_company_employee(alpha["id"], employees["alpha"])
        == employees["alpha"]
    )

    with pytest.raises(HTTPException) as raised:
        require_company_employee(alpha["id"], employees["beta"])

    assert raised.value.status_code == 400


# ----------------------------------------------------------------------
# The escalation path that already used this table
# ----------------------------------------------------------------------


def test_an_assistant_ticket_still_creates_and_reads_back(service, alpha):
    """`core/engine.py` creates a ticket with no title and shows the returned id
    to the customer. Adding task columns must not change that contract or the
    assistant's escalation path breaks in production, not in this suite."""
    ticket_id = service.create(
        company_id=alpha["id"],
        data={
            "platform": "whatsapp",
            "user_id": "961700000",
            "problem": "No signal",
        },
    )

    assert isinstance(ticket_id, int)

    ticket = service.get(company_id=alpha["id"], ticket_id=ticket_id)

    assert ticket["problem"] == "No signal"
    assert ticket["task_type"] == "support"
    assert ticket["status"] == "open"
    assert ticket["title"] is None

    listed = service.list(company_id=alpha["id"], status="open")
    assert listed["total"] == 1


def test_every_endpoint_that_accepts_an_assignee_checks_it():
    """The generalisation, because the specific case was missed once.

    `require_company_employee` existed, its docstring explained exactly why it
    mattered, the module docstring claimed "every employee id a request names is
    checked against that company's own" — and `PATCH /api/tickets/{id}` passed
    `payload.assigned_user_id` straight through. Three sibling endpoints in the
    same file did check. Nothing noticed, because a guard being present in a
    file says nothing about it being reached on every path.

    An id belonging to no employee of this company does not leak a name —
    `user_display_names` is scoped, so it resolves to nothing. It leaves the
    ticket assigned to a phantom: `assigned_user_id` set, no assignee shown,
    and matching nobody's "assigned to me". Assigned, unnamed, and lost.

    So the rule is checked by walking the source rather than by remembering: a
    handler whose payload type carries `assigned_user_id` must call the guard.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "backend/api/routes/tickets.py"
    tree = ast.parse(source.read_text())

    # Which request models carry an assignee.
    models_with_assignee = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.target.id == "assigned_user_id"
            for item in node.body
        )
    }

    assert models_with_assignee, "No request model carries an assignee any more"

    unguarded = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        takes_assignee = any(
            isinstance(arg.annotation, ast.Name)
            and arg.annotation.id in models_with_assignee
            for arg in node.args.args
        )

        if not takes_assignee:
            continue

        guarded = any(
            isinstance(call.func, ast.Name)
            and call.func.id == "require_company_employee"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        )

        if not guarded:
            unguarded.append(node.name)

    assert not unguarded, (
        "Endpoint(s) accepting an assignee without checking it belongs to this "
        f"company: {unguarded}. Wrap the value in `require_company_employee`."
    )
