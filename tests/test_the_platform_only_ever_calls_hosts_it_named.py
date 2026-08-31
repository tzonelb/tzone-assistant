"""Every outbound request goes to a host written in the source, not one computed.

This platform holds, per company, a Meta page token, a WhatsApp token, a
Telegram bot token and an OpenAI key, and it attaches them to outbound calls.
An outbound URL that a request can steer is therefore not merely a request to
somewhere unexpected -- it is those credentials, sent there.

The property that prevents it is narrow and worth stating exactly: the *host*
of every outbound call is a literal or a configured constant. Path segments are
interpolated all over these modules -- a page id, a PSID, a comment id -- and
that is fine, because the authority part of a URL ends at the first `/` and
those segments all come after one. An id containing a slash can reach a
different path on `graph.facebook.com`; it cannot reach a different host.

What this rules out is the next module, the one that fetches a media URL a
webhook supplied, or a callback address a company typed into a settings screen.
Neither exists today. Both are one plausible feature away, and neither would
look wrong in review.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SEARCHED = ("backend", "channels", "core", "main.py")

# The objects an outbound call is made on. `session.get(...)` is excluded by
# construction: `session` here is a dict of conversation state, and including
# it produced six false positives that were dictionary lookups.
CLIENTS = {"httpx", "requests", "urllib", "client", "_client"}

METHODS = {"get", "post", "put", "patch", "delete", "request", "stream", "head"}

# A base URL that comes from deployment settings rather than from source. There
# is no literal to follow, so it is named here with the reason it is safe.
CONFIGURED_BASES = {
    "config.OPENAI_API_URL": (
        "The model endpoint, from the environment. Set by whoever runs the "
        "server; no request reaches it."
    ),
}


def _sources():
    for name in SEARCHED:
        path = ROOT / name

        if path.is_file():
            yield path
        else:
            yield from sorted(path.rglob("*.py"))


def _assigned(expression: str, *scopes) -> ast.AST | None:
    """The last value assigned to this name, searching each scope in turn.

    `expression` is either a bare name (`url`, `API_BASE`) or an attribute on
    self (`self.api_base`) -- between them they cover every indirection these
    modules use to reach a base URL.
    """
    for scope in scopes:
        found = None

        for node in ast.walk(scope):
            if not isinstance(node, ast.Assign):
                continue

            for target in node.targets:
                if ast.unparse(target) == expression:
                    found = node.value

        if found is not None:
            return found

    return None


def _leading_host(node: ast.AST, *scopes, depth: int = 0) -> str | None:
    """The literal text a URL expression begins with, following indirection.

    `httpx.post(url)` where `url = f"{API_BASE}/bot..."` and
    `API_BASE = "https://api.telegram.org"` has to arrive at the telegram host,
    or the check would report every real call and prove nothing. Bounded so a
    circular assignment cannot spin.
    """
    if depth > 4:
        return None

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value

    if isinstance(node, ast.JoinedStr) and node.values:
        head = node.values[0]

        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            if head.value:
                return head.value

            head = node.values[1] if len(node.values) > 1 else None

        if isinstance(head, ast.FormattedValue):
            resolved = _leading_host(head.value, *scopes, depth=depth + 1)

            if resolved is None:
                return None

            # `f"{API_BASE}/bot{token}"` where API_BASE is bare host text: the
            # host still ends before anything interpolated, but only because
            # the very next character in the f-string is the separator. Carry
            # the following literal so the caller can see that.
            following = node.values[node.values.index(head) + 1 :]

            if following and isinstance(following[0], ast.Constant):
                if isinstance(following[0].value, str):
                    return resolved + following[0].value

            return resolved

        return None

    if isinstance(node, (ast.Name, ast.Attribute)):
        assigned = _assigned(ast.unparse(node), *scopes)

        if assigned is not None:
            return _leading_host(assigned, *scopes, depth=depth + 1)

    return None


def _enclosing_function(tree: ast.AST, node: ast.AST):
    found = None

    for candidate in ast.walk(tree):
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = candidate.end_lineno or candidate.lineno

            if candidate.lineno <= node.lineno <= end:
                if found is None or candidate.lineno > found.lineno:
                    found = candidate

    return found


def _outbound_calls():
    for path in _sources():
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            if not isinstance(node.func, ast.Attribute):
                continue

            if node.func.attr not in METHODS or not node.args:
                continue

            base = node.func.value
            base_name = (
                base.id
                if isinstance(base, ast.Name)
                else base.attr if isinstance(base, ast.Attribute) else None
            )

            if base_name not in CLIENTS:
                continue

            yield path, node, tree


def test_the_sweep_still_finds_the_calls():
    found = list(_outbound_calls())

    assert len(found) >= 8, f"only {len(found)} outbound calls walked"


def test_every_outbound_url_starts_with_a_host_written_in_the_source():
    computed = []

    for path, node, tree in _outbound_calls():
        target = node.args[0]

        if ast.unparse(target) in CONFIGURED_BASES:
            continue

        function = _enclosing_function(tree, node)
        scopes = [scope for scope in (function, tree) if scope is not None]

        leading = _leading_host(target, *scopes)

        if leading is not None and leading.startswith(("http://", "https://")):
            # The host is settled before the first path separator, so the rest
            # may be interpolated freely.
            after_scheme = leading.split("//", 1)[1]

            if "/" in after_scheme:
                continue

        computed.append(
            f"{path.relative_to(ROOT)}:{node.lineno}  {ast.unparse(target)}"
        )

    assert not computed, (
        "An outbound request is sent to a URL this file cannot show is a "
        "constant host:\n  "
        + "\n  ".join(sorted(computed))
        + "\n\nThese calls carry a company's Meta, WhatsApp, Telegram or "
        "OpenAI credentials. A URL something else decides is those "
        "credentials, sent wherever it says. Build the request from a literal "
        "host or a configured base and interpolate only the path; if a "
        "deployment setting genuinely supplies the base, add it to "
        "CONFIGURED_BASES."
    )
