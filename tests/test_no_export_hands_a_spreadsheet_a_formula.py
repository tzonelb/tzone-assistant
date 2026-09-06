"""Every value written to a CSV is neutralised against formula injection.

A cell beginning with `=`, `+`, `-`, `@` or a control character is read as a
*formula* by Excel, LibreOffice and Google Sheets. Nothing in an export is safe
by being a number: a customer's own display name, a channel handle, a
department code all reach these cells verbatim, and a customer who calls
themselves `=cmd|'/C calc'!A0` turns the owner's export into code that runs on
the owner's machine when they open it. The customer needs no account to do
this -- they only have to message the business once.

Both exports that exist today neutralise it, prefixing a quote so the cell
reads as text. This file is the guard against the third export, added later,
that writes a row straight from the database. It is a source sweep rather than
a behaviour test because the failure is a `writerow` that never learned about
`_csv_cell`, and that is a fact about the code, not about any one response.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent

SEARCHED = ("backend", "channels", "core")


def _writerow_calls():
    for name in SEARCHED:
        for path in (ROOT / name).rglob("*.py"):
            source = path.read_text()

            if "csv" not in source:
                continue

            tree = ast.parse(source)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue

                if not isinstance(node.func, ast.Attribute):
                    continue

                if node.func.attr not in ("writerow", "writerows"):
                    continue

                yield path, node, ast.get_source_segment(source, node) or ""


def test_there_are_csv_writers_to_check():
    """A sweep that finds nothing passes without meaning anything."""
    assert list(_writerow_calls()), "no CSV writerow calls found to check"


def test_every_csv_row_is_written_through_the_formula_guard():
    unguarded = []

    for path, node, segment in _writerow_calls():
        # The neutraliser is named `_csv_cell` in both existing exports. A row
        # built without a call to it is a row written raw.
        if "_csv_cell" not in segment:
            unguarded.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert not unguarded, (
        "A CSV row is written without passing every cell through the "
        "formula-injection guard (`_csv_cell`):\n  "
        + "\n  ".join(unguarded)
        + "\n\nA customer's display name reaches these cells verbatim; "
        "`=cmd|'/C calc'!A0` as a name is code that runs when the owner opens "
        "the file. Wrap each cell in `_csv_cell`, as the analytics and "
        "conversation exports do."
    )


def test_the_guard_neutralises_the_dangerous_leaders():
    """The guard the sweep trusts, checked so the whole file rests on fact."""
    from backend.api.routes.analytics import _csv_cell as analytics_cell
    from backend.api.routes.conversations import _csv_cell as conversation_cell

    for cell in (analytics_cell, conversation_cell):
        for attack in ("=cmd|'/C calc'!A0", "+1+1", "-2+3", "@SUM(A1)", "\tx"):
            neutralised = cell(attack)

            assert neutralised.startswith("'"), (
                f"{cell.__module__}._csv_cell left {attack!r} dangerous"
            )

        # A plain value is untouched, so a report of names is still readable.
        assert cell("Lina Khoury") == "Lina Khoury"
        assert cell(42) == 42
