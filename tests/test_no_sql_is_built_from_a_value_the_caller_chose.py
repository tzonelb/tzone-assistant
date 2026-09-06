"""SQL is built from constants; values always travel as parameters.

Seventy-nine statements in this codebase are f-strings. That is normal and not
in itself a defect: SQLite will not accept a placeholder where a table name, a
column name or a whole `WHERE` clause belongs, so those have to be interpolated
and every one of them is assembled from module-level constants.

The defect this guards is one interpolation away from any of them. The moment a
value that reached the process from a request is formatted into the statement
instead of passed as a parameter, the encrypted-file-per-tenant design stops
mattering: whatever the query was scoped to, the caller can rewrite it.

Reading for that does not scale and does not last -- the sites are spread across
twenty modules and the safe ones look exactly like the unsafe one would. So the
check is mechanical, and it is deliberately blunt: an interpolated expression
may not mention any parameter of the function it sits in. A parameter is the
only way a request-borne value gets this deep, and a constant never needs one.
Interpolating `f"... {table} ..."` where `table` came from a module dict is
untouched by this; interpolating a column name a caller sent is not.

Adding a statement that formats a parameter into SQL is not forbidden. Adding
one without recording why is.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

# Application code. `tests/` is excluded: a test that builds a query from its
# own fixture argument is not a way in for anybody.
SEARCHED = ("backend", "channels", "core", "database", "tools", "main.py")

EXECUTORS = {"execute", "executemany", "executescript"}

ALLOWED: dict[str, str] = {
    "database/manager.py:_open:keyring.sqlcipher_key_literal(key)": (
        "SQLCipher's `PRAGMA key` takes no placeholder -- the key has to be a "
        "literal in the statement, which is why this one exists at all. It is "
        "safe by construction rather than by convention: "
        "`keyring.sqlcipher_key_literal` refuses anything that is not exactly "
        "KEY_BYTES of raw key material and renders it as x'<hex>', and hex "
        "digits cannot close the quote. The key is derived from the company's "
        "wrapped DEK and never travels in a request."
    ),
}


def _enclosing_function(tree: ast.AST, node: ast.AST):
    """The innermost def containing `node`."""
    found = None

    for candidate in ast.walk(tree):
        if not isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        end = candidate.end_lineno or candidate.lineno

        if candidate.lineno <= node.lineno <= end:
            if found is None or candidate.lineno > found.lineno:
                found = candidate

    return found


def _sources():
    for name in SEARCHED:
        path = ROOT / name

        if path.is_file():
            yield path
            continue

        yield from sorted(path.rglob("*.py"))


def _interpolated_sql():
    """Every f-string handed to execute(), with the names it interpolates."""
    for path in _sources():
        try:
            source = path.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            if not isinstance(node.func, ast.Attribute):
                continue

            if node.func.attr not in EXECUTORS or not node.args:
                continue

            statement = node.args[0]

            if not isinstance(statement, ast.JoinedStr):
                continue

            function = _enclosing_function(tree, node)

            yield path, node, statement, function


def test_the_sweep_still_finds_the_statements():
    """A walk that quietly matches nothing would pass every assertion below."""
    found = list(_interpolated_sql())

    assert len(found) > 40, f"only {len(found)} interpolated statements walked"


def test_no_statement_formats_a_value_its_caller_chose():
    unrecorded = []

    for path, node, statement, function in _interpolated_sql():
        if function is None:
            continue

        arguments = function.args
        parameters = {
            argument.arg
            for argument in (
                arguments.posonlyargs + arguments.args + arguments.kwonlyargs
            )
        }

        if arguments.vararg:
            parameters.add(arguments.vararg.arg)

        if arguments.kwarg:
            parameters.add(arguments.kwarg.arg)

        for piece in statement.values:
            if not isinstance(piece, ast.FormattedValue):
                continue

            mentioned = {
                name.id
                for name in ast.walk(piece.value)
                if isinstance(name, ast.Name)
            }

            if not mentioned & parameters:
                continue

            expression = ast.unparse(piece.value)
            key = (
                f"{path.relative_to(ROOT)}:{function.name}:{expression}"
            )

            if key not in ALLOWED:
                unrecorded.append(f"{key}  (line {node.lineno})")

    assert not unrecorded, (
        "SQL is being built from a value that reached this function as an "
        "argument:\n  "
        + "\n  ".join(sorted(unrecorded))
        + "\n\nA parameter is how a request-borne value gets this deep. Pass it "
        "as a query parameter (`?`) instead; if the statement genuinely needs "
        "an identifier there, resolve it against a module-level constant "
        "first, or record it in ALLOWED with the reason it cannot be reached."
    )


def test_the_one_recorded_exception_is_still_safe_by_construction():
    """The allowance above rests on this, so it is checked rather than trusted."""
    from backend.security.keyring import KEY_BYTES, KeyringError, sqlcipher_key_literal

    import pytest

    rendered = sqlcipher_key_literal(b"\xab" * KEY_BYTES)

    assert rendered == "x'" + ("ab" * KEY_BYTES) + "'"

    # Only hex digits reach the statement, so nothing can close the quote.
    assert set(rendered[2:-1]) <= set("0123456789abcdef")

    for wrong in (b"", b"\x00" * (KEY_BYTES - 1), b"\x00" * (KEY_BYTES + 1)):
        with pytest.raises(KeyringError):
            sqlcipher_key_literal(wrong)
