"""Everything this channel does through a real browser, and the one place
that does it.

WhatsApp's unofficial-automation ecosystem has no Python equivalent of
`instagrapi`: the mature libraries that reimplement WhatsApp's own
multi-device protocol (`whatsmeow`, the one this platform's own earlier
research found and rejected -- see the catalogue's own note on why WhatsApp
QR was nearly cancelled outright) are written in Go, not Python, and there
is nothing comparably maintained on the Python side. What this drives
instead is the same web client a person uses: `web.whatsapp.com`, through a
real, headless Chromium (Playwright), the same tool `channels/
facebook_direct/browser.py` uses for the same reason.

### Why this one is riskier than Facebook's reader

`channels/facebook_direct/browser.py` targets `mbasic.facebook.com`
deliberately -- Facebook's own long-stable, low-bandwidth, mostly
server-rendered surface. WhatsApp Web has no equivalent: it is one heavy
client-side React application, its DOM structure is not documented
anywhere WhatsApp itself controls, and it changes across WhatsApp's own
releases without notice. Every selector below is written against
role/label-based locators (Playwright's `get_by_role`, ARIA attributes)
rather than CSS class names, because ARIA structure tends to survive a
visual redesign that would break a class-name selector outright -- but
that is a mitigation, not a guarantee. None of it has been exercised
against a live WhatsApp Web session: this platform's own environment has
no path to one, the same limitation `facebook_direct/browser.py` documents
for itself. Every reading function here fails soft -- returns nothing
rather than raising -- for exactly that reason.

### Why the login flow looks different from every other channel here

Scanning a QR code is not a request this platform can answer inside one
HTTP call: the phone has to actually scan it, which takes real human time.
So the browser session that shows the QR code is handed to a background
thread (`start_connect` below) that owns it for as long as the scan takes,
polled by `backend/api/routes/whatsapp_qr.py`'s own `connect/status`
endpoint rather than blocking a request thread for however long a person
takes to pick up their phone.
"""

from __future__ import annotations

import base64
import logging
import re
import threading
import time
from typing import Any

from playwright.sync_api import sync_playwright


logger = logging.getLogger(__name__)

WA_URL = "https://web.whatsapp.com"

# A plain, real-looking desktop user agent -- the same reasoning
# `facebook_direct/browser.py` gives for its own.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

NAVIGATE_TIMEOUT_MS = 30000
QR_WAIT_TIMEOUT_MS = 20000

# How long a person has, after the QR code is shown, to actually scan it
# before the connection attempt gives up. WhatsApp Web itself refreshes the
# QR code every ~20-60 seconds on its own; this is generous room for a
# person to find their phone and open the camera, not a bet that one QR
# image stays valid the whole time.
CONNECT_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2


class WhatsAppSessionError(RuntimeError):
    """The browser session could not reach or use WhatsApp Web -- expired,
    logged out, or WhatsApp itself unreachable."""


class PendingConnection:
    """One in-progress QR login, owned by exactly one background thread for
    its whole lifetime. `backend/api/routes/whatsapp_qr.py` only ever reads
    this under `lock`; only `_run` (this module) ever writes to it.

    `status` moves forward only: starting -> qr_ready -> connected, or to
    failed/expired from either of the first two. Never backward.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = "starting"
        self.qr_png_base64: str | None = None
        self.error: str | None = None
        self.storage_state: dict[str, Any] | None = None
        self.phone_number: str | None = None
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()


def _looks_logged_in(page: Any) -> bool:
    """Whether this page has moved past the QR screen into the chat list.

    The chat list's own search box is the marker: WhatsApp Web has kept a
    `role="textbox"` labelled for search on every redesign this platform's
    own research found screenshots of, and it exists only once a session is
    live -- the QR screen has no search box at all.
    """
    try:
        return page.get_by_role("textbox").filter(has_text=re.compile("")).count() > 0 and (
            page.locator('div[aria-label], div[data-testid="chat-list"]').count() > 0
        )
    except Exception:  # noqa: BLE001
        return False


def _extract_qr_png(page: Any) -> bytes | None:
    """A screenshot of just the QR code element, not the whole page.

    WhatsApp Web renders the QR as a `<canvas>` inside the landing screen.
    Screenshotting the element rather than reading its pixel data keeps
    this from caring whether it is drawn on a canvas, an svg, or an img --
    whichever it is, on whatever redesign, a screenshot of it is a scannable
    image either way.
    """
    try:
        canvas = page.locator("canvas").first
        canvas.wait_for(state="visible", timeout=QR_WAIT_TIMEOUT_MS)
        return canvas.screenshot()
    except Exception:  # noqa: BLE001
        return None


_PHONE_PATTERN = re.compile(r"\+\d[\d\s\-()]{6,}\d")


def _extract_phone_number(page: Any) -> str | None:
    """The WhatsApp account's own phone number, read from its session.

    Tried in order, most reliable first:

    1. `localStorage`'s own record of the signed-in account's id
       (`last-wid-md`, the key WhatsApp Web's multi-device client has used
       to persist which account a browser is linked to) -- not documented
       anywhere WhatsApp controls, so this is read defensively and never
       trusted to still be the key in use by the time this runs live.
    2. The account's own profile panel, which shows the number as
       human-readable text -- a plain regex over whatever text is visible
       once that panel is opened.

    Returns ``None`` rather than guessing when neither works; the caller
    treats a missing number as a hard stop, never a fabricated one -- a
    wrong routing id would let two companies collide on the same number.
    """
    try:
        wid = page.evaluate(
            "() => { "
            "for (const key of Object.keys(window.localStorage)) { "
            "  if (key.toLowerCase().includes('wid')) { "
            "    const value = window.localStorage.getItem(key); "
            "    if (value && value.includes('@')) return value; "
            "  } "
            "} "
            "return null; }"
        )

        if wid:
            match = re.search(r"(\d{6,})@", str(wid))

            if match:
                return match.group(1)
    except Exception:  # noqa: BLE001
        pass

    try:
        page.get_by_role("button", name=re.compile("profile|account", re.I)).first.click(
            timeout=5000
        )
        text = page.locator("body").inner_text(timeout=5000)
        match = _PHONE_PATTERN.search(text)

        if match:
            return re.sub(r"[\s\-()]", "", match.group(0))
    except Exception:  # noqa: BLE001
        pass

    return None


def _run(pending: PendingConnection) -> None:
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            try:
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()
                page.goto(WA_URL, timeout=NAVIGATE_TIMEOUT_MS, wait_until="domcontentloaded")

                png = _extract_qr_png(page)

                if not png:
                    with pending.lock:
                        pending.status = "failed"
                        pending.error = (
                            "Could not load the WhatsApp Web QR code. Please try again."
                        )
                    return

                with pending.lock:
                    pending.qr_png_base64 = base64.b64encode(png).decode()
                    pending.status = "qr_ready"

                deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS

                while time.monotonic() < deadline:
                    if pending._stop.is_set():
                        return

                    if _looks_logged_in(page):
                        phone_number = _extract_phone_number(page)

                        if not phone_number:
                            with pending.lock:
                                pending.status = "failed"
                                pending.error = (
                                    "Signed in, but this account's own phone "
                                    "number could not be read. Please try again."
                                )
                            return

                        state = context.storage_state()

                        with pending.lock:
                            pending.storage_state = state
                            pending.phone_number = phone_number
                            pending.status = "connected"

                        return

                    time.sleep(POLL_INTERVAL_SECONDS)

                with pending.lock:
                    if pending.status == "qr_ready":
                        pending.status = "expired"
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("WhatsApp QR connection failed: %s", exc)

        with pending.lock:
            pending.status = "failed"
            pending.error = "Could not reach WhatsApp Web. Please try again."


def start_connect() -> PendingConnection:
    """Begin one QR login in a dedicated background thread and return
    immediately -- the thread, not this call, owns the browser for the rest
    of this connection's life. See `PendingConnection`'s own docstring."""
    pending = PendingConnection()
    threading.Thread(target=_run, args=(pending,), daemon=True).start()
    return pending


# ------------------------------------------------------------------ an
# already-connected session


def _open_session(storage_state: dict[str, Any]):
    """A context manager-shaped pair: call, use `.page`, then `.close()`.

    Not a `with`-block itself, because the poller needs to keep the page
    open across several reads (each chat it opens) within one sweep --
    reusing `facebook_direct`'s one-browser-per-sweep economy, not
    `instagram_direct`'s hand this expects the caller to close explicitly.
    """

    class _Session:
        def __init__(self) -> None:
            self._playwright = sync_playwright().start()
            self.browser = self._playwright.chromium.launch(headless=True)
            self.context = self.browser.new_context(
                storage_state=storage_state, user_agent=USER_AGENT
            )
            self.page = self.context.new_page()

        def close(self) -> None:
            try:
                self.browser.close()
            finally:
                self._playwright.stop()

    return _Session()


def open_authenticated_page(storage_state: dict[str, Any]):
    """Load WhatsApp Web with an already-scanned-in session. Raises
    :class:`WhatsAppSessionError` if the session no longer works."""
    session = _open_session(storage_state)

    try:
        session.page.goto(WA_URL, timeout=NAVIGATE_TIMEOUT_MS, wait_until="domcontentloaded")

        if not _looks_logged_in(session.page):
            session.close()
            raise WhatsAppSessionError(
                "This WhatsApp session has expired. It needs to be reconnected."
            )
    except WhatsAppSessionError:
        raise
    except Exception as exc:  # noqa: BLE001
        session.close()
        raise WhatsAppSessionError(
            "Could not reach WhatsApp Web with this session."
        ) from exc

    return session


_CHAT_ID_RE = re.compile(r"(\d{6,})@")


def read_unread_chats(session: Any, *, max_chats: int) -> list[dict[str, Any]]:
    """Every chat this sweep found unread, each with the messages currently
    visible in it.

    Deliberately reads only chats WhatsApp Web itself already marked
    unread, rather than sweeping every chat in the list: it is the one
    signal this client did not have to infer, and it keeps a sweep's cost
    proportional to how much actually happened since the last one rather
    than to how many conversations this account has ever had.

    Best-effort, like every reading function in this module -- a chat this
    cannot make sense of is skipped, not raised past.
    """
    page = session.page
    chats: list[dict[str, Any]] = []

    try:
        rows = page.locator('[aria-label*="unread" i]').all()
    except Exception:  # noqa: BLE001
        return []

    for row in rows[:max_chats]:
        try:
            row.click(timeout=5000)
        except Exception:  # noqa: BLE001
            continue

        chat_name = None
        chat_id = None

        try:
            chat_name = page.locator("header").first.inner_text(timeout=3000).strip() or None
        except Exception:  # noqa: BLE001
            pass

        try:
            data_id = page.evaluate(
                "() => { "
                "const el = document.querySelector('[data-id*=\"@c.us\"], [data-id*=\"@s.whatsapp.net\"]'); "
                "return el ? el.getAttribute('data-id') : null; }"
            )

            if data_id:
                match = _CHAT_ID_RE.search(str(data_id))

                if match:
                    chat_id = match.group(1)
        except Exception:  # noqa: BLE001
            pass

        if not chat_id:
            # Nothing this platform can route a reply to safely -- skip
            # rather than invent an id that could collide with another chat.
            continue

        messages = _read_visible_messages(page)

        chats.append(
            {"chat_id": chat_id, "chat_name": chat_name, "messages": messages}
        )

    return chats


def _read_visible_messages(page: Any) -> list[dict[str, Any]]:
    """Every message bubble currently rendered in the open chat, oldest
    first.

    `message-out` has been WhatsApp Web's own class name for a bubble this
    account sent, across enough of its history that scraping tools built
    against it kept working release over release -- used here as the one
    signal for `is_outgoing`, defensively: a bubble this cannot classify is
    marked outgoing rather than incoming, so a markup change this platform
    cannot see coming degrades to "quieter than it should be", never to
    replaying this account's own sent messages back to itself as if a
    customer had sent them.
    """
    try:
        bubbles = page.locator(
            'div.message-in, div.message-out, div[class*="message-in"], div[class*="message-out"]'
        ).all()
    except Exception:  # noqa: BLE001
        return []

    messages: list[dict[str, Any]] = []

    for bubble in bubbles:
        try:
            class_name = bubble.get_attribute("class") or ""
            is_outgoing = "message-out" in class_name or "message-in" not in class_name
            text = bubble.inner_text(timeout=2000).strip()
        except Exception:  # noqa: BLE001
            continue

        if not text:
            continue

        messages.append({"text": text, "is_outgoing": is_outgoing})

    return messages


def send_text_message(session: Any, chat_id: str, text: str) -> bool:
    """Open a chat by its WhatsApp id and send one message. Returns whether
    it was typed and sent; never raises for a chat this cannot find."""
    page = session.page

    try:
        page.goto(
            f"{WA_URL}/send?phone={chat_id}",
            timeout=NAVIGATE_TIMEOUT_MS,
            wait_until="domcontentloaded",
        )
        box = page.get_by_role("textbox", name=re.compile("type a message", re.I))
        box.wait_for(state="visible", timeout=15000)
        box.click()
        box.fill(text)
        box.press("Enter")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sending a WhatsApp (QR) message failed: %s", exc)
        return False
