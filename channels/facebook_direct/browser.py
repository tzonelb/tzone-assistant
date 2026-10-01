"""Everything this channel does through a real browser, and the one place
that does it.

Facebook has no unofficial-API equivalent of `instagrapi`: there is no
maintained library that reimplements a private endpoint this could call
directly, because Facebook's web client does not expose one the way
Instagram's mobile app does -- what it exposes is HTML, to a browser. So
this reads the same HTML a signed-in person would see, through a real,
headless Chromium (Playwright), with the operator's own exported cookies
loaded into it rather than a fresh login.

`mbasic.facebook.com` -- not the main site -- is the surface this targets.
It is Facebook's own long-standing low-bandwidth interface: server-rendered
HTML with none of the heavy client-side rendering the main site does, which
is both closer to parseable and, because it changes far less often, the
closer thing to a *stable* surface an unofficial reader can build against.
It is still Facebook's own markup, not a documented API contract, so it can
change without notice -- see `_extract_comments` below for where that risk
is concentrated and isolated.

### What is verified here, and what is not

Every function in this module has been exercised against the HTML shapes
its own tests construct -- the parsing logic is real and defensively
written (a block it cannot make sense of is skipped, never a crash). What
has not been exercised is a live Facebook session, because this platform's
own development and test environment has no path to one. The first company
that connects a real Page is this channel's real integration test; if
Facebook's markup has moved in some way these functions do not anticipate,
`poll_account` in `channels/facebook_direct/poller.py` logs a warning and
finds nothing that sweep rather than raising, so a markup change degrades
to "no new comments this sweep" instead of a broken connection an operator
has to notice and reconnect.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any

from playwright.sync_api import sync_playwright


logger = logging.getLogger(__name__)

MBASIC_BASE = "https://mbasic.facebook.com"

NAVIGATE_TIMEOUT_MS = 20000

# A plain, real-looking desktop user agent. mbasic serves the same simplified
# markup to any client; what actually matters is that the request looks like
# an ordinary browser's, not a script's.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class FacebookSessionError(RuntimeError):
    """The cookies could not be used to read Facebook -- expired, wrong
    account, wrong Page, or Facebook itself unreachable."""


def _looks_like_login_page(html: str, final_url: str) -> bool:
    """Whether the response is Facebook's own sign-in page rather than the
    Page this was supposed to load.

    Cookies going stale is the expected, routine failure mode here -- the
    same "the session eventually needs replacing" shape every cookie- or
    session-based channel on this platform has -- so this is checked after
    every navigation, not assumed to be a one-time problem the connect step
    alone can rule out.
    """
    if "login" in final_url.lower():
        return True

    lowered = html.lower()
    return 'name="login"' in lowered or "id=\"login_form\"" in lowered


def _page_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)

    if not match:
        return None

    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title or None


def _fetch(context: Any, path: str) -> tuple[str, str]:
    """Load one mbasic path in this session and return ``(html, final_url)``."""
    page = context.new_page()

    try:
        page.goto(
            f"{MBASIC_BASE}{path}",
            timeout=NAVIGATE_TIMEOUT_MS,
            wait_until="domcontentloaded",
        )
        return page.content(), page.url
    finally:
        page.close()


def validate_session_and_fetch_page_name(
    cookies: list[dict[str, Any]], page_id: str
) -> str:
    """Confirm these cookies can see this Page, and return its display name.

    Called once, at connect time -- see `backend/api/routes/facebook_direct.py`.
    Raises :class:`FacebookSessionError` rather than returning something
    unusable, so a company can never connect a Page it cannot actually read.
    """
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            try:
                context = browser.new_context(user_agent=USER_AGENT)
                context.add_cookies(cookies)

                html, final_url = _fetch(context, f"/{page_id}")

                if _looks_like_login_page(html, final_url):
                    raise FacebookSessionError(
                        "Facebook did not accept these cookies -- they may "
                        "already be expired. Export a fresh set from a "
                        "browser that is currently signed in to Facebook."
                    )

                title = _page_title(html)

                if not title:
                    raise FacebookSessionError(
                        "Could not read that Page. Check the Page id, and "
                        "that this Facebook account manages it."
                    )

                return title
            finally:
                browser.close()
    except FacebookSessionError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Facebook cookie validation failed: %s", exc)
        raise FacebookSessionError(
            "Could not reach Facebook to check these cookies. Please try again."
        ) from exc


# ------------------------------------------------------------------ reading


class _TextExtractor(HTMLParser):
    """The plain text of one HTML fragment, tags stripped.

    Used on a bounded slice around one comment's markup, not a whole page --
    good enough to turn "<span>hello <b>there</b></span>" into "hello
    there" without pulling in a full HTML/XML dependency for it.
    """

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._parts)).strip()


def _plain_text(fragment: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(fragment)
    return extractor.text()


# The one stable thread through years of Facebook's own markup changes,
# mbasic included: a comment's own permalink/timestamp link always carries
# `comment_id=<digits>` in its href, because that id is also what a reply
# posts back against. Everything else this module infers is positioned
# relative to that anchor rather than matched by a fixed template, since the
# surrounding tags are exactly the part most likely to have moved.
_COMMENT_ID_RE = re.compile(r'comment_id=(\d+)')
_PROFILE_HREF_RE = re.compile(r'href="(/profile\.php\?id=(\d+)[^"]*|/[A-Za-z0-9\.\-_]+)"')
_ANCHOR_RE = re.compile(r"<a\b[^>]*>.*?</a>", re.IGNORECASE | re.DOTALL)


def _extract_comments(html: str, *, post_id: str) -> list[dict[str, Any]]:
    """Every comment this page's markup can be made to yield.

    Deliberately permissive rather than a strict template match. A comment's
    own markup, in this reading, is: an author's profile link, then the
    comment's own text, then the `comment_id=` permalink/timestamp link --
    so each `comment_id=` match anchors one comment, the nearest anchor
    *before* it in a bounded window is taken as the author (only if it is
    itself a profile-shaped link, not "Like" or "Reply"), and the plain text
    strictly between the two is the message. A block that does not fit this
    shape -- no author link found, or nothing but whitespace between the
    two -- is skipped, not raised: see this module's own docstring on why
    silence beats a crash here.
    """
    comments: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for match in _COMMENT_ID_RE.finditer(html):
        comment_id = match.group(1)

        if comment_id in seen_ids:
            continue

        window_start = max(0, match.start() - 600)
        window_end = min(len(html), match.end() + 100)
        window = html[window_start:window_end]

        anchors = list(_ANCHOR_RE.finditer(window))

        if not anchors:
            continue

        # The anchor containing `comment_id=` itself, within this window.
        own_offset = match.start() - window_start
        own_anchor = next(
            (a for a in anchors if a.start() <= own_offset <= a.end()), None
        )

        if own_anchor is None:
            continue

        preceding = [a for a in anchors if a.end() <= own_anchor.start()]

        if not preceding:
            continue

        author_anchor = preceding[-1]
        profile_match = _PROFILE_HREF_RE.search(author_anchor.group(0))

        if not profile_match:
            continue

        author_name = _plain_text(author_anchor.group(0)) or None
        author_id = profile_match.group(2) or profile_match.group(1)

        message = _plain_text(window[author_anchor.end() : own_anchor.start()])

        if not message:
            continue

        seen_ids.add(comment_id)
        comments.append(
            {
                "provider_comment_id": comment_id,
                "post_id": post_id,
                "author_name": author_name,
                "author_external_id": author_id,
                "message": message,
            }
        )

    return comments


_POST_ID_RE = re.compile(r'/story\.php\?story_fbid=(\d+)')


def _extract_post_links(html: str, *, page_id: str) -> list[str]:
    """Every post permalink path this Page's own listing offers."""
    paths: list[str] = []
    seen: set[str] = set()

    for match in _POST_ID_RE.finditer(html):
        story_fbid = match.group(1)

        if story_fbid in seen:
            continue

        seen.add(story_fbid)
        paths.append(f"/story.php?story_fbid={story_fbid}&id={page_id}")

    return paths


def read_page_comments(
    cookies: list[dict[str, Any]], page_id: str, *, max_posts: int
) -> list[dict[str, Any]]:
    """Every comment findable on this Page's most recent posts, this sweep.

    One browser session covers the whole sweep -- launching Chromium is the
    expensive part, not loading one more mbasic path in an already-open
    context. Raises :class:`FacebookSessionError` only when the session
    itself looks unusable (expired cookies, Page unreachable); a post whose
    own markup does not parse is skipped rather than failing the sweep.
    """
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            try:
                context = browser.new_context(user_agent=USER_AGENT)
                context.add_cookies(cookies)

                listing_html, final_url = _fetch(context, f"/{page_id}")

                if _looks_like_login_page(listing_html, final_url):
                    raise FacebookSessionError(
                        "Facebook's session for this Page has expired. "
                        "Reconnect with a fresh cookie export."
                    )

                post_paths = _extract_post_links(listing_html, page_id=page_id)[
                    :max_posts
                ]

                comments: list[dict[str, Any]] = []

                for post_path in post_paths:
                    post_id_match = _POST_ID_RE.search(post_path)
                    post_id = post_id_match.group(1) if post_id_match else post_path

                    try:
                        post_html, post_url = _fetch(context, post_path)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "Could not load a Facebook post for Page %s", page_id
                        )
                        continue

                    if _looks_like_login_page(post_html, post_url):
                        raise FacebookSessionError(
                            "Facebook's session for this Page has expired. "
                            "Reconnect with a fresh cookie export."
                        )

                    comments.extend(
                        _extract_comments(post_html, post_id=post_id)
                    )

                return comments
            finally:
                browser.close()
    except FacebookSessionError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reading Facebook comments failed for Page %s: %s", page_id, exc)
        return []
