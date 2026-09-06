"""A tile reading a key the API does not answer shows a confident zero.

`counts[key] ?? 0` cannot tell "nothing to count" from "nobody counted". So a
stat card whose key the endpoint never fills renders 0 -- not blank, not an
error, a number, on the first screen after signing in, for every company,
however full the thing it claims to be counting.

That is what "Products" did. The tile has been in `STAT_CARDS` since the
dashboard was redesigned and `GET /api/dashboard/summary` has never contained
the word `products`; the encrypted file has had a `products` table the whole
time. A business with a hundred products was told it had none, and told it
every single day.

This is the same defect this platform has already found twice on the reporting
side -- a counter watching an event name nothing writes -- arriving from the
other direction, so it gets the same treatment: a sweep, not a fix.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SCREEN = ROOT / "frontend/src/pages/dashboard/DashboardPageV2.jsx"

ROUTE = ROOT / "backend/api/routes/dashboard.py"


def _keys_the_screen_reads() -> set[str]:
    """Every `counts.<key>` the dashboard puts on screen.

    Both shapes the screen uses: the tile's own key, and the ones its
    description line interpolates (`${c.open_conversations} open now`).
    """
    source = SCREEN.read_text()

    cards = re.search(r"const STAT_CARDS = \[(.*?)\n\];", source, re.DOTALL)

    assert cards, "STAT_CARDS is no longer declared"

    block = cards.group(1)

    keys = set(re.findall(r'\[\s*"([a-z_]+)"', block))
    keys |= set(re.findall(r"c\.([a-z_]+)", block))

    return keys


def _keys_the_api_answers() -> set[str]:
    source = ROUTE.read_text()

    # `counts["x"] = ...` and the `{"x": "SELECT ..."}` table it loops over.
    assigned = set(re.findall(r'counts\["([a-z_]+)"\]', source))
    queried = set(
        re.findall(r'"([a-z_]+)":\s*\(?\s*\n?\s*"SELECT COUNT', source)
    )

    return assigned | queried


def test_the_sweep_finds_both_sides():
    """Two regexes that would each pass by matching nothing."""
    screen = _keys_the_screen_reads()
    api = _keys_the_api_answers()

    assert len(screen) >= 6, f"only parsed {sorted(screen)} from the screen"
    assert len(api) >= 8, f"only parsed {sorted(api)} from the endpoint"


def test_every_number_the_dashboard_shows_is_one_the_endpoint_fills():
    unanswered = sorted(_keys_the_screen_reads() - _keys_the_api_answers())

    assert not unanswered, (
        "The dashboard renders a number the API never sends, so it shows 0 "
        "whatever the truth is: "
        + ", ".join(unanswered)
        + "\n\nEither count it in GET /api/dashboard/summary or take the tile "
        "off the screen. A tile that always reads 0 is worse than no tile, "
        "because 0 is an answer."
    )
