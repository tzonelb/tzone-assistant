"""A table written by something and read by nothing is either a gap or a lie.

This is the sweep that found the branch defect, kept as a check. Every table in
both schemas is matched against the code that reads and writes it:

* no reader means somebody is paying to store something nobody can see;
* no writer means a feature is enforced and can never be armed — which is
  exactly what `super_admin_setting_overrides` was, and what `branches` was for
  the two screens that offered it.

Both are recorded rather than tolerated. An entry below has to say what reads
the same information instead, so the reason a table is write-only is a decision
somebody made and not a thing nobody noticed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SEARCH_ROOTS = ("backend", "core", "channels", "gateway", "tools", "database")


# Empty, and the intended state.
#
# It held `company_setting_audit` and `customer_audit` — the before-and-after of
# every settings change and customer edit, written since each shipped and read
# by nothing. Declaring them here was the honest first step and not a fix:
# rows kept accumulating where nobody could open them and nothing pruned them.
#
# They have a reader now (`/api/activity/settings/{section}/history` and
# `/api/activity/customers/{id}/history`) and a retention policy on the same
# clock as the log entry they belong to. An entry here should have to be argued
# for, not used to park a table.
WRITE_ONLY: dict[str, str] = {}


def _tables(schema: str) -> list[str]:
    source = (ROOT / "database" / schema).read_text()

    return sorted(set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", source)))


def _files_matching(pattern: str) -> set[str]:
    result = subprocess.run(
        ["grep", "-rEl", pattern, "--include=*.py", *SEARCH_ROOTS],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    return {
        path
        for path in result.stdout.split()
        # The schema declares every table; naming one there is not using it.
        if "schema_" not in path
    }


def _readers(table: str) -> set[str]:
    return _files_matching(rf"(FROM|JOIN)\s+{table}\b")


def _writers(table: str) -> set[str]:
    """Anything that changes the table at all."""
    return _files_matching(rf"(INSERT INTO|UPDATE|DELETE FROM)\s+{table}\b")


def _creators(table: str) -> set[str]:
    """Anything that can bring a row into existence.

    Separate from `_writers` because they are not the same question, and the
    first version of this file asked the wrong one. `branches` had an `UPDATE`
    and a `DELETE` long before it had an `INSERT`; a check counting those as
    writers reports a reachable table while every list built from it is empty
    for ever. Editing and deleting rows that cannot be created is not a
    feature.
    """
    return _files_matching(rf"INSERT (OR \w+ )?INTO\s+{table}\b")


def test_the_tables_can_be_read():
    """Without this, a change to how the schema is written would make every
    check below pass by finding no tables at all."""
    assert len(_tables("schema_control.py")) > 15
    assert len(_tables("schema_tenant.py")) > 25


def test_no_table_is_read_and_never_written():
    """The `branches` shape.

    A table the code reads and nothing can insert into is a feature that
    cannot be reached: four queries joined `branches`, two screens rendered the
    result, and every list was empty for ever because no endpoint could create
    a row.

    Insertion specifically, not "any write". An `UPDATE` on a table nothing
    inserts into edits rows that do not exist.
    """
    unreachable = []

    for schema in ("schema_control.py", "schema_tenant.py"):
        for table in _tables(schema):
            if _readers(table) and not _creators(table):
                unreachable.append(table)

    assert not unreachable, (
        "Table(s) something reads and nothing can write:\n  "
        + "\n  ".join(unreachable)
        + "\n\nWhatever reads it will always find nothing. Add the writer, or "
        "remove the reader and the table."
    )


def test_every_write_only_table_is_declared():
    """The `company_setting_audit` shape: rows accumulating where nobody can
    look at them."""
    undeclared = []

    for schema in ("schema_control.py", "schema_tenant.py"):
        for table in _tables(schema):
            if table in WRITE_ONLY:
                continue

            if _writers(table) and not _readers(table):
                undeclared.append(table)

    assert not undeclared, (
        "Table(s) written and read by nothing:\n  "
        + "\n  ".join(undeclared)
        + "\n\nGive it a reader, stop writing it, or add it to WRITE_ONLY "
        "naming what reads the same information instead."
    )


def test_the_write_only_list_has_no_stale_entries():
    """An entry for a table that has since gained a reader tells the next
    person the information is unreachable when it is not."""
    known = {
        table
        for schema in ("schema_control.py", "schema_tenant.py")
        for table in _tables(schema)
    }

    stale = sorted(set(WRITE_ONLY) - known)

    assert not stale, f"WRITE_ONLY names tables that no longer exist: {stale}"


def test_a_declared_table_that_gained_a_reader_is_reported():
    """The other direction of staleness, which the entry itself cannot catch:
    a table listed here that somebody has since wired a reader to."""
    now_readable = sorted(
        table for table in WRITE_ONLY if _readers(table)
    )

    assert not now_readable, (
        f"These are no longer write-only and should leave WRITE_ONLY: "
        f"{now_readable}"
    )
