"""The schema is a contract, and this file checks the code keeps it.

D-010 records the defect this guards against. Creating a role raised
`IntegrityError` on every attempt for as long as the feature existed, because
the INSERT omitted `created_at` — a `NOT NULL` column with no default. Nobody
noticed, because a broad `except Exception` around it answered "A role with this
code already exists", a plausible sentence that sent every investigation in the
wrong direction.

The rule is not "be careful when writing an INSERT". It is that the schema
declares which columns must be supplied, so a machine can check every INSERT in
the codebase against it — and does, here, on every test run. A new INSERT that
forgets a required column fails this file rather than a customer's request.

The same reasoning applies to `INSERT OR IGNORE`, which suppresses a `NOT NULL`
violation exactly as it suppresses a duplicate. It once discarded every
permission assigned to a role, silently. Every use of it is listed below with
the constraint it is deliberately ignoring, so a new one has to be justified
rather than copied.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent

SOURCE_ROOTS = ("backend", "core", "channels", "database", "gateway", "tools")

INSERT_PATTERN = re.compile(
    r"INSERT(?:\s+OR\s+(\w+))?\s+INTO\s+(\w+)\s*\(([^)]*)\)", re.I | re.S
)

TABLE_PATTERN = re.compile(
    r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n    \)", re.S
)

# A column declared `NOT NULL` with no `DEFAULT` must be supplied by every
# INSERT. One with a default must not be, and one that is nullable need not be.
REQUIRED_PATTERN = re.compile(r"(\w+)\s+(?:TEXT|INTEGER|REAL|BLOB)\s+NOT NULL\s*$")


# INSERTs whose column list is built at runtime, so the literal SQL cannot be
# read for it. Each was checked by hand; the note says what makes it safe. A new
# dynamic INSERT is not skipped — it fails until it is reviewed and listed here,
# which is the point: the exception is visible rather than silent.
REVIEWED_DYNAMIC_INSERTS: dict[tuple[str, str], str] = {
    (
        "backend/services/catalogue_service.py",
        "products",
    ): (
        "`created_at` and `updated_at` are literals in the SQL; `name` is "
        "refused above the INSERT when absent."
    ),
    (
        "backend/services/platform_service.py",
        "plans",
    ): (
        "`code` and `created_at` are prepended to the column list; `name` is "
        "refused by `_clean_plan_values` when absent."
    ),
}


# Every `INSERT OR IGNORE` in the codebase, with the constraint it is there to
# ignore. The point of listing them is that the reader of a new one has to say
# which conflict they mean — `OR IGNORE` swallows a NOT NULL violation just as
# happily as a duplicate key.
REVIEWED_OR_IGNORE: dict[tuple[str, str], str] = {
    ("database/manager.py", "plans"): "Re-seeding the shipped plans at boot.",
    (
        "database/manager.py",
        "company_settings",
    ): "Re-seeding a company's default settings sections.",
    (
        "backend/services/team_chat_service.py",
        "team_channel_members",
    ): "Joining a channel somebody is already in.",
    (
        "tools/capture_demo_fixtures.py",
        "roles",
    ): "Seeding a throwaway company's default roles, which provision may have created.",
    (
        "tools/capture_demo_fixtures.py",
        "role_permissions",
    ): "Granting a permission the seeded role already holds.",
    (
        "backend/services/platform_service.py",
        "role_permissions",
    ): "Granting a permission a role already holds.",
    (
        "backend/services/platform_service.py",
        "company_platform_config",
    ): "Creating the config row when one already exists.",
    (
        "backend/api/routes/roles.py",
        "role_permissions",
    ): "Granting a permission a role already holds.",
    (
        "tools/manage_platform.py",
        "role_permissions",
    ): "Granting a permission a role already holds.",
}


def _required_columns() -> dict[str, set[str]]:
    required: dict[str, set[str]] = {}

    for schema in ("database/schema_control.py", "database/schema_tenant.py"):
        source = (ROOT / schema).read_text()

        for match in TABLE_PATTERN.finditer(source):
            table, body = match.group(1), match.group(2)
            columns = set()

            for line in body.splitlines():
                line = line.strip().rstrip(",")
                found = REQUIRED_PATTERN.match(line)

                if found and found.group(1) != "id":
                    columns.add(found.group(1))

            if columns:
                required[table] = columns

    return required


def _source_files() -> list[Path]:
    files: list[Path] = []

    for root in SOURCE_ROOTS:
        for directory, _, names in os.walk(ROOT / root):
            if "__pycache__" in directory:
                continue

            files.extend(
                Path(directory) / name for name in names if name.endswith(".py")
            )

    files.append(ROOT / "main.py")

    return sorted(files)


def _inserts():
    """Every INSERT in the codebase, as (relative path, line, or-clause, table, columns)."""
    for path in _source_files():
        source = path.read_text()
        relative = str(path.relative_to(ROOT))

        for match in INSERT_PATTERN.finditer(source):
            columns = {
                column.strip()
                for column in match.group(3).replace("\n", " ").split(",")
                if column.strip()
            }

            yield (
                relative,
                source[: match.start()].count("\n") + 1,
                (match.group(1) or "").upper(),
                match.group(2),
                columns,
            )


def test_the_schema_declares_required_columns():
    """A sanity check on the parser itself.

    Without it, a change to how the schema is written could make every test
    below pass by finding no tables at all.
    """
    required = _required_columns()

    assert len(required) > 30, "the schema parser found almost no tables"
    assert "created_at" in required["roles"], (
        "the column whose omission caused D-010 is no longer seen as required"
    )


def test_every_insert_supplies_every_required_column():
    """The D-010 defect, checked by machine rather than by care.

    A `NOT NULL` column with no default that an INSERT omits is a statement
    that fails every single time it runs. That it survived in `roles.py` for
    the whole life of the feature is the reason this test exists.
    """
    required = _required_columns()
    problems = []

    for path, line, _or_clause, table, columns in _inserts():
        if table not in required:
            continue

        # A dynamic column list: the literal SQL does not name every column, so
        # this cannot be read statically. Reviewed by hand and listed above.
        if (path, table) in REVIEWED_DYNAMIC_INSERTS:
            continue

        missing = required[table] - columns

        if missing:
            problems.append(f"{path}:{line} INTO {table} omits {sorted(missing)}")

    assert not problems, "INSERT(s) that would fail on every execution:\n" + "\n".join(
        problems
    )


def test_every_or_ignore_names_the_conflict_it_ignores():
    """`INSERT OR IGNORE` suppresses a NOT NULL violation exactly as it
    suppresses a duplicate. It silently discarded every permission assigned to
    a role once already.

    A new one has to be added to the list above with the constraint it means,
    which is a small enough cost to pay for never having to wonder again.
    """
    unreviewed = [
        f"{path}:{line} INTO {table}"
        for path, line, or_clause, table, _columns in _inserts()
        if or_clause == "IGNORE" and (path, table) not in REVIEWED_OR_IGNORE
    ]

    assert not unreviewed, (
        "INSERT OR IGNORE without a recorded reason:\n"
        + "\n".join(unreviewed)
        + "\n\nAdd it to REVIEWED_OR_IGNORE naming the constraint it ignores, "
        "or use a plain INSERT."
    )


def test_the_reviewed_lists_have_no_stale_entries():
    """An entry for an INSERT that no longer exists is a note nobody will
    remove, and a list nobody trusts is a list nobody reads."""
    live_dynamic = {(path, table) for path, _, _, table, _ in _inserts()}
    live_ignore = {
        (path, table)
        for path, _, or_clause, table, _ in _inserts()
        if or_clause == "IGNORE"
    }

    assert not (set(REVIEWED_DYNAMIC_INSERTS) - live_dynamic), (
        f"stale dynamic-INSERT entries: "
        f"{sorted(set(REVIEWED_DYNAMIC_INSERTS) - live_dynamic)}"
    )
    assert not (set(REVIEWED_OR_IGNORE) - live_ignore), (
        f"stale OR IGNORE entries: {sorted(set(REVIEWED_OR_IGNORE) - live_ignore)}"
    )


def test_no_bare_except_anywhere():
    """`except:` catches `KeyboardInterrupt` and `SystemExit` as well, so a
    process holding one cannot be stopped."""
    offenders = []

    for path in _source_files():
        tree = ast.parse(path.read_text())

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert not offenders, "bare `except:` at:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "table",
    [
        "usage_records",
        "company_plan_overrides",
        "password_reset_tokens",
        "company_work_index",
    ],
)
def test_tables_added_later_are_created_on_a_fresh_database(table, platform):
    """`CREATE TABLE IF NOT EXISTS` never adds a table to a database that
    already exists — but it must create one on a database that does not.

    A table added to the tuple and not to a fresh install is a feature that
    works in development and fails on the first deploy.
    """
    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()

    assert row, f"{table} is missing from a freshly provisioned control database"
