"""On a phone, the list and the thread share one screen -- so only one may show.

Below the breakpoint the conversation list is not a column beside the thread;
it is an overlay on top of it, covering the full width. Which of the two is
showing therefore has to be decided, and getting it wrong is not a cosmetic
fault: an open list over a thread swallows every tap meant for the conversation
underneath. The messages are visible through nothing -- they are simply not
there -- and every control on the thread, including the details drawer, is
inert. A browser driven at 390px could not click the customer's name at all;
the click timed out with the list's search box named as the element
intercepting it.

The first attempt at this closed the list inside the row's click handler, which
fixes exactly one of the three ways a phone arrives at a conversation. A
refresh, a shared link and the browser's back button all bypass it and land
with the list open over the thread again. So the property is not "closes on
tap" but "is derived from the route": a conversation in the URL means the
thread is showing, no conversation means the list is.

These read the source because the behaviour lives in a browser this suite does
not run. They are narrow on purpose -- each one names a way the defect came
back rather than describing the layout.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SCREEN = ROOT / "frontend/src/pages/conversations/ConversationsPageV2.jsx"

STYLES = ROOT / "frontend/src/pages/conversations/ConversationsPageV2.css"

DETAIL_STYLES = ROOT / "frontend/src/pages/conversations/ConversationDetailPageV2.css"


def test_the_breakpoint_in_the_screen_matches_the_one_in_the_stylesheet():
    """Two numbers that must agree, in two files that cannot check each other.

    The script decides when to hide the list; the stylesheet decides when the
    list becomes an overlay. If they disagree there is a band of widths where
    the list covers the thread and the script believes it does not.
    """
    match = re.search(r"const NARROW_SCREEN_PX = (\d+);", SCREEN.read_text())

    assert match, "NARROW_SCREEN_PX is no longer declared"

    breakpoint_px = match.group(1)

    assert f"max-width: {breakpoint_px}px" in STYLES.read_text(), (
        f"The script switches layouts at {breakpoint_px}px but no media query "
        "in ConversationsPageV2.css uses that width."
    )


def test_the_list_starts_closed_when_the_url_already_names_a_conversation():
    """A refresh and a shared link both land here, with no tap to react to."""
    source = SCREEN.read_text()

    match = re.search(
        r"const \[listOpen, setListOpen\] = useState\((.*?)\);",
        source,
        re.DOTALL,
    )

    assert match, "listOpen is no longer declared with useState"

    initial = match.group(1)

    assert "routeChannel" in initial and "routeUserId" in initial, (
        "listOpen's initial value ignores the route, so arriving straight at a "
        "conversation -- a refresh, a shared link, a bookmark -- opens the "
        "list on top of the thread and every tap on the conversation is "
        "swallowed by it. Derive the initial value from whether the URL names "
        "a conversation."
    )


def test_going_back_to_the_list_brings_the_list_back():
    """The back button changes the route without touching any handler."""
    source = SCREEN.read_text()

    effects = re.findall(
        r"useEffect\(\(\) => \{(.*?)\n  \}, \[(.*?)\]\);",
        source,
        re.DOTALL,
    )

    keyed_on_the_route = [
        body
        for body, deps in effects
        if "routeChannel" in deps and "routeUserId" in deps
    ]

    assert keyed_on_the_route, (
        "No effect re-derives the list's visibility when the route changes, so "
        "the browser's back button leaves the phone on a thread it has "
        "navigated away from, or on a list it has navigated into."
    )

    assert any("setListOpen" in body for body in keyed_on_the_route)


def test_the_drawers_cards_keep_their_own_height():
    """The details drawer is a flex column, where children shrink by default.

    With `overflow: hidden` on the card that does not produce a scrollbar --
    it clips inside each card, so six cards in a short drawer each lose the
    bottom of their own subtitle and the stack reads as cards printed on top of
    one another. The drawer body already scrolls; it only ever got the chance
    once the cards stopped shrinking.
    """
    source = DETAIL_STYLES.read_text()

    body = re.search(r"\.tzv2-cd-drawer-body \{(.*?)\}", source, re.DOTALL)
    card = re.search(r"\.tzv2-cd-acc \{(.*?)\}", source, re.DOTALL)

    assert body and card

    assert "overflow-y: auto" in body.group(1)
    assert "flex: none" in card.group(1), (
        "The drawer's cards shrink to fit instead of overflowing into the "
        "scroll the drawer body already provides, and `overflow: hidden` then "
        "clips each card's own text."
    )
